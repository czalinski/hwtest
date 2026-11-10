//! Test rack runtime.
//!
//! A [`Rack`] opens every instrument in a rack YAML, verifies each one's
//! identity against the expected manufacturer/model, and gives test code
//! access by logical channel name (`dut_voltage`, `relay_dut_power`, ...).
//! It also publishes instrument streams to NATS, runs calibration, and backs
//! the `hwtest-rack` status server.

pub mod calibrate;
pub mod rack;
pub mod server;

pub use rack::{InstrumentState, InstrumentStatus, Rack, RackError, RackStatus};
