//! Validates test definition files and prints every problem found.
//!
//! Usage: `cargo run -p hwtest-spec --example check_spec -- <file.yaml>...`
fn main() {
    let mut failed = false;
    for path in std::env::args().skip(1) {
        match hwtest_spec::TestDefinition::from_file(&path) {
            Ok(d) => println!(
                "{path}: OK ({} states, {} monitors)",
                d.monitor_states.len(),
                d.monitors.len()
            ),
            Err(e) => {
                failed = true;
                println!("{e}");
            }
        }
    }
    std::process::exit(i32::from(failed));
}
