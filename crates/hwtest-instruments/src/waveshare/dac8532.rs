//! DAC8532 16-bit dual DAC driver (port of the original Python driver).

use crate::error::{InstrumentError, Result};

use super::bus::{lock, SharedBus};

/// Command bytes.
pub mod cmd {
    /// Write A input register and update A.
    pub const WRITE_A: u8 = 0x10;
    /// Write B input register and update B.
    pub const WRITE_B: u8 = 0x24;
    /// Load A input register without updating.
    pub const LOAD_A: u8 = 0x00;
    /// Write B input register and update both outputs.
    pub const WRITE_B_UPDATE_BOTH: u8 = 0x34;
}

/// The DAC.
pub struct Dac8532 {
    bus: SharedBus,
    cs_pin: u32,
    vref: f64,
    values: [u16; 2],
}

impl Dac8532 {
    /// Claims the chip select and sets both outputs to 0 V.
    ///
    /// # Errors
    /// Pin or bus failure.
    pub fn open(bus: SharedBus, cs_pin: u32, vref: f64) -> Result<Self> {
        lock(&bus).gpio.setup_output(cs_pin, true)?;
        let mut me = Self { bus, cs_pin, vref, values: [0; 2] };
        me.write_raw(0, 0)?;
        me.write_raw(1, 0)?;
        Ok(me)
    }

    fn frame(&self, command: u8, value: u16) -> Result<()> {
        let mut b = lock(&self.bus);
        b.gpio.set(self.cs_pin, false)?;
        let r = b.spi.write(&[command, (value >> 8) as u8, (value & 0xFF) as u8]);
        let cs = b.gpio.set(self.cs_pin, true);
        r?;
        cs
    }

    /// Writes a raw code to channel 0 (A) or 1 (B).
    ///
    /// # Errors
    /// Bad channel or bus failure.
    pub fn write_raw(&mut self, channel: u8, value: u16) -> Result<()> {
        let c = match channel {
            0 => cmd::WRITE_A,
            1 => cmd::WRITE_B,
            _ => {
                return Err(InstrumentError::OutOfRange(format!(
                    "DAC8532 channel must be 0 or 1, got {channel}"
                )))
            }
        };
        self.frame(c, value)?;
        self.values[usize::from(channel)] = value;
        Ok(())
    }

    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    fn code(&self, volts: f64) -> Result<u16> {
        if !(0.0..=self.vref).contains(&volts) {
            return Err(InstrumentError::OutOfRange(format!(
                "DAC8532 voltage must be 0-{} V, got {volts}",
                self.vref
            )));
        }
        Ok(((volts / self.vref) * 65535.0).clamp(0.0, 65535.0) as u16)
    }

    /// Sets an output voltage.
    ///
    /// # Errors
    /// Out of range or bus failure.
    pub fn write_voltage(&mut self, channel: u8, volts: f64) -> Result<()> {
        let code = self.code(volts)?;
        self.write_raw(channel, code)
    }

    /// Sets both outputs, updating them simultaneously.
    ///
    /// # Errors
    /// Out of range or bus failure.
    pub fn write_both(&mut self, a: f64, b: f64) -> Result<()> {
        let (ca, cb) = (self.code(a)?, self.code(b)?);
        self.frame(cmd::LOAD_A, ca)?;
        self.frame(cmd::WRITE_B_UPDATE_BOTH, cb)?;
        self.values = [ca, cb];
        Ok(())
    }

    /// The last voltage written (software readback).
    pub fn read_voltage(&self, channel: u8) -> Option<f64> {
        self.values.get(usize::from(channel)).map(|v| f64::from(*v) / 65535.0 * self.vref)
    }
}
