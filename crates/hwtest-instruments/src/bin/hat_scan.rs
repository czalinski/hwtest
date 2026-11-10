//! `hwtest-hat-scan`: lists the MCC DAQ HATs on this board.

use std::process::ExitCode;

use clap::Parser;
use hwtest_instruments::daqhats::system_backend;

#[derive(Parser)]
#[command(about = "List MCC DAQ HATs detected by libdaqhats")]
struct Args {
    /// Print JSON instead of a table.
    #[arg(long)]
    json: bool,
}

fn main() -> ExitCode {
    let args = Args::parse();
    let hats = match system_backend().and_then(|b| b.list()) {
        Ok(h) => h,
        Err(e) => {
            eprintln!("error: {e}");
            return ExitCode::FAILURE;
        }
    };
    if args.json {
        println!("{}", serde_json::to_string_pretty(&hats).unwrap_or_default());
    } else if hats.is_empty() {
        println!("No MCC DAQ HATs found.");
    } else {
        println!("{:<8} {:<8} {:<8} NAME", "ADDRESS", "ID", "VERSION");
        for h in &hats {
            println!("{:<8} {:<#8x} {:<8} {}", h.address, h.id, h.version, h.product_name);
        }
    }
    ExitCode::SUCCESS
}
