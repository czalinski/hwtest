//! `hwtest-import-csv`: imports CSV telemetry (written by the CSV logger,
//! e.g. while offsite) into `InfluxDB`.
//!
//! ```text
//! hwtest-import-csv ./logs --url http://influx:8086 --token $INFLUXDB_TOKEN
//! hwtest-import-csv ./logs/hass/echo/run42 --dry-run
//! ```

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use clap::Parser;
use hwtest_logger::line_protocol::{point, FieldValue};
use hwtest_logger::InfluxWriter;

#[derive(Parser)]
#[command(about = "Import CSV telemetry into InfluxDB")]
struct Args {
    /// A test run directory (containing metadata.json) or a root containing many.
    path: PathBuf,
    #[arg(long, env = "INFLUXDB_URL", default_value = "http://localhost:8086")]
    url: String,
    #[arg(long, env = "INFLUXDB_ORG", default_value = "hwtest")]
    org: String,
    #[arg(long, env = "INFLUXDB_BUCKET", default_value = "telemetry")]
    bucket: String,
    #[arg(long, env = "INFLUXDB_TOKEN")]
    token: Option<String>,
    #[arg(long, default_value = "telemetry")]
    measurement: String,
    #[arg(long, default_value_t = 1000)]
    batch_size: usize,
    /// Parse everything but do not write.
    #[arg(long)]
    dry_run: bool,
}

fn find_runs(root: &Path, out: &mut Vec<PathBuf>) {
    if root.join("metadata.json").is_file() {
        out.push(root.to_path_buf());
    }
    if let Ok(entries) = std::fs::read_dir(root) {
        let mut dirs: Vec<PathBuf> =
            entries.flatten().map(|e| e.path()).filter(|p| p.is_dir()).collect();
        dirs.sort();
        for d in dirs {
            find_runs(&d, out);
        }
    }
}

fn import_run(
    dir: &Path,
    args: &Args,
    writer: Option<&InfluxWriter>,
) -> Result<(usize, usize), String> {
    let meta: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(dir.join("metadata.json"))
            .map_err(|e| format!("metadata.json: {e}"))?,
    )
    .map_err(|e| format!("metadata.json: {e}"))?;
    let tags: Vec<(String, String)> = meta
        .as_object()
        .into_iter()
        .flatten()
        .filter(|(k, _)| *k != "topics" && *k != "schemas")
        .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
        .collect();
    let topics: Vec<String> = meta["topics"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|t| t.as_str().map(String::from))
        .collect();
    if topics.is_empty() {
        return Err("no topics in metadata.json".into());
    }
    let (mut n_topics, mut n_points) = (0, 0);
    for topic in &topics {
        let file = dir.join(format!("{}.csv", topic.replace(['/', '.'], "_")));
        let mut reader =
            csv::Reader::from_path(&file).map_err(|e| format!("{}: {e}", file.display()))?;
        let headers = reader.headers().map_err(|e| e.to_string())?.clone();
        let mut batch = Vec::new();
        for (row_no, row) in reader.records().enumerate() {
            let row = row.map_err(|e| format!("{}: {e}", file.display()))?;
            let ts: i128 = row
                .get(0)
                .and_then(|t| t.parse().ok())
                .ok_or_else(|| format!("{}: row {}: bad timestamp", file.display(), row_no + 2))?;
            let fields = headers.iter().zip(row.iter()).skip(1).map(|(k, v)| {
                (
                    k,
                    v.parse::<f64>()
                        .map_or_else(|_| FieldValue::Str(v.to_string()), FieldValue::Float),
                )
            });
            let tag_iter = std::iter::once(("topic", topic.as_str()))
                .chain(tags.iter().map(|(k, v)| (k.as_str(), v.as_str())));
            if let Some(line) = point(&args.measurement, tag_iter, fields, ts) {
                batch.push(line);
            }
            if batch.len() >= args.batch_size {
                if let Some(w) = writer {
                    w.write(&batch).map_err(|e| e.to_string())?;
                }
                n_points += batch.len();
                batch.clear();
            }
        }
        if let Some(w) = writer {
            w.write(&batch).map_err(|e| e.to_string())?;
        }
        n_points += batch.len();
        n_topics += 1;
    }
    Ok((n_topics, n_points))
}

fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();
    let args = Args::parse();
    let writer = if args.dry_run {
        None
    } else {
        let Some(token) = args.token.clone() else {
            eprintln!("error: --token is required (or set INFLUXDB_TOKEN)");
            return ExitCode::from(2);
        };
        Some(InfluxWriter::new(&args.url, &args.org, &args.bucket, &token))
    };
    let mut runs = Vec::new();
    find_runs(&args.path, &mut runs);
    if runs.is_empty() {
        eprintln!("error: no test runs (metadata.json) under {}", args.path.display());
        return ExitCode::FAILURE;
    }
    let (mut ok, mut total) = (0, 0);
    for run in &runs {
        match import_run(run, &args, writer.as_ref()) {
            Ok((topics, points)) => {
                println!("{}: {topics} topic(s), {points} point(s)", run.display());
                ok += 1;
                total += points;
            }
            Err(e) => println!("{}: FAILED: {e}", run.display()),
        }
    }
    println!(
        "Imported {ok}/{} test run(s), {total} point(s){}",
        runs.len(),
        if args.dry_run { " (dry run)" } else { "" }
    );
    if ok == runs.len() {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}
