//! `hwtest-runner`: test station runner (web UI for operators).
//!
//! ```text
//! hwtest-runner configs/pi5_bench_a_station.yaml --port 8000 [--nats nats://...] [--db results.db] [--emulate]
//! ```

use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;

use clap::Parser;
use hwtest_runner::{server, Executor, RunnerConfig};
use hwtest_spec::StationConfig;

#[derive(Parser)]
#[command(about = "hwtest test station runner", version)]
struct Args {
    /// Station YAML.
    config: PathBuf,
    #[arg(long, default_value = "0.0.0.0")]
    host: String,
    #[arg(long, default_value_t = 8000)]
    port: u16,
    /// Python interpreter for test scripts.
    #[arg(long, env = "HWTEST_PYTHON", default_value = "python3")]
    python: String,
    /// NATS URL passed to test scripts.
    #[arg(long, env = "HWTEST_NATS_URL")]
    nats: Option<String>,
    /// Results database passed to test scripts (and listed at /runs).
    #[arg(long, env = "HWTEST_DB")]
    db: Option<PathBuf>,
    /// Run test scripts against instrument emulators.
    #[arg(long)]
    emulate: bool,
    /// Seconds to wait after Stop before killing a test.
    #[arg(long, default_value_t = 15.0)]
    stop_timeout: f64,
    /// Ignored (Python compatibility).
    #[arg(long, hide = true)]
    log_level: Option<String>,
}

fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let args = Args::parse();
    let station = match StationConfig::from_file(&args.config) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("error: {e}");
            return ExitCode::FAILURE;
        }
    };
    for t in &station.test_cases {
        if station.script_path(t).is_none() {
            tracing::warn!(
                "test case {:?}: no script found (set script: or add {}.py)",
                t.id,
                t.id
            );
        }
    }
    let executor = Executor::new(RunnerConfig {
        station,
        python: args.python,
        nats_url: args.nats,
        db_path: args.db,
        emulate: args.emulate,
        stop_timeout: Duration::from_secs_f64(args.stop_timeout),
    });
    let rt = match tokio::runtime::Runtime::new() {
        Ok(rt) => rt,
        Err(e) => {
            eprintln!("error: {e}");
            return ExitCode::FAILURE;
        }
    };
    let result = rt.block_on(async {
        let listener = tokio::net::TcpListener::bind((args.host.as_str(), args.port))
            .await
            .map_err(|e| format!("cannot listen on {}:{}: {e}", args.host, args.port))?;
        tracing::info!("runner on http://{}:{}", args.host, args.port);
        let stopper = executor.clone();
        axum::serve(listener, server::router(executor))
            .with_graceful_shutdown(async move {
                let _ = tokio::signal::ctrl_c().await;
                stopper.stop();
            })
            .await
            .map_err(|e| e.to_string())
    });
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::FAILURE
        }
    }
}
