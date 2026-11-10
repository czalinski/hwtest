//! Measurement Computing DAQ HATs (MCC 118, 134, 152).
//!
//! The drivers talk to a [`DaqHats`] backend: [`ffi::LibDaqHats`] calls the
//! vendor C library `libdaqhats` (loaded at runtime, so builds and tests work
//! without it), and [`emulator::EmulatedDaqHats`] models the boards in
//! software for development without hardware.

pub mod emulator;
pub mod ffi;
pub mod mcc118;
pub mod mcc134;
pub mod mcc152;

use std::sync::Arc;

use crate::error::{InstrumentError, Result};

/// HAT product ids.
pub mod ids {
    /// MCC 118 voltage DAQ.
    pub const MCC_118: u16 = 0x0142;
    /// MCC 134 thermocouple DAQ.
    pub const MCC_134: u16 = 0x0143;
    /// MCC 152 DIO / analog output.
    pub const MCC_152: u16 = 0x0144;
    /// MCC 128.
    pub const MCC_128: u16 = 0x0146;
    /// MCC 172.
    pub const MCC_172: u16 = 0x0145;
}

/// Scan option: continuous acquisition.
pub const OPTS_CONTINUOUS: u32 = 0x0010;
/// Scan status bits.
pub mod status {
    /// Hardware overrun (data lost on the HAT).
    pub const HW_OVERRUN: u16 = 0x0001;
    /// Buffer overrun (host buffer full).
    pub const BUFFER_OVERRUN: u16 = 0x0002;
    /// Scan is running.
    pub const RUNNING: u16 = 0x0008;
}

/// MCC 134 special readings.
pub mod tc_values {
    /// Open thermocouple.
    pub const OPEN: f64 = -9999.0;
    /// Over range.
    pub const OVERRANGE: f64 = -8888.0;
    /// Common-mode error.
    pub const COMMON_MODE: f64 = -7777.0;
}

/// MCC 152 DIO configuration item: direction (`0` = output, `1` = input).
pub const DIO_DIRECTION: u8 = 0;

/// A detected HAT.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct HatInfo {
    /// Board address (0-7).
    pub address: u8,
    /// Product id.
    pub id: u16,
    /// Hardware version.
    pub version: u16,
    /// Product name.
    pub product_name: String,
}

/// Result of one scan read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ScanRead {
    /// Status bits (see [`status`]).
    pub status: u16,
    /// Samples read per channel.
    pub samples_per_channel: u32,
}

/// The subset of the `libdaqhats` API used by the drivers.
#[allow(missing_docs, clippy::missing_errors_doc)]
pub trait DaqHats: Send + Sync {
    fn list(&self) -> Result<Vec<HatInfo>>;

    fn mcc118_open(&self, address: u8) -> Result<()>;
    fn mcc118_close(&self, address: u8) -> Result<()>;
    fn mcc118_serial(&self, address: u8) -> Result<String>;
    fn mcc118_a_in_read(&self, address: u8, channel: u8) -> Result<f64>;
    fn mcc118_scan_actual_rate(&self, channel_count: u8, rate: f64) -> Result<f64>;
    fn mcc118_scan_start(
        &self,
        address: u8,
        channel_mask: u8,
        samples_per_channel: u32,
        rate: f64,
        options: u32,
    ) -> Result<()>;
    /// Reads up to `samples_per_channel` (-1 = all available) into `buffer`.
    fn mcc118_scan_read(
        &self,
        address: u8,
        samples_per_channel: i32,
        timeout: f64,
        buffer: &mut [f64],
    ) -> Result<ScanRead>;
    fn mcc118_scan_stop(&self, address: u8) -> Result<()>;
    fn mcc118_scan_cleanup(&self, address: u8) -> Result<()>;

    fn mcc134_open(&self, address: u8) -> Result<()>;
    fn mcc134_close(&self, address: u8) -> Result<()>;
    fn mcc134_serial(&self, address: u8) -> Result<String>;
    fn mcc134_tc_type_write(&self, address: u8, channel: u8, tc_type: u8) -> Result<()>;
    fn mcc134_update_interval_write(&self, address: u8, seconds: u8) -> Result<()>;
    fn mcc134_t_in_read(&self, address: u8, channel: u8) -> Result<f64>;

    fn mcc152_open(&self, address: u8) -> Result<()>;
    fn mcc152_close(&self, address: u8) -> Result<()>;
    fn mcc152_serial(&self, address: u8) -> Result<String>;
    fn mcc152_dio_config_write_bit(
        &self,
        address: u8,
        channel: u8,
        item: u8,
        value: u8,
    ) -> Result<()>;
    fn mcc152_dio_input_read_bit(&self, address: u8, channel: u8) -> Result<bool>;
    fn mcc152_dio_output_write_bit(&self, address: u8, channel: u8, value: bool) -> Result<()>;
    fn mcc152_a_out_write(&self, address: u8, channel: u8, value: f64) -> Result<()>;
}

/// Shared backend handle.
pub type Backend = Arc<dyn DaqHats>;

/// Text for a `libdaqhats` result code.
pub fn result_message(code: i32) -> &'static str {
    match code {
        0 => "success",
        -1 => "bad parameter",
        -2 => "device busy",
        -3 => "timeout",
        -4 => "resource lock timeout",
        -5 => "invalid device (wrong HAT type at this address?)",
        -6 => "resource unavailable",
        -7 => "communication failure",
        _ => "undefined error",
    }
}

pub(crate) fn check(code: i32, what: impl FnOnce() -> String) -> Result<()> {
    if code == 0 {
        Ok(())
    } else {
        Err(InstrumentError::Comms(format!("{}: {} ({code})", what(), result_message(code))))
    }
}

pub(crate) fn check_address(address: i64) -> Result<u8> {
    u8::try_from(address)
        .ok()
        .filter(|a| *a <= 7)
        .ok_or_else(|| InstrumentError::Config(format!("address must be 0-7, got {address}")))
}

/// Opens the real library if present; otherwise explains how to install it.
///
/// # Errors
/// [`InstrumentError::Library`] if `libdaqhats` is not installed.
pub fn system_backend() -> Result<Backend> {
    Ok(Arc::new(ffi::LibDaqHats::load()?))
}
