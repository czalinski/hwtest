//! The High-Precision AD/DA board as an instrument.
//!
//! ```yaml
//! driver: waveshare_adda   # legacy: hwtest_waveshare.high_precision_ad_da:create_instrument
//! kwargs:
//!   source_id: uut_adda
//!   adc_channels: [{id: 0, name: uut_in}, {id: 1, name: diff, differential_negative: 2}]
//!   dac_channels: [{id: 0, name: uut_out, initial_voltage: 0.0}]
//!   adc_gain: GAIN_1          # or 0-6
//!   adc_data_rate: SPS_100    # or the register code
//!   # optional: adc_vref, dac_vref, spi_bus, spi_device, adc_cs_pin, adc_drdy_pin,
//!   #           adc_reset_pin (null if not wired), dac_cs_pin, gpio_chip
//! ```

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{SystemTime, UNIX_EPOCH};

use hwtest_core::{DataType, InstrumentIdentity, StreamData, StreamField, StreamSchema};
use serde::Deserialize;
use serde_json::Value;

use super::ads1256::{Ads1256, Ads1256Config, DATA_RATES, GAINS};
use super::bus::SharedBus;
use super::dac8532::Dac8532;
use crate::daqhats::mcc118::validate_channels;
use crate::error::{InstrumentError, Result};
use crate::traits::{
    resolve, AnalogInput, AnalogOutput, ChannelInfo, ChannelKind, ChannelRef, Instrument,
    StreamSink, StreamSource,
};

/// ADC channel.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AdcChannel {
    /// Positive input 0-7.
    pub id: u32,
    /// Logical name.
    #[serde(alias = "logical_name")]
    pub name: String,
    /// Negative input for differential mode (else AINCOM).
    #[serde(default)]
    pub differential_negative: Option<u8>,
    /// Ignored (rack YAML compatibility).
    #[serde(default, rename = "type")]
    pub kind: Option<String>,
    /// Free-text note (ignored).
    #[serde(default)]
    pub description: Option<String>,
}

/// DAC channel.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DacChannel {
    /// 0 = A, 1 = B.
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
#[allow(missing_docs)]
pub struct Config {
    #[serde(default)]
    pub source_id: Option<String>,
    #[serde(default)]
    pub adc_channels: Vec<AdcChannel>,
    #[serde(default)]
    pub dac_channels: Vec<DacChannel>,
    #[serde(default = "gain1")]
    pub adc_gain: Value,
    #[serde(default = "sps100")]
    pub adc_data_rate: Value,
    #[serde(default = "v25")]
    pub adc_vref: f64,
    #[serde(default = "v5")]
    pub dac_vref: f64,
    #[serde(default)]
    pub spi_bus: u32,
    #[serde(default)]
    pub spi_device: u32,
    #[serde(default = "p22")]
    pub adc_cs_pin: u32,
    #[serde(default = "p17")]
    pub adc_drdy_pin: u32,
    #[serde(default = "p18")]
    pub adc_reset_pin: Option<u32>,
    #[serde(default = "p23")]
    pub dac_cs_pin: u32,
    #[serde(default = "chip0")]
    pub gpio_chip: String,
}

fn gain1() -> Value {
    Value::from("GAIN_1")
}
fn sps100() -> Value {
    Value::from("SPS_100")
}
fn v25() -> f64 {
    2.5
}
fn v5() -> f64 {
    5.0
}
fn p22() -> u32 {
    22
}
fn p17() -> u32 {
    17
}
#[allow(clippy::unnecessary_wraps)]
fn p18() -> Option<u32> {
    Some(18)
}
fn p23() -> u32 {
    23
}
fn chip0() -> String {
    "/dev/gpiochip0".into()
}

fn parse_code(v: &Value, table: &[(&str, u8)], what: &str) -> Result<u8> {
    let names: Vec<&str> = table.iter().map(|(n, _)| *n).collect();
    let bad = || InstrumentError::Config(format!("{what}: {v} is not one of {}", names.join(", ")));
    match v {
        Value::String(s) => table.iter().find(|(n, _)| n == s).map(|(_, c)| *c).ok_or_else(bad),
        Value::Number(n) => {
            let c = n.as_u64().and_then(|c| u8::try_from(c).ok()).ok_or_else(bad)?;
            table.iter().any(|(_, x)| *x == c).then_some(c).ok_or_else(bad)
        }
        _ => Err(bad()),
    }
}

impl Config {
    /// ADC settings from the config.
    ///
    /// # Errors
    /// Unknown gain or data rate.
    pub fn adc_config(&self) -> Result<Ads1256Config> {
        let rates: Vec<(&str, u8)> = DATA_RATES.iter().map(|(n, c, _)| (*n, *c)).collect();
        Ok(Ads1256Config {
            cs_pin: self.adc_cs_pin,
            drdy_pin: self.adc_drdy_pin,
            reset_pin: self.adc_reset_pin,
            gain: parse_code(&self.adc_gain, GAINS, "adc_gain")?,
            data_rate: parse_code(&self.adc_data_rate, &rates, "adc_data_rate")?,
            vref: self.adc_vref,
        })
    }
}

struct Chips {
    adc: Ads1256,
    dac: Dac8532,
}

/// The AD/DA board.
pub struct AdDa {
    chips: Arc<Mutex<Chips>>,
    config: Config,
    schema: StreamSchema,
    poll: Option<(Arc<AtomicBool>, JoinHandle<()>)>,
}

fn lock(c: &Arc<Mutex<Chips>>) -> std::sync::MutexGuard<'_, Chips> {
    c.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

impl AdDa {
    /// Opens the board on Linux SPI/GPIO.
    ///
    /// # Errors
    /// Invalid config or device failure.
    pub fn open(config: Config, name: &str) -> Result<Self> {
        let bus =
            super::bus::linux_bus(config.spi_bus, config.spi_device, 1_000_000, &config.gpio_chip)?;
        Self::with_bus(bus, config, name)
    }

    /// Opens the board on an existing bus (e.g. the emulator).
    ///
    /// # Errors
    /// Invalid config or device failure.
    pub fn with_bus(bus: SharedBus, mut config: Config, name: &str) -> Result<Self> {
        validate_channels(config.adc_channels.iter().map(|c| (c.id, c.name.as_str())), 7, "ADC")?;
        validate_channels(config.dac_channels.iter().map(|c| (c.id, c.name.as_str())), 1, "DAC")?;
        if let Some(c) =
            config.adc_channels.iter().find(|c| c.differential_negative.is_some_and(|n| n > 7))
        {
            return Err(InstrumentError::Config(format!(
                "differential_negative must be 0-7 for ADC channel {:?}",
                c.name
            )));
        }
        let names: Vec<&str> = config.adc_channels.iter().map(|c| c.name.as_str()).collect();
        if let Some(d) = config.dac_channels.iter().find(|d| names.contains(&d.name.as_str())) {
            return Err(InstrumentError::Config(format!("duplicate channel name: {}", d.name)));
        }
        for d in &config.dac_channels {
            if !(0.0..=config.dac_vref).contains(&d.initial_voltage) {
                return Err(InstrumentError::Config(format!(
                    "initial_voltage must be 0-{} V for DAC channel {:?}",
                    config.dac_vref, d.name
                )));
            }
        }
        let adc = Ads1256::open(bus.clone(), config.adc_config()?)?;
        let mut dac = Dac8532::open(bus, config.dac_cs_pin, config.dac_vref)?;
        for d in &config.dac_channels {
            dac.write_voltage(u8::try_from(d.id).unwrap_or(0), d.initial_voltage)?;
        }
        let source = config.source_id.get_or_insert_with(|| name.to_string()).clone();
        let schema = StreamSchema::new(
            source,
            config
                .adc_channels
                .iter()
                .map(|c| StreamField::new(c.name.clone(), DataType::F64, "V"))
                .collect(),
        );
        Ok(Self { chips: Arc::new(Mutex::new(Chips { adc, dac })), config, schema, poll: None })
    }

    fn read_adc(chips: &Chips, ch: &AdcChannel) -> Result<f64> {
        let pos = u8::try_from(ch.id).unwrap_or(0);
        match ch.differential_negative {
            Some(n) => chips.adc.read_differential(pos, n),
            None => chips.adc.read_voltage(pos),
        }
    }
}

impl Instrument for AdDa {
    fn model(&self) -> &'static str {
        "High-Precision AD/DA"
    }
    fn identity(&mut self) -> Result<InstrumentIdentity> {
        let id = lock(&self.chips).adc.chip_id()?;
        Ok(InstrumentIdentity {
            manufacturer: "Waveshare".into(),
            model: "High-Precision AD/DA".into(),
            serial: String::new(),
            firmware: format!("ADS1256 id {id}"),
        })
    }
    fn channels(&self) -> Vec<ChannelInfo> {
        let adc = self.config.adc_channels.iter().map(|c| ChannelInfo {
            name: c.name.clone(),
            id: c.id,
            kind: ChannelKind::AnalogIn,
            unit: "V",
        });
        let dac = self.config.dac_channels.iter().map(|c| ChannelInfo {
            name: c.name.clone(),
            id: c.id,
            kind: ChannelKind::AnalogOut,
            unit: "V",
        });
        adc.chain(dac).collect()
    }
    fn close(&mut self) -> Result<()> {
        let _ = self.stop_stream();
        let mut c = lock(&self.chips);
        c.dac.write_raw(0, 0)?;
        c.dac.write_raw(1, 0)
    }
    fn analog_in(&mut self) -> Option<&mut dyn AnalogInput> {
        Some(self)
    }
    fn analog_out(&mut self) -> Option<&mut dyn AnalogOutput> {
        Some(self)
    }
    fn stream(&mut self) -> Option<&mut dyn StreamSource> {
        Some(self)
    }
}

impl AnalogInput for AdDa {
    fn read(&mut self, ch: &ChannelRef) -> Result<f64> {
        let id = resolve(&self.channels(), ch, &[ChannelKind::AnalogIn], "AD/DA")?;
        let cfg = self.config.adc_channels.iter().find(|c| c.id == id).cloned().expect("resolved");
        Self::read_adc(&lock(&self.chips), &cfg)
    }
}

impl AnalogOutput for AdDa {
    fn write(&mut self, ch: &ChannelRef, value: f64) -> Result<()> {
        let id = resolve(&self.channels(), ch, &[ChannelKind::AnalogOut], "AD/DA")?;
        lock(&self.chips).dac.write_voltage(u8::try_from(id).unwrap_or(0), value)
    }
    fn last_written(&mut self, ch: &ChannelRef) -> Result<Option<f64>> {
        let id = resolve(&self.channels(), ch, &[ChannelKind::AnalogOut], "AD/DA")?;
        Ok(lock(&self.chips).dac.read_voltage(u8::try_from(id).unwrap_or(0)))
    }
}

impl StreamSource for AdDa {
    fn schema(&self) -> StreamSchema {
        self.schema.clone()
    }

    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss, clippy::cast_precision_loss)]
    fn start_stream(&mut self, mut sink: StreamSink) -> Result<f64> {
        if self.poll.is_some() {
            return Err(InstrumentError::State("AD/DA: already streaming".into()));
        }
        if self.config.adc_channels.is_empty() {
            return Err(InstrumentError::Config("AD/DA: no ADC channels to stream".into()));
        }
        let rate = lock(&self.chips).adc.sample_rate() / self.config.adc_channels.len() as f64;
        let stop = Arc::new(AtomicBool::new(false));
        let (chips, stop2, channels, schema_id) = (
            self.chips.clone(),
            stop.clone(),
            self.config.adc_channels.clone(),
            self.schema.schema_id(),
        );
        let handle = std::thread::spawn(move || {
            while !stop2.load(Ordering::Relaxed) {
                let ts =
                    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_nanos() as u64);
                let mut sample = Vec::with_capacity(channels.len());
                {
                    let c = lock(&chips);
                    for ch in &channels {
                        match Self::read_adc(&c, ch) {
                            Ok(v) => sample.push(v.into()),
                            Err(e) => {
                                tracing::error!("AD/DA stream: {e}");
                                return;
                            }
                        }
                    }
                }
                sink(StreamData {
                    schema_id,
                    timestamp_ns: ts,
                    period_ns: (1e9 / rate) as u64,
                    samples: vec![sample],
                });
            }
        });
        self.poll = Some((stop, handle));
        Ok(rate)
    }

    fn stop_stream(&mut self) -> Result<()> {
        if let Some((stop, h)) = self.poll.take() {
            stop.store(true, Ordering::Relaxed);
            let _ = h.join();
        }
        Ok(())
    }

    fn is_streaming(&self) -> bool {
        self.poll.is_some()
    }
}

impl Drop for AdDa {
    fn drop(&mut self) {
        let _ = self.stop_stream();
    }
}

#[cfg(test)]
mod tests {
    use super::super::emulator::{EmulatedAdDa, Pins};
    use super::*;
    use serde_json::json;

    #[allow(clippy::needless_pass_by_value)]
    fn board(extra: serde_json::Value) -> (AdDa, EmulatedAdDa) {
        let (emu, bus) = EmulatedAdDa::new(Pins::default());
        let mut cfg = json!({
            "source_id": "uut",
            "adc_channels": [{"id": 0, "name": "uut_in"}, {"id": 1, "name": "diff", "differential_negative": 2}],
            "dac_channels": [{"id": 0, "name": "uut_out", "initial_voltage": 1.0}],
        });
        cfg.as_object_mut().unwrap().extend(extra.as_object().unwrap().clone());
        (AdDa::with_bus(bus, serde_json::from_value(cfg).unwrap(), "uut").unwrap(), emu)
    }

    #[test]
    fn configures_chip_like_the_python_driver() {
        let (mut b, emu) = board(json!({"adc_gain": "GAIN_2", "adc_data_rate": "SPS_1000"}));
        assert_eq!(emu.register(2), 1, "ADCON gain");
        assert_eq!(emu.register(3), 0xA1, "DRATE");
        let log = emu.adc_log();
        assert_eq!(&log[..4], &[0x0F, 0x50, 0x00, 0x00], "SDATAC then WREG STATUS");
        assert!(log.contains(&0xF0), "SELFCAL issued");
        assert_eq!(b.identity().unwrap().firmware, "ADS1256 id 3");
        assert!((emu.dac_output(0) - 1.0).abs() < 1e-3, "initial voltage applied");
    }

    #[test]
    fn reads_single_ended_and_differential() {
        let (mut b, emu) = board(json!({}));
        emu.set_input(0, 1.234);
        emu.set_input(1, 2.0);
        emu.set_input(2, 0.5);
        assert!((b.read(&"uut_in".into()).unwrap() - 1.234).abs() < 1e-6);
        assert!((b.read(&"diff".into()).unwrap() - 1.5).abs() < 1e-6);
        emu.set_input(0, 4.0); // full scale is ±2·Vref = ±5 V
        assert!((b.read(&ChannelRef::Id(0)).unwrap() - 4.0).abs() < 1e-6);
        emu.set_input(0, -6.0); // beyond full scale clamps
        assert!((b.read(&ChannelRef::Id(0)).unwrap() + 5.0).abs() < 1e-6);
    }

    #[test]
    fn gain_scales_and_dac_loopback() {
        let (mut b, emu) = board(json!({"adc_gain": 2}));
        emu.connect(0, 0);
        b.write(&"uut_out".into(), 0.5).unwrap();
        assert!((b.read(&"uut_in".into()).unwrap() - 0.5).abs() < 1e-4);
        assert!((b.last_written(&"uut_out".into()).unwrap().unwrap() - 0.5).abs() < 1e-4);
        assert!(b.write(&"uut_out".into(), 6.0).is_err());
        assert!(b.write(&"uut_in".into(), 1.0).is_err(), "inputs are not outputs");
    }

    #[test]
    fn config_validation() {
        let (_, bus) = EmulatedAdDa::new(Pins::default());
        let bad: Config = serde_json::from_value(json!({"adc_gain": "GAIN_3"})).unwrap();
        let e = AdDa::with_bus(bus, bad, "x").err().unwrap().to_string();
        assert!(e.contains("adc_gain: \"GAIN_3\" is not one of GAIN_1, GAIN_2"), "{e}");
        assert!(serde_json::from_value::<Config>(json!({"adc_gian": 1})).is_err());
    }

    #[test]
    fn streams_samples() {
        let (mut b, emu) = board(json!({"adc_data_rate": "SPS_30000"}));
        emu.set_input(0, 0.25);
        let (tx, rx) = std::sync::mpsc::channel();
        let rate = b
            .start_stream(Box::new(move |d| {
                let _ = tx.send(d);
            }))
            .unwrap();
        assert!((rate - 15000.0).abs() < 1e-9);
        let d = rx.recv_timeout(std::time::Duration::from_secs(2)).unwrap();
        b.stop_stream().unwrap();
        assert!((d.samples[0][0].as_f64() - 0.25).abs() < 1e-6);
        assert_eq!(d.schema_id, b.schema().schema_id());
    }
}
