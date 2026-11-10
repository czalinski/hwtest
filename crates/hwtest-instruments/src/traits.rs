//! The instrument model shared by all drivers.

use hwtest_core::{InstrumentIdentity, StreamData, StreamSchema};

use crate::error::{InstrumentError, Result};

/// Refers to a channel by its logical name or its number on the instrument.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum ChannelRef {
    /// Channel number.
    Id(u32),
    /// Logical name from the rack YAML.
    Name(String),
}

impl std::fmt::Display for ChannelRef {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ChannelRef::Id(i) => write!(f, "{i}"),
            ChannelRef::Name(n) => write!(f, "{n:?}"),
        }
    }
}

impl From<u32> for ChannelRef {
    fn from(v: u32) -> Self {
        ChannelRef::Id(v)
    }
}

impl From<&str> for ChannelRef {
    fn from(v: &str) -> Self {
        ChannelRef::Name(v.to_string())
    }
}

impl From<String> for ChannelRef {
    fn from(v: String) -> Self {
        ChannelRef::Name(v)
    }
}

/// What a channel does.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ChannelKind {
    /// Analog input (volts).
    AnalogIn,
    /// Analog output (volts).
    AnalogOut,
    /// Temperature input (°C).
    Temperature,
    /// Digital input.
    DigitalIn,
    /// Digital output.
    DigitalOut,
    /// Power supply output.
    Psu,
}

/// A configured channel.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct ChannelInfo {
    /// Logical name.
    pub name: String,
    /// Channel number on the instrument.
    pub id: u32,
    /// Function.
    pub kind: ChannelKind,
    /// Unit (`V`, `degC`, ...).
    pub unit: &'static str,
}

/// Resolves a [`ChannelRef`] against configured channels of the given kinds.
///
/// # Errors
/// [`InstrumentError::UnknownChannel`] if no channel matches.
pub fn resolve(
    channels: &[ChannelInfo],
    ch: &ChannelRef,
    kinds: &[ChannelKind],
    instrument: &str,
) -> Result<u32> {
    let found = channels.iter().filter(|c| kinds.contains(&c.kind)).find(|c| match ch {
        ChannelRef::Id(i) => c.id == *i,
        ChannelRef::Name(n) => &c.name == n,
    });
    found.map(|c| c.id).ok_or_else(|| {
        let names: Vec<&str> =
            channels.iter().filter(|c| kinds.contains(&c.kind)).map(|c| c.name.as_str()).collect();
        InstrumentError::UnknownChannel(format!(
            "{ch} on {instrument} (configured: {})",
            names.join(", ")
        ))
    })
}

/// Reads analog values (volts or °C).
pub trait AnalogInput {
    /// Reads one channel.
    ///
    /// # Errors
    /// Unknown channel, invalid state (e.g. streaming) or communication failure.
    fn read(&mut self, ch: &ChannelRef) -> Result<f64>;
}

/// Drives analog outputs.
pub trait AnalogOutput {
    /// Sets an output voltage.
    ///
    /// # Errors
    /// Unknown channel, out-of-range value or communication failure.
    fn write(&mut self, ch: &ChannelRef, value: f64) -> Result<()>;

    /// The last value written, if any.
    ///
    /// # Errors
    /// Unknown channel.
    fn last_written(&mut self, ch: &ChannelRef) -> Result<Option<f64>>;
}

/// Digital input/output lines.
pub trait DigitalIo {
    /// Reads a line.
    ///
    /// # Errors
    /// Unknown channel or communication failure.
    fn read(&mut self, ch: &ChannelRef) -> Result<bool>;

    /// Drives an output line.
    ///
    /// # Errors
    /// Unknown channel, line is an input, or communication failure.
    fn write(&mut self, ch: &ChannelRef, value: bool) -> Result<()>;
}

/// Programmable power supply channels.
#[allow(missing_docs)]
pub trait PowerSupply {
    /// Sets the voltage setpoint.
    ///
    /// # Errors
    /// Limit exceeded or communication failure.
    fn set_voltage(&mut self, ch: &ChannelRef, volts: f64) -> Result<()>;
    /// Sets the current limit.
    ///
    /// # Errors
    /// Limit exceeded or communication failure.
    fn set_current_limit(&mut self, ch: &ChannelRef, amps: f64) -> Result<()>;
    /// Enables or disables the output.
    ///
    /// # Errors
    /// Communication failure.
    fn set_output(&mut self, ch: &ChannelRef, on: bool) -> Result<()>;
    /// Voltage and current limit at once.
    ///
    /// # Errors
    /// Limit exceeded or communication failure.
    fn apply(&mut self, ch: &ChannelRef, volts: f64, amps: f64) -> Result<()>;
    /// # Errors
    /// Communication failure.
    fn voltage_setpoint(&mut self, ch: &ChannelRef) -> Result<f64>;
    /// # Errors
    /// Communication failure.
    fn current_limit(&mut self, ch: &ChannelRef) -> Result<f64>;
    /// # Errors
    /// Communication failure.
    fn output_enabled(&mut self, ch: &ChannelRef) -> Result<bool>;
    /// # Errors
    /// Communication failure.
    fn measure_voltage(&mut self, ch: &ChannelRef) -> Result<f64>;
    /// # Errors
    /// Communication failure.
    fn measure_current(&mut self, ch: &ChannelRef) -> Result<f64>;
    /// # Errors
    /// Communication failure.
    fn measure_power(&mut self, ch: &ChannelRef) -> Result<f64>;
}

/// Receives stream batches from a background acquisition thread.
pub type StreamSink = Box<dyn FnMut(StreamData) + Send>;

/// Continuous acquisition.
pub trait StreamSource {
    /// The stream layout (field names are logical channel names).
    fn schema(&self) -> StreamSchema;

    /// Starts acquiring; batches go to `sink`. Returns the actual sample rate.
    ///
    /// # Errors
    /// Already streaming or communication failure.
    fn start_stream(&mut self, sink: StreamSink) -> Result<f64>;

    /// Stops acquiring.
    ///
    /// # Errors
    /// Communication failure.
    fn stop_stream(&mut self) -> Result<()>;

    /// True while acquiring.
    fn is_streaming(&self) -> bool;
}

/// A connected instrument.
pub trait Instrument: Send {
    /// Model name, e.g. `MCC 118`.
    fn model(&self) -> &str;

    /// Queries the instrument's identity.
    ///
    /// # Errors
    /// Communication failure.
    fn identity(&mut self) -> Result<InstrumentIdentity>;

    /// Configured channels.
    fn channels(&self) -> Vec<ChannelInfo>;

    /// Releases the hardware (outputs to safe state where applicable).
    ///
    /// # Errors
    /// Communication failure.
    fn close(&mut self) -> Result<()> {
        Ok(())
    }

    /// Analog inputs, if supported.
    fn analog_in(&mut self) -> Option<&mut dyn AnalogInput> {
        None
    }
    /// Analog outputs, if supported.
    fn analog_out(&mut self) -> Option<&mut dyn AnalogOutput> {
        None
    }
    /// Digital I/O, if supported.
    fn digital_io(&mut self) -> Option<&mut dyn DigitalIo> {
        None
    }
    /// Power supply channels, if supported.
    fn power_supply(&mut self) -> Option<&mut dyn PowerSupply> {
        None
    }
    /// Streaming, if supported.
    fn stream(&mut self) -> Option<&mut dyn StreamSource> {
        None
    }
}

/// `Unsupported` error helper.
pub fn unsupported(instrument: &str, capability: &'static str) -> InstrumentError {
    InstrumentError::Unsupported { instrument: instrument.to_string(), capability }
}
