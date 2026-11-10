//! UUT simulator: a stand-in Unit Under Test for integration testing
//! (normally a Raspberry Pi 4 with the Waveshare AD/DA and CAN HATs).
//!
//! - CAN: 10 Hz heartbeat (8-byte big-endian counter on id 0x100), optional
//!   echo (id + offset, optional id filter), receive buffer of 100 frames
//! - DAC/ADC: Waveshare High-Precision AD/DA (or emulated)
//! - GPIO: MCP23017 expander (or emulated)
//! - Fault injection: after a delay the DAC output is offset, cyclically
//!   (delay, then duration) or permanently (duration 0)
//!
//! [`server`] exposes it over HTTP (same API as the Python simulator) and
//! [`client::UutClient`] is the matching client.

pub mod cli;
pub mod client;
pub mod server;
pub mod simulator;

pub use client::UutClient;
pub use simulator::{AdDaBackend, GpioBackend, SimConfig, Simulator};
