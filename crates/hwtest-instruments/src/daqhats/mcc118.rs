//! MCC 118: 8-channel ±10 V voltage DAQ (100 kS/s aggregate).
//!
//! ```yaml
//! driver: mcc118      # legacy: hwtest_mcc.mcc118:create_instrument
//! kwargs: {address: 4, sample_rate: 1000.0, source_id: voltage_daq,
//!          channels: [{id: 0, name: dut_voltage}]}
//! ```

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::{SystemTime, UNIX_EPOCH};

use hwtest_core::{DataType, InstrumentIdentity, StreamData, StreamField, StreamSchema};
use serde::Deserialize;

use super::{check_address, status, Backend, OPTS_CONTINUOUS};
use crate::error::{InstrumentError, Result};
use crate::traits::{
    resolve, AnalogInput, ChannelInfo, ChannelKind, ChannelRef, Instrument, StreamSink,
    StreamSource,
};

/// A channel.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Channel {
    /// Physical channel 0-7.
    pub id: u32,
    /// Logical name (stream field name).
    #[serde(alias = "logical_name")]
    pub name: String,
    /// Ignored (accepted for rack YAML compatibility).
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
    /// Per-channel scan rate (S/s).
    #[serde(default = "default_rate")]
    pub sample_rate: f64,
    /// Channels.
    pub channels: Vec<Channel>,
    /// Stream source id (defaults to the instrument name).
    #[serde(default)]
    pub source_id: Option<String>,
}

fn default_rate() -> f64 {
    1000.0
}

pub(crate) fn validate_channels<'a>(
    chs: impl Iterator<Item = (u32, &'a str)>,
    max: u32,
    what: &str,
) -> Result<()> {
    let mut ids = Vec::new();
    let mut names = Vec::new();
    for (id, name) in chs {
        if id > max {
            return Err(InstrumentError::Config(format!(
                "{what} channel id must be 0-{max}, got {id}"
            )));
        }
        if ids.contains(&id) {
            return Err(InstrumentError::Config(format!("duplicate {what} channel id: {id}")));
        }
        if names.contains(&name) {
            return Err(InstrumentError::Config(format!("duplicate channel name: {name}")));
        }
        ids.push(id);
        names.push(name);
    }
    Ok(())
}

struct ScanThread {
    stop: Arc<AtomicBool>,
    handle: JoinHandle<()>,
}

/// An MCC 118.
pub struct Mcc118 {
    hats: Backend,
    address: u8,
    config: Config,
    schema: StreamSchema,
    scan: Option<ScanThread>,
}

impl Mcc118 {
    /// Opens the board.
    ///
    /// # Errors
    /// Invalid config or the board cannot be opened.
    pub fn open(hats: Backend, mut config: Config, name: &str) -> Result<Self> {
        let address = check_address(config.address)?;
        if config.channels.is_empty() {
            return Err(InstrumentError::Config("channels must not be empty".into()));
        }
        if config.sample_rate <= 0.0 {
            return Err(InstrumentError::Config(format!(
                "sample_rate must be > 0, got {}",
                config.sample_rate
            )));
        }
        validate_channels(config.channels.iter().map(|c| (c.id, c.name.as_str())), 7, "MCC 118")?;
        let source = config.source_id.get_or_insert_with(|| name.to_string()).clone();
        let schema = StreamSchema::new(
            source,
            config
                .channels
                .iter()
                .map(|c| StreamField::new(c.name.clone(), DataType::F64, "V"))
                .collect(),
        );
        hats.mcc118_open(address)?;
        Ok(Self { hats, address, config, schema, scan: None })
    }

    fn mask(&self) -> u8 {
        self.config.channels.iter().fold(0u8, |m, c| m | (1 << c.id))
    }
}

impl Instrument for Mcc118 {
    fn model(&self) -> &'static str {
        "MCC 118"
    }
    fn identity(&mut self) -> Result<InstrumentIdentity> {
        Ok(InstrumentIdentity {
            manufacturer: "Measurement Computing".into(),
            model: "MCC 118".into(),
            serial: self.hats.mcc118_serial(self.address)?,
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
                kind: ChannelKind::AnalogIn,
                unit: "V",
            })
            .collect()
    }
    fn close(&mut self) -> Result<()> {
        let _ = self.stop_stream();
        self.hats.mcc118_close(self.address)
    }
    fn analog_in(&mut self) -> Option<&mut dyn AnalogInput> {
        Some(self)
    }
    fn stream(&mut self) -> Option<&mut dyn StreamSource> {
        Some(self)
    }
}

impl AnalogInput for Mcc118 {
    fn read(&mut self, ch: &ChannelRef) -> Result<f64> {
        if self.scan.is_some() {
            return Err(InstrumentError::State(
                "MCC 118: cannot do a single read while streaming".into(),
            ));
        }
        let id = match ch {
            ChannelRef::Id(i) if *i <= 7 => *i,
            _ => resolve(&self.channels(), ch, &[ChannelKind::AnalogIn], "MCC 118")?,
        };
        self.hats.mcc118_a_in_read(self.address, u8::try_from(id).unwrap_or(0))
    }
}

#[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss, clippy::cast_precision_loss)]
fn scan_loop(
    hats: &Backend,
    address: u8,
    n_ch: usize,
    rate: f64,
    schema_id: u32,
    stop: &AtomicBool,
    mut sink: StreamSink,
) {
    let batch = ((rate / 10.0) as usize).max(1);
    let period_ns = (1e9 / rate) as u64;
    let timeout = batch as f64 / rate * 2.0 + 1.0;
    let start_ns = SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_nanos() as u64);
    let mut total: u64 = 0;
    let mut buf = vec![0.0f64; batch * n_ch];
    while !stop.load(Ordering::Relaxed) {
        let r = match hats.mcc118_scan_read(
            address,
            i32::try_from(batch).unwrap_or(i32::MAX),
            timeout,
            &mut buf,
        ) {
            Ok(r) => r,
            Err(e) => {
                tracing::error!("MCC 118 at {address}: scan read failed: {e}");
                break;
            }
        };
        if r.status & status::HW_OVERRUN != 0 {
            tracing::warn!("MCC 118 at {address}: hardware overrun (samples lost)");
        }
        if r.status & status::BUFFER_OVERRUN != 0 {
            tracing::warn!("MCC 118 at {address}: buffer overrun (samples lost)");
        }
        let n = r.samples_per_channel as usize;
        if n > 0 {
            let samples = buf[..n * n_ch]
                .chunks(n_ch)
                .map(|c| c.iter().map(|v| (*v).into()).collect())
                .collect();
            sink(StreamData {
                schema_id,
                timestamp_ns: start_ns + total * period_ns,
                period_ns,
                samples,
            });
            total += n as u64;
        }
        if r.status & status::RUNNING == 0 {
            break;
        }
    }
}

impl StreamSource for Mcc118 {
    fn schema(&self) -> StreamSchema {
        self.schema.clone()
    }

    fn start_stream(&mut self, sink: StreamSink) -> Result<f64> {
        if self.scan.is_some() {
            return Err(InstrumentError::State("MCC 118: already streaming".into()));
        }
        let n = self.config.channels.len();
        let rate = self
            .hats
            .mcc118_scan_actual_rate(u8::try_from(n).unwrap_or(8), self.config.sample_rate)?;
        self.hats.mcc118_scan_start(
            self.address,
            self.mask(),
            0,
            self.config.sample_rate,
            OPTS_CONTINUOUS,
        )?;
        let stop = Arc::new(AtomicBool::new(false));
        let (hats, address, schema_id, stop2) =
            (self.hats.clone(), self.address, self.schema.schema_id(), stop.clone());
        let handle = std::thread::Builder::new()
            .name(format!("mcc118-{address}"))
            .spawn(move || scan_loop(&hats, address, n, rate, schema_id, &stop2, sink))
            .map_err(|e| InstrumentError::Comms(format!("cannot start scan thread: {e}")))?;
        self.scan = Some(ScanThread { stop, handle });
        Ok(rate)
    }

    fn stop_stream(&mut self) -> Result<()> {
        let Some(scan) = self.scan.take() else { return Ok(()) };
        scan.stop.store(true, Ordering::Relaxed);
        let stopped = self.hats.mcc118_scan_stop(self.address);
        let _ = scan.handle.join();
        self.hats.mcc118_scan_cleanup(self.address)?;
        stopped
    }

    fn is_streaming(&self) -> bool {
        self.scan.is_some()
    }
}

impl Drop for Mcc118 {
    fn drop(&mut self) {
        let _ = self.stop_stream();
    }
}
