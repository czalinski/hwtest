//! Command line of `hwtest-uut-sim` (also installed as `pi4-waveshare-sim`).
//!
//! ```text
//! hwtest-uut-sim --port 8080 --can-interface can0 --waveshare-adda --no-gpio
//! hwtest-uut-sim --port 8080 --emulate --can-interface vcan0     # no hardware
//! ```

use std::process::ExitCode;
use std::sync::Arc;

use crate::{server, AdDaBackend, GpioBackend, SimConfig, Simulator};
use clap::Parser;

#[derive(Parser)]
// Command-line flags (--no-can, --emulate, ...) are naturally independent bools.
#[allow(clippy::struct_excessive_bools)]
#[command(about = "UUT simulator (CAN, ADC/DAC, GPIO, fault injection)", version)]
struct Args {
    #[arg(long, default_value = "0.0.0.0")]
    host: String,
    #[arg(long, default_value_t = 8080)]
    port: u16,
    /// CAN interface (can0, vcan0, virtual:<name>).
    #[arg(long, default_value = "can0")]
    can_interface: String,
    /// Accepted for compatibility (set the bitrate with `ip link`).
    #[arg(long, default_value_t = 500_000, hide = true)]
    can_bitrate: u32,
    #[arg(long)]
    no_can: bool,
    #[arg(long)]
    no_dac: bool,
    #[arg(long)]
    no_adc: bool,
    #[arg(long)]
    no_gpio: bool,
    /// MCP23017 I2C address.
    #[arg(long, default_value = "0x20", value_parser = parse_int)]
    gpio_address: u16,
    /// I2C bus for the MCP23017.
    #[arg(long, default_value_t = 1)]
    gpio_bus: u32,
    /// Use the Waveshare High-Precision AD/DA board.
    #[arg(long)]
    waveshare_adda: bool,
    /// Emulate the AD/DA board and the GPIO expander (no hardware).
    #[arg(long)]
    emulate: bool,
    /// With --emulate: wire DAC A/B to ADC 0/1 (otherwise set ADC inputs via
    /// `PUT /emulation/adc`).
    #[arg(long)]
    loopback: bool,
    /// Heartbeat interval in ms (0 disables).
    #[arg(long, default_value_t = 100)]
    heartbeat_interval_ms: u64,
    #[arg(long, default_value_t = 0.0)]
    failure_delay: f64,
    #[arg(long, default_value_t = 10.0)]
    failure_duration: f64,
    #[arg(long, default_value_t = 1.0)]
    failure_offset: f64,
    #[arg(long)]
    debug: bool,
}

fn parse_int(s: &str) -> Result<u16, String> {
    let r = s.strip_prefix("0x").map_or_else(|| s.parse(), |h| u16::from_str_radix(h, 16));
    r.map_err(|e| e.to_string())
}

/// Runs the simulator server from command-line arguments.
pub fn main() -> ExitCode {
    let args = Args::parse();
    let level = if args.debug { "debug" } else { "info" };
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| level.into()),
        )
        .init();
    let adda = if args.no_dac && args.no_adc {
        AdDaBackend::None
    } else if args.emulate {
        AdDaBackend::Emulated { loopback: args.loopback }
    } else if args.waveshare_adda {
        AdDaBackend::Waveshare
    } else {
        AdDaBackend::None
    };
    let gpio = if args.no_gpio {
        GpioBackend::None
    } else if args.emulate {
        GpioBackend::Emulated
    } else {
        GpioBackend::Mcp23017 { bus: args.gpio_bus, address: args.gpio_address }
    };
    let config = SimConfig {
        can_interface: (!args.no_can).then_some(args.can_interface),
        heartbeat_interval_ms: args.heartbeat_interval_ms,
        adda,
        gpio,
        failure_delay: args.failure_delay,
        failure_duration: args.failure_duration,
        failure_offset: args.failure_offset,
        ..SimConfig::default()
    };
    let sim = Arc::new(Simulator::start(config));
    let rt = match tokio::runtime::Runtime::new() {
        Ok(r) => r,
        Err(e) => {
            eprintln!("error: {e}");
            return ExitCode::FAILURE;
        }
    };
    let result = rt.block_on(async {
        let listener = tokio::net::TcpListener::bind((args.host.as_str(), args.port))
            .await
            .map_err(|e| format!("cannot listen on {}:{}: {e}", args.host, args.port))?;
        tracing::info!("UUT simulator on http://{}:{}", args.host, args.port);
        axum::serve(listener, server::router(sim))
            .with_graceful_shutdown(async {
                let _ = tokio::signal::ctrl_c().await;
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
