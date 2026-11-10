//! Test station runner.
//!
//! Operators pick a test case and mode in a browser; the runner launches the
//! test case's Python script (`python <script>`) with the station's settings
//! in environment variables, follows its `@@hwtest` progress events, keeps a
//! log tail, and stops it cleanly (SIGINT, then kill after a grace period).
//! Only one test runs at a time.

pub mod executor;
pub mod server;

pub use executor::{Executor, RunState, RunStatus, RunnerConfig};
