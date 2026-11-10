//! Instrument drivers for hwtest.
//!
//! - [`traits`]: the common instrument model (analog/digital I/O, PSU, streaming)
//! - [`scpi`]: SCPI over raw TCP, serial or USBTMC (replaces pyvisa)
//! - [`bk`]: BK Precision 9100-series PSUs, with emulator and TCP emulator server
//! - [`daqhats`]: MCC 118/134/152 via `libdaqhats` (runtime-loaded), with emulator
//! - [`waveshare`]: High-Precision AD/DA (ADS1256 + DAC8532) via spidev/GPIO, with
//!   an SPI-level emulator
//! - [`registry`]: opening instruments from rack YAML, including emulation mode

pub mod bk;
pub mod daqhats;
pub mod error;
pub mod registry;
pub mod scpi;
pub mod traits;
pub mod waveshare;

pub use error::{InstrumentError, Result};
pub use registry::{builtin_name, open_instrument, DriverEnv};
pub use traits::{
    AnalogInput, AnalogOutput, ChannelInfo, ChannelKind, ChannelRef, DigitalIo, Instrument,
    PowerSupply, StreamSink, StreamSource,
};
