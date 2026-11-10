//! `hwtest-rack`: rack status server and rack instance tools.
//!
//! ```text
//! hwtest-rack serve configs/pi5_mcc_intg_a_rack.yaml --serial 001 --port 8080 [--nats nats://localhost:4222] [--emulate]
//! hwtest-rack configs/pi5_mcc_intg_a_rack.yaml --port 8080      # same as `serve` (systemd unit compatibility)
//! hwtest-rack check configs/*.yaml                              # validate rack files
//! hwtest-rack init --rack-class pi5_mcc_intg_a_rack --serial 001
//! hwtest-rack calibrate configs/pi5_mcc_intg_a_rack.yaml --serial 001 --output control_voltage --input voltage_0
//! hwtest-rack info --rack-class pi5_mcc_intg_a_rack --serial 001
//! hwtest-rack list
//! ```

use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use clap::{Parser, Subcommand};
use hwtest_instruments::DriverEnv;
use hwtest_nats::{NatsConfig, NatsConnection};
use hwtest_rack::calibrate::{calibrate, CalibrationPlan};
use hwtest_rack::{server, Rack};
use hwtest_spec::instance::{find_instance_file, load_instance};
use hwtest_spec::search::rack_instance_search_paths;
use hwtest_spec::{RackConfig, RackInstance};

#[derive(Parser)]
#[command(about = "hwtest test rack tools", version)]
struct Cli {
    #[command(subcommand)]
    command: Command,
    /// Debug logging.
    #[arg(long, global = true)]
    debug: bool,
}

#[derive(Subcommand)]
enum Command {
    /// Open the rack and serve status over HTTP (and streams over NATS).
    Serve {
        /// Rack class YAML.
        config: PathBuf,
        /// Rack instance serial (loads calibration).
        #[arg(long, env = "HWTEST_RACK_SERIAL")]
        serial: Option<String>,
        #[arg(long, default_value = "0.0.0.0")]
        host: String,
        #[arg(long, default_value_t = 8000)]
        port: u16,
        /// Publish instrument streams to this NATS server.
        #[arg(long, env = "HWTEST_NATS_URL")]
        nats: Option<String>,
        /// Use emulators instead of hardware.
        #[arg(long)]
        emulate: bool,
        /// Ignored (Python compatibility).
        #[arg(long, hide = true)]
        log_level: Option<String>,
    },
    /// Validate rack YAML files.
    Check {
        /// Files to check.
        #[arg(required = true)]
        files: Vec<PathBuf>,
    },
    /// Create a rack instance (calibration) file.
    Init {
        #[arg(long)]
        rack_class: String,
        #[arg(long)]
        serial: String,
        #[arg(long, default_value = "")]
        description: String,
        /// Output path (default ~/.config/hwtest/racks/<class>_<serial>.yaml).
        #[arg(long)]
        output: Option<PathBuf>,
        /// Overwrite an existing file.
        #[arg(long)]
        force: bool,
    },
    /// Calibrate an analog input against an analog output and save the factor.
    Calibrate {
        /// Rack class YAML.
        config: PathBuf,
        #[arg(long)]
        serial: String,
        /// Logical analog output driving the reference.
        #[arg(long, default_value = "control_voltage")]
        output: String,
        /// Logical analog input to calibrate.
        #[arg(long, default_value = "voltage_0")]
        input: String,
        /// Calibration factor name to store.
        #[arg(long, default_value = "mcc118_scale_factor")]
        factor_name: String,
        /// Reference voltages.
        #[arg(long, value_delimiter = ',', default_value = "1.0,2.5,4.0")]
        voltages: Vec<f64>,
        /// Settling time per point (s).
        #[arg(long, default_value_t = 0.1)]
        settling_time: f64,
        /// Readings per point.
        #[arg(long, default_value_t = 10)]
        samples: usize,
        /// Create the instance file if missing.
        #[arg(long)]
        create: bool,
        /// Show results without saving.
        #[arg(long)]
        dry_run: bool,
        /// Use emulators.
        #[arg(long)]
        emulate: bool,
    },
    /// Show a rack instance file.
    Info {
        #[arg(long)]
        rack_class: String,
        #[arg(long)]
        serial: Option<String>,
    },
    /// List rack instance files.
    List,
}

fn args() -> Vec<String> {
    // `hwtest-rack <config.yaml> ...` means `hwtest-rack serve <config.yaml> ...`.
    let mut a: Vec<String> = std::env::args().collect();
    let is_yaml = |x: &String| {
        Path::new(x)
            .extension()
            .is_some_and(|e| e.eq_ignore_ascii_case("yaml") || e.eq_ignore_ascii_case("yml"))
    };
    if a.get(1).is_some_and(is_yaml) {
        a.insert(1, "serve".into());
    }
    a
}

fn rack_class(config: &Path) -> String {
    config.file_stem().map_or_else(String::new, |s| s.to_string_lossy().into_owned())
}

fn now() -> String {
    chrono::Utc::now().to_rfc3339()
}

async fn serve(
    config: &Path,
    serial: Option<&str>,
    host: &str,
    port: u16,
    nats: Option<String>,
    emulate: bool,
) -> Result<(), String> {
    let cfg = RackConfig::from_file(config).map_err(|e| e.to_string())?;
    let instance = match serial {
        Some(s) => Some(
            load_instance(&rack_class(config), Some(s), None, None).map_err(|e| e.to_string())?,
        ),
        None => None,
    };
    let env = if emulate { DriverEnv::emulated() } else { DriverEnv::from_env() };
    let rack = tokio::task::spawn_blocking(move || Rack::open(cfg, instance, env))
        .await
        .map_err(|e| e.to_string())?;
    let st = rack.status();
    tracing::info!(
        "rack {} {} ({} instruments{})",
        st.rack_id,
        st.state,
        st.instruments.len(),
        if st.emulated { ", emulated" } else { "" }
    );
    let state = server::AppState::new(rack);
    if let Some(url) = nats {
        let conn =
            NatsConnection::connect(NatsConfig::from_url(url)).await.map_err(|e| e.to_string())?;
        let n = server::start_streaming(&state, &conn).await.map_err(|e| e.to_string())?;
        tracing::info!("publishing {n} instrument stream(s) to NATS");
    }
    let listener = tokio::net::TcpListener::bind((host, port))
        .await
        .map_err(|e| format!("cannot listen on {host}:{port}: {e}"))?;
    tracing::info!("serving on http://{host}:{port}");
    let shutdown_state = state.clone();
    axum::serve(listener, server::router(state))
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await
        .map_err(|e| e.to_string())?;
    shutdown_state.rack.lock().unwrap_or_else(std::sync::PoisonError::into_inner).close();
    Ok(())
}

#[allow(clippy::too_many_lines)] // one arm per subcommand
fn run(cli: Cli) -> Result<(), String> {
    match cli.command {
        Command::Serve { config, serial, host, port, nats, emulate, .. } => {
            let rt = tokio::runtime::Runtime::new().map_err(|e| e.to_string())?;
            rt.block_on(serve(&config, serial.as_deref(), &host, port, nats, emulate))
        }
        Command::Check { files } => {
            let mut failed = 0;
            for f in &files {
                match RackConfig::from_file(f) {
                    Ok(r) => println!(
                        "{}: OK ({} instruments, {} channels)",
                        f.display(),
                        r.instruments.len(),
                        r.channels().count()
                    ),
                    Err(e) => {
                        failed += 1;
                        println!("{e}");
                    }
                }
            }
            if failed == 0 {
                Ok(())
            } else {
                Err(format!("{failed} file(s) invalid"))
            }
        }
        Command::Init { rack_class, serial, description, output, force } => {
            if let Some(existing) = find_instance_file(&rack_class, Some(&serial), None) {
                if !force && output.is_none() {
                    return Err(format!(
                        "instance file already exists: {} (use --force to overwrite)",
                        existing.display()
                    ));
                }
            }
            let mut inst = RackInstance::new(&serial, &rack_class, &description, &now());
            let path = inst.save(output.as_deref()).map_err(|e| e.to_string())?;
            println!(
                "Saved {}\nNext: hwtest-rack calibrate <rack.yaml> --serial {serial}",
                path.display()
            );
            Ok(())
        }
        Command::Calibrate {
            config,
            serial,
            output,
            input,
            factor_name,
            voltages,
            settling_time,
            samples,
            create,
            dry_run,
            emulate,
        } => {
            let class = rack_class(&config);
            let mut inst = match load_instance(&class, Some(&serial), None, None) {
                Ok(i) => i,
                Err(_) if create => RackInstance::new(&serial, &class, "", &now()),
                Err(e) => return Err(format!("{e}\nUse --create or run `hwtest-rack init` first")),
            };
            let cfg = RackConfig::from_file(&config).map_err(|e| e.to_string())?;
            let env = if emulate { DriverEnv::emulated() } else { DriverEnv::from_env() };
            let rack = Rack::open(cfg, None, env);
            let plan = CalibrationPlan {
                output: output.clone(),
                input,
                voltages,
                settling: Duration::from_secs_f64(settling_time),
                samples,
            };
            let result = calibrate(&rack, &plan).map_err(|e| e.to_string())?;
            println!("Scale factor: {:.5}", result.scale_factor);
            for p in &result.points {
                println!(
                    "  {:.3} V -> {:.4} V (scale {:.5})",
                    p.reference_voltage, p.measured_voltage, p.scale_factor
                );
            }
            if dry_run {
                println!("Dry run: not saved");
                return Ok(());
            }
            inst.calibration.insert(factor_name, result.scale_factor);
            inst.metadata.calibrated_at = now();
            inst.metadata.calibrated_by = "hwtest-rack calibrate".into();
            inst.metadata.reference_instrument = output;
            inst.metadata.notes = result.notes();
            let path = inst.save(None).map_err(|e| e.to_string())?;
            println!("Saved {}", path.display());
            Ok(())
        }
        Command::Info { rack_class, serial } => {
            let i = load_instance(&rack_class, serial.as_deref(), None, None)
                .map_err(|e| e.to_string())?;
            println!("Rack instance: {} #{}", i.instance.rack_class, i.instance.serial_number);
            println!(
                "  File: {}",
                i.source_path.as_ref().map_or_else(String::new, |p| p.display().to_string())
            );
            println!("  Description: {}", i.instance.description);
            println!(
                "  Calibrated: {} by {} ({})",
                i.metadata.calibrated_at, i.metadata.calibrated_by, i.metadata.notes
            );
            for (k, v) in &i.calibration {
                println!("  {k}: {v}");
            }
            Ok(())
        }
        Command::List => {
            for dir in rack_instance_search_paths() {
                let Ok(entries) = std::fs::read_dir(&dir) else { continue };
                println!("{}:", dir.display());
                let mut files: Vec<_> = entries
                    .flatten()
                    .map(|e| e.path())
                    .filter(|p| p.extension().is_some_and(|e| e == "yaml" || e == "yml"))
                    .collect();
                files.sort();
                for f in files {
                    match RackInstance::from_file(&f) {
                        Ok(i) => println!(
                            "  {} #{} ({})",
                            i.instance.rack_class,
                            i.instance.serial_number,
                            f.display()
                        ),
                        Err(e) => println!("  {}: {e}", f.display()),
                    }
                }
            }
            Ok(())
        }
    }
}

fn main() -> ExitCode {
    let cli = Cli::parse_from(args());
    let filter = if cli.debug { "debug" } else { "info" };
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| filter.into()),
        )
        .init();
    match run(cli) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::FAILURE
        }
    }
}
