//! ADS1256 24-bit ADC driver (port of the original Python driver: same
//! command sequences, registers and timing).

use crate::error::{InstrumentError, Result};

use super::bus::{delay_us, lock, SharedBus};

/// Commands.
pub mod cmd {
    #![allow(missing_docs)]
    pub const WAKEUP: u8 = 0x00;
    pub const RDATA: u8 = 0x01;
    pub const SDATAC: u8 = 0x0F;
    pub const RREG: u8 = 0x10;
    pub const WREG: u8 = 0x50;
    pub const SELFCAL: u8 = 0xF0;
    pub const SYNC: u8 = 0xFC;
    pub const RESET: u8 = 0xFE;
}

/// Registers.
pub mod reg {
    #![allow(missing_docs)]
    pub const STATUS: u8 = 0x00;
    pub const MUX: u8 = 0x01;
    pub const ADCON: u8 = 0x02;
    pub const DRATE: u8 = 0x03;
}

/// AINCOM mux code.
pub const AINCOM: u8 = 8;

/// Data-rate register codes and sample rates.
pub const DATA_RATES: &[(&str, u8, f64)] = &[
    ("SPS_30000", 0xF0, 30000.0),
    ("SPS_15000", 0xE0, 15000.0),
    ("SPS_7500", 0xD0, 7500.0),
    ("SPS_3750", 0xC0, 3750.0),
    ("SPS_2000", 0xB0, 2000.0),
    ("SPS_1000", 0xA1, 1000.0),
    ("SPS_500", 0x92, 500.0),
    ("SPS_100", 0x82, 100.0),
    ("SPS_60", 0x72, 60.0),
    ("SPS_50", 0x63, 50.0),
    ("SPS_30", 0x53, 30.0),
    ("SPS_25", 0x43, 25.0),
    ("SPS_15", 0x33, 15.0),
    ("SPS_10", 0x23, 10.0),
    ("SPS_5", 0x13, 5.0),
    ("SPS_2_5", 0x03, 2.5),
];

/// Gain codes (`GAIN_1`=0 .. `GAIN_64`=6).
pub const GAINS: &[(&str, u8)] = &[
    ("GAIN_1", 0),
    ("GAIN_2", 1),
    ("GAIN_4", 2),
    ("GAIN_8", 3),
    ("GAIN_16", 4),
    ("GAIN_32", 5),
    ("GAIN_64", 6),
];

/// Pins and analog settings.
#[derive(Debug, Clone)]
pub struct Ads1256Config {
    /// Chip select (BCM).
    pub cs_pin: u32,
    /// Data ready, active low (BCM).
    pub drdy_pin: u32,
    /// Reset, active low (BCM), if wired.
    pub reset_pin: Option<u32>,
    /// Gain code 0-6.
    pub gain: u8,
    /// DRATE register code.
    pub data_rate: u8,
    /// Reference voltage.
    pub vref: f64,
}

impl Default for Ads1256Config {
    fn default() -> Self {
        Self { cs_pin: 22, drdy_pin: 17, reset_pin: Some(18), gain: 0, data_rate: 0x82, vref: 2.5 }
    }
}

/// The ADC.
pub struct Ads1256 {
    bus: SharedBus,
    config: Ads1256Config,
}

impl Ads1256 {
    /// Claims pins, resets, configures and self-calibrates the chip.
    ///
    /// # Errors
    /// Pin or bus failure, or DRDY never asserting (chip not responding).
    pub fn open(bus: SharedBus, config: Ads1256Config) -> Result<Self> {
        {
            let mut b = lock(&bus);
            b.gpio.setup_output(config.cs_pin, true)?;
            b.gpio.setup_input(config.drdy_pin)?;
            if let Some(r) = config.reset_pin {
                b.gpio.setup_output(r, true)?;
            }
        }
        let me = Self { bus, config };
        me.reset()?;
        me.configure()?;
        Ok(me)
    }

    /// Sample rate for the configured DRATE code.
    pub fn sample_rate(&self) -> f64 {
        DATA_RATES.iter().find(|(_, c, _)| *c == self.config.data_rate).map_or(0.0, |(_, _, r)| *r)
    }

    fn wait_drdy(&self, timeout_ms: u64) -> Result<()> {
        for _ in 0..timeout_ms * 10 {
            if !lock(&self.bus).gpio.get(self.config.drdy_pin)? {
                return Ok(());
            }
            delay_us(100);
        }
        Err(InstrumentError::Comms(format!(
            "ADS1256: DRDY (GPIO{}) did not assert within {timeout_ms} ms",
            self.config.drdy_pin
        )))
    }

    fn with_cs<T>(&self, f: impl FnOnce(&mut super::bus::Bus) -> Result<T>) -> Result<T> {
        let mut b = lock(&self.bus);
        b.gpio.set(self.config.cs_pin, false)?;
        let r = f(&mut b);
        let cs = b.gpio.set(self.config.cs_pin, true);
        let v = r?;
        cs?;
        Ok(v)
    }

    fn command(&self, c: u8) -> Result<()> {
        self.with_cs(|b| b.spi.write(&[c]))
    }

    fn write_reg(&self, r: u8, value: u8) -> Result<()> {
        self.with_cs(|b| b.spi.write(&[cmd::WREG | r, 0x00, value]))
    }

    /// Reads a register.
    ///
    /// # Errors
    /// Bus failure.
    pub fn read_reg(&self, r: u8) -> Result<u8> {
        self.with_cs(|b| {
            b.spi.write(&[cmd::RREG | r, 0x00])?;
            delay_us(10); // t6
            Ok(b.spi.read(1)?[0])
        })
    }

    fn reset(&self) -> Result<()> {
        if let Some(pin) = self.config.reset_pin {
            lock(&self.bus).gpio.set(pin, false)?;
            delay_us(1000);
            lock(&self.bus).gpio.set(pin, true)?;
            delay_us(1000);
        } else {
            self.command(cmd::RESET)?;
            delay_us(1000);
        }
        self.wait_drdy(1000)
    }

    fn configure(&self) -> Result<()> {
        self.command(cmd::SDATAC)?;
        delay_us(100);
        self.write_reg(reg::STATUS, 0x00)?;
        self.write_reg(reg::MUX, 0x08)?;
        self.write_reg(reg::ADCON, self.config.gain)?;
        self.write_reg(reg::DRATE, self.config.data_rate)?;
        self.command(cmd::SELFCAL)?;
        self.wait_drdy(2000)
    }

    /// Chip id (upper nibble of STATUS; 3 for the ADS1256).
    ///
    /// # Errors
    /// Bus failure.
    pub fn chip_id(&self) -> Result<u8> {
        Ok(self.read_reg(reg::STATUS)? >> 4)
    }

    fn set_channel(&self, pos: u8, neg: u8) -> Result<()> {
        self.write_reg(reg::MUX, (pos << 4) | neg)?;
        self.with_cs(|b| {
            b.spi.write(&[cmd::SYNC])?;
            delay_us(4); // t11
            b.spi.write(&[cmd::WAKEUP])
        })
    }

    fn read_raw(&self) -> Result<i32> {
        self.wait_drdy(1000)?;
        let d = self.with_cs(|b| {
            b.spi.write(&[cmd::RDATA])?;
            delay_us(10); // t6
            b.spi.read(3)
        })?;
        let v = (i32::from(d[0]) << 16) | (i32::from(d[1]) << 8) | i32::from(d[2]);
        Ok(if v >= 0x80_0000 { v - 0x100_0000 } else { v })
    }

    /// Full scale is ±2·VREF/PGA (datasheet "Analog input range"; Waveshare's
    /// example uses `raw * 5.0 / 0x7FFFFF` with VREF = 2.5 V). The original
    /// Python driver used ±VREF/PGA and read half the true voltage.
    fn to_volts(&self, raw: i32) -> f64 {
        f64::from(raw) * 2.0 * self.config.vref
            / (8_388_607.0 * f64::from(1u32 << self.config.gain))
    }

    /// Single-ended reading of `channel` (0-7) against AINCOM.
    ///
    /// # Errors
    /// Out-of-range channel or bus failure.
    pub fn read_voltage(&self, channel: u8) -> Result<f64> {
        if channel > 7 {
            return Err(InstrumentError::OutOfRange(format!(
                "ADS1256 channel must be 0-7, got {channel}"
            )));
        }
        self.set_channel(channel, AINCOM)?;
        Ok(self.to_volts(self.read_raw()?))
    }

    /// Differential reading `positive - negative`.
    ///
    /// # Errors
    /// Out-of-range channel or bus failure.
    pub fn read_differential(&self, positive: u8, negative: u8) -> Result<f64> {
        if positive > 7 || negative > 7 {
            return Err(InstrumentError::OutOfRange(format!(
                "ADS1256 channels must be 0-7, got {positive}/{negative}"
            )));
        }
        self.set_channel(positive, negative)?;
        Ok(self.to_volts(self.read_raw()?))
    }
}
