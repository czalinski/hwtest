//! MCC 134: 4-channel thermocouple DAQ.
//!
//! ```yaml
//! driver: mcc134      # legacy: hwtest_mcc.mcc134:create_instrument
//! kwargs: {address: 1, source_id: thermocouple_daq, update_interval: 1.0,
//!          channels: [{id: 0, name: tc_0, tc_type: TYPE_K}]}
//! ```
//! Single reads of an open / over-range / common-mode thermocouple return an
//! error naming the condition; in streams those samples are NaN (which fails
//! every bound check).

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use hwtest_core::{DataType, InstrumentIdentity, StreamData, StreamField, StreamSchema};
use serde::Deserialize;

use super::mcc118::validate_channels;
use super::{check_address, tc_values, Backend};
use crate::error::{InstrumentError, Result};
use crate::traits::{
    resolve, AnalogInput, ChannelInfo, ChannelKind, ChannelRef, Instrument, StreamSink,
    StreamSource,
};

/// Thermocouple types (`TYPE_J` .. `TYPE_N`, or `DISABLED`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Default)]
#[allow(missing_docs)]
pub enum TcType {
    #[serde(rename = "TYPE_J")]
    J,
    #[default]
    #[serde(rename = "TYPE_K")]
    K,
    #[serde(rename = "TYPE_T")]
    T,
    #[serde(rename = "TYPE_E")]
    E,
    #[serde(rename = "TYPE_R")]
    R,
    #[serde(rename = "TYPE_S")]
    S,
    #[serde(rename = "TYPE_B")]
    B,
    #[serde(rename = "TYPE_N")]
    N,
    #[serde(rename = "DISABLED")]
    Disabled,
}

impl TcType {
    /// libdaqhats code.
    pub fn code(self) -> u8 {
        match self {
            TcType::J => 0,
            TcType::K => 1,
            TcType::T => 2,
            TcType::E => 3,
            TcType::R => 4,
            TcType::S => 5,
            TcType::B => 6,
            TcType::N => 7,
            TcType::Disabled => 255,
        }
    }
}

/// A channel.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Channel {
    /// Physical channel 0-3.
    pub id: u32,
    /// Logical name.
    #[serde(alias = "logical_name")]
    pub name: String,
    /// Thermocouple type (default `TYPE_K`).
    #[serde(default)]
    pub tc_type: TcType,
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
    /// Channels.
    pub channels: Vec<Channel>,
    /// Stream source id (defaults to the instrument name).
    #[serde(default)]
    pub source_id: Option<String>,
    /// Seconds between stream samples.
    #[serde(default = "one")]
    pub update_interval: f64,
}

fn one() -> f64 {
    1.0
}

fn special(v: f64) -> Option<&'static str> {
    #[allow(clippy::float_cmp)]
    match v {
        x if x == tc_values::OPEN => Some("open thermocouple"),
        x if x == tc_values::OVERRANGE => Some("thermocouple over range"),
        x if x == tc_values::COMMON_MODE => Some("thermocouple common-mode error"),
        _ => None,
    }
}

/// An MCC 134.
pub struct Mcc134 {
    hats: Backend,
    address: u8,
    config: Config,
    schema: StreamSchema,
    poll: Option<(Arc<AtomicBool>, JoinHandle<()>)>,
}

impl Mcc134 {
    /// Opens the board and configures thermocouple types.
    ///
    /// # Errors
    /// Invalid config or the board cannot be opened.
    pub fn open(hats: Backend, mut config: Config, name: &str) -> Result<Self> {
        let address = check_address(config.address)?;
        if config.channels.is_empty() {
            return Err(InstrumentError::Config("channels must not be empty".into()));
        }
        if config.update_interval <= 0.0 {
            return Err(InstrumentError::Config(format!(
                "update_interval must be > 0, got {}",
                config.update_interval
            )));
        }
        validate_channels(config.channels.iter().map(|c| (c.id, c.name.as_str())), 3, "MCC 134")?;
        let source = config.source_id.get_or_insert_with(|| name.to_string()).clone();
        let schema = StreamSchema::new(
            source,
            config
                .channels
                .iter()
                .map(|c| StreamField::new(c.name.clone(), DataType::F64, "degC"))
                .collect(),
        );
        hats.mcc134_open(address)?;
        for c in &config.channels {
            hats.mcc134_tc_type_write(address, u8::try_from(c.id).unwrap_or(0), c.tc_type.code())?;
        }
        Ok(Self { hats, address, config, schema, poll: None })
    }
}

impl Instrument for Mcc134 {
    fn model(&self) -> &'static str {
        "MCC 134"
    }
    fn identity(&mut self) -> Result<InstrumentIdentity> {
        Ok(InstrumentIdentity {
            manufacturer: "Measurement Computing".into(),
            model: "MCC 134".into(),
            serial: self.hats.mcc134_serial(self.address)?,
            firmware: String::new(),
        })
    }
    fn channels(&self) -> Vec<ChannelInfo> {
        self.config
            .channels
            .iter()
            .map(|c| ChannelInfo {
                name: c.name.clone(),
                id: c.id,
                kind: ChannelKind::Temperature,
                unit: "degC",
            })
            .collect()
    }
    fn close(&mut self) -> Result<()> {
        let _ = self.stop_stream();
        self.hats.mcc134_close(self.address)
    }
    fn analog_in(&mut self) -> Option<&mut dyn AnalogInput> {
        Some(self)
    }
    fn stream(&mut self) -> Option<&mut dyn StreamSource> {
        Some(self)
    }
}

impl AnalogInput for Mcc134 {
    fn read(&mut self, ch: &ChannelRef) -> Result<f64> {
        let id = resolve(&self.channels(), ch, &[ChannelKind::Temperature], "MCC 134")?;
        let v = self.hats.mcc134_t_in_read(self.address, u8::try_from(id).unwrap_or(0))?;
        match special(v) {
            Some(what) => Err(InstrumentError::Instrument(format!(
                "MCC 134 at {} channel {ch}: {what}",
                self.address
            ))),
            None => Ok(v),
        }
    }
}

impl StreamSource for Mcc134 {
    fn schema(&self) -> StreamSchema {
        self.schema.clone()
    }

    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    fn start_stream(&mut self, mut sink: StreamSink) -> Result<f64> {
        if self.poll.is_some() {
            return Err(InstrumentError::State("MCC 134: already streaming".into()));
        }
        let stop = Arc::new(AtomicBool::new(false));
        let (hats, address, stop2) = (self.hats.clone(), self.address, stop.clone());
        let ids: Vec<u8> =
            self.config.channels.iter().map(|c| u8::try_from(c.id).unwrap_or(0)).collect();
        let interval = Duration::from_secs_f64(self.config.update_interval);
        let schema_id = self.schema.schema_id();
        let handle = std::thread::Builder::new()
            .name(format!("mcc134-{address}"))
            .spawn(move || {
                let mut next = Instant::now();
                while !stop2.load(Ordering::Relaxed) {
                    let ts = SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .map_or(0, |d| d.as_nanos() as u64);
                    let mut sample = Vec::with_capacity(ids.len());
                    for ch in &ids {
                        match hats.mcc134_t_in_read(address, *ch) {
                            Ok(v) if special(v).is_some() => sample.push(f64::NAN.into()),
                            Ok(v) => sample.push(v.into()),
                            Err(e) => {
                                tracing::error!("MCC 134 at {address} channel {ch}: {e}");
                                return;
                            }
                        }
                    }
                    sink(StreamData {
                        schema_id,
                        timestamp_ns: ts,
                        period_ns: interval.as_nanos() as u64,
                        samples: vec![sample],
                    });
                    next += interval;
                    // Sleep in short steps so stop is responsive.
                    while !stop2.load(Ordering::Relaxed) && Instant::now() < next {
                        std::thread::sleep(
                            next.saturating_duration_since(Instant::now())
                                .min(Duration::from_millis(50)),
                        );
                    }
                }
            })
            .map_err(|e| InstrumentError::Comms(format!("cannot start poll thread: {e}")))?;
        self.poll = Some((stop, handle));
        Ok(1.0 / self.config.update_interval)
    }

    fn stop_stream(&mut self) -> Result<()> {
        if let Some((stop, handle)) = self.poll.take() {
            stop.store(true, Ordering::Relaxed);
            let _ = handle.join();
        }
        Ok(())
    }

    fn is_streaming(&self) -> bool {
        self.poll.is_some()
    }
}

impl Drop for Mcc134 {
    fn drop(&mut self) {
        let _ = self.stop_stream();
    }
}
