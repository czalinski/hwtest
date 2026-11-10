//! `hwtest-bk-emulator`: a BK Precision PSU emulator on a TCP port.
//!
//! ```text
//! hwtest-bk-emulator --model bk9130b --port 5025
//! # rack YAML: visa_address: "TCPIP::localhost::5025::SOCKET"
//! ```

use std::net::TcpListener;
use std::process::ExitCode;

use clap::Parser;
use hwtest_instruments::bk::emulator::BkPsuEmulator;
use hwtest_instruments::bk::server::serve;

#[derive(Parser)]
#[command(about = "BK Precision PSU emulator (raw-socket SCPI)")]
struct Args {
    /// Model: bk9115 or bk9130b.
    #[arg(long, default_value = "bk9130b")]
    model: String,
    /// Listen address.
    #[arg(long, default_value = "0.0.0.0")]
    host: String,
    /// Listen port.
    #[arg(long, default_value_t = 5025)]
    port: u16,
}

fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let args = Args::parse();
    let emu = match BkPsuEmulator::by_name(&args.model) {
        Ok(e) => e,
        Err(e) => {
            eprintln!("error: {e}");
            return ExitCode::from(2);
        }
    };
    let listener = match TcpListener::bind((args.host.as_str(), args.port)) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("error: cannot listen on {}:{}: {e}", args.host, args.port);
            return ExitCode::FAILURE;
        }
    };
    tracing::info!("{} emulator listening on {}:{}", args.model, args.host, args.port);
    serve(&listener, &emu);
    ExitCode::SUCCESS
}
