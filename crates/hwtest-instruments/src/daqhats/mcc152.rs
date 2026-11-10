//! MCC 152: 8 digital I/O lines and 2 analog outputs (0-5 V).
//!
//! ```yaml
//! driver: mcc152      # legacy: hwtest_mcc.mcc152:create_instrument
//! kwargs:
//!   address: 0
//!   dio_channels: [{id: 0, name: relay_dut_power, direction: OUTPUT, initial_value: false},
//!                  {id: 2, name: sensor_door, direction: INPUT}]
//!   analog_channels: [{id: 0, name: control_voltage, initial_voltage: 0.0}]
//! ```
//!
//! Note: `libdaqhats` expects direction `0` for output and `1` for input. The
//! original Python driver wrote its own enum values (INPUT=0, OUTPUT=1)
//! directly, configuring inputs as outputs; this driver maps them correctly.

use hwtest_core::InstrumentIdentity;
use serde::Deserialize;

use super::mcc118::validate_channels;
use super::{check_address, Backend, DIO_DIRECTION};
use crate::error::{InstrumentError, Result};
use crate::traits::{
    resolve, AnalogOutput, ChannelInfo, ChannelKind, ChannelRef, DigitalIo, Instrument,
};

/// DIO direction.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum Direction {
    /// Input (default).
    #[default]
    Input,
    /// Output.
    Output,
}

/// A DIO line.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DioChannel {
    /// Line 0-7.
    pub id: u32,
    /// Logical name.
    #[serde(alias = "logical_name")]
    pub name: String,
    /// Direction.
    #[serde(default)]
    pub direction: Direction,
    /// Level driven at start-up (outputs).
    #[serde(default)]
    pub initial_value: bool,
    /// Ignored (rack YAML compatibility).
    #[serde(default, rename = "type")]
    pub kind: Option<String>,
    /// Free-text note (ignored).
    #[serde(default)]
    pub description: Option<String>,
}

/// An analog output.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnalogChannel {
    /// Output 0-1.
    pub id: u32,
    /// Logical name.
    #[serde(alias = "logical_name")]
    pub name: String,
    /// Voltage at start-up.
    #[serde(default)]
    pub initial_voltage: f64,
    /// Ignored (rack YAML compatibility).
    #[serde(default, rename = "type")]
    pub kind: Option<String>,
    /// Free-text note (ignored).
    #[serde(default)]
    pub description: Option<String>,
}

/// Configuration.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    /// HAT address 0-7.
    pub address: i64,
    /// Accepted for compatibility (the MCC 152 does not stream).
    #[serde(default)]
    pub source_id: Option<String>,
    /// DIO lines.
    #[serde(default)]
    pub dio_channels: Vec<DioChannel>,
    /// Analog outputs.
    #[serde(default)]
    pub analog_channels: Vec<AnalogChannel>,
}

/// An MCC 152.
pub struct Mcc152 {
    hats: Backend,
    address: u8,
    config: Config,
    ao: [Option<f64>; 2],
}

fn check_volts(v: f64) -> Result<()> {
    if (0.0..=5.0).contains(&v) {
        Ok(())
    } else {
        Err(InstrumentError::OutOfRange(format!("MCC 152 analog output must be 0-5 V, got {v}")))
    }
}

impl Mcc152 {
    /// Opens the board and applies directions and initial values.
    ///
    /// # Errors
    /// Invalid config or the board cannot be configured.
    pub fn open(hats: Backend, config: Config) -> Result<Self> {
        let address = check_address(config.address)?;
        validate_channels(
            config
                .dio_channels
                .iter()
                .map(|c| (c.id, c.name.as_str()))
                .chain(config.analog_channels.iter().map(|c| (c.id + 100, c.name.as_str()))),
            u32::MAX,
            "MCC 152",
        )?;
        if let Some(c) = config.dio_channels.iter().find(|c| c.id > 7) {
            return Err(InstrumentError::Config(format!(
                "DIO channel id must be 0-7, got {}",
                c.id
            )));
        }
        if let Some(c) = config.analog_channels.iter().find(|c| c.id > 1) {
            return Err(InstrumentError::Config(format!(
                "analog channel id must be 0-1, got {}",
                c.id
            )));
        }
        for c in &config.analog_channels {
            check_volts(c.initial_voltage).map_err(|e| InstrumentError::Config(e.to_string()))?;
        }
        hats.mcc152_open(address)?;
        let mut me = Self { hats, address, config, ao: [None; 2] };
        if let Err(e) = me.apply_initial() {
            let _ = me.hats.mcc152_close(address);
            return Err(e);
        }
        Ok(me)
    }

    fn apply_initial(&mut self) -> Result<()> {
        for c in &self.config.dio_channels {
            let ch = u8::try_from(c.id).unwrap_or(0);
            if c.direction == Direction::Output {
                // Set the level first so the line never glitches to the wrong state.
                self.hats.mcc152_dio_output_write_bit(self.address, ch, c.initial_value)?;
            }
            let value = u8::from(c.direction == Direction::Input); // 0 = output, 1 = input
            self.hats.mcc152_dio_config_write_bit(self.address, ch, DIO_DIRECTION, value)?;
        }
        for c in self.config.analog_channels.clone() {
            self.hats.mcc152_a_out_write(
                self.address,
                u8::try_from(c.id).unwrap_or(0),
                c.initial_voltage,
            )?;
            self.ao[c.id as usize] = Some(c.initial_voltage);
        }
        Ok(())
    }
}

impl Instrument for Mcc152 {
    fn model(&self) -> &'static str {
        "MCC 152"
    }
    fn identity(&mut self) -> Result<InstrumentIdentity> {
        Ok(InstrumentIdentity {
            manufacturer: "Measurement Computing".into(),
            model: "MCC 152".into(),
            serial: self.hats.mcc152_serial(self.address)?,
            firmware: String::new(),
        })
    }
    fn channels(&self) -> Vec<ChannelInfo> {
        let dio = self.config.dio_channels.iter().map(|c| ChannelInfo {
            name: c.name.clone(),
            id: c.id,
            kind: if c.direction == Direction::Output {
                ChannelKind::DigitalOut
            } else {
                ChannelKind::DigitalIn
            },
            unit: "",
        });
        let ao = self.config.analog_channels.iter().map(|c| ChannelInfo {
            name: c.name.clone(),
            id: c.id,
            kind: ChannelKind::AnalogOut,
            unit: "V",
        });
        dio.chain(ao).collect()
    }
    fn close(&mut self) -> Result<()> {
        self.hats.mcc152_close(self.address)
    }
    fn analog_out(&mut self) -> Option<&mut dyn AnalogOutput> {
        Some(self)
    }
    fn digital_io(&mut self) -> Option<&mut dyn DigitalIo> {
        Some(self)
    }
}

impl AnalogOutput for Mcc152 {
    fn write(&mut self, ch: &ChannelRef, value: f64) -> Result<()> {
        let id = resolve(&self.channels(), ch, &[ChannelKind::AnalogOut], "MCC 152")?;
        check_volts(value)?;
        self.hats.mcc152_a_out_write(self.address, u8::try_from(id).unwrap_or(0), value)?;
        self.ao[id as usize] = Some(value);
        Ok(())
    }

    fn last_written(&mut self, ch: &ChannelRef) -> Result<Option<f64>> {
        let id = resolve(&self.channels(), ch, &[ChannelKind::AnalogOut], "MCC 152")?;
        Ok(self.ao[id as usize])
    }
}

impl DigitalIo for Mcc152 {
    fn read(&mut self, ch: &ChannelRef) -> Result<bool> {
        let id = resolve(
            &self.channels(),
            ch,
            &[ChannelKind::DigitalIn, ChannelKind::DigitalOut],
            "MCC 152",
        )?;
        self.hats.mcc152_dio_input_read_bit(self.address, u8::try_from(id).unwrap_or(0))
    }

    fn write(&mut self, ch: &ChannelRef, value: bool) -> Result<()> {
        let channels = self.channels();
        if resolve(&channels, ch, &[ChannelKind::DigitalIn], "MCC 152").is_ok() {
            return Err(InstrumentError::State(format!(
                "MCC 152 line {ch} is configured as an INPUT"
            )));
        }
        let id = resolve(&channels, ch, &[ChannelKind::DigitalOut], "MCC 152")?;
        self.hats.mcc152_dio_output_write_bit(self.address, u8::try_from(id).unwrap_or(0), value)
    }
}
