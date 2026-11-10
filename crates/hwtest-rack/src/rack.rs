//! The rack: instruments, identity checks, logical channels, calibration.

use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use hwtest_core::InstrumentIdentity;
use hwtest_instruments::{
    open_instrument, ChannelKind, ChannelRef, DriverEnv, Instrument, InstrumentError,
};
use hwtest_spec::rack::{InstrumentConfig, LogicalChannel};
use hwtest_spec::{RackConfig, RackInstance};
use indexmap::IndexMap;
use serde::Serialize;

/// Rack error. Messages name the logical channel / instrument.
#[derive(Debug, thiserror::Error)]
pub enum RackError {
    /// No such logical channel.
    #[error("unknown channel {name:?} (rack channels: {available})")]
    UnknownChannel {
        /// Requested name.
        name: String,
        /// Configured names.
        available: String,
    },
    /// The instrument is not ready (failed to open or identity mismatch).
    #[error("instrument {instrument:?} for channel {channel:?} is not ready: {reason}")]
    NotReady {
        /// Instrument name.
        instrument: String,
        /// Channel name.
        channel: String,
        /// Why.
        reason: String,
    },
    /// The instrument cannot do this on that channel.
    #[error("channel {channel:?} ({kind}) does not support {operation}")]
    WrongKind {
        /// Channel name.
        channel: String,
        /// Channel kind.
        kind: String,
        /// Operation attempted.
        operation: &'static str,
    },
    /// Driver error.
    #[error("{context}: {source}")]
    Instrument {
        /// Channel/instrument.
        context: String,
        /// Cause.
        source: InstrumentError,
    },
}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, RackError>;

/// Lifecycle state of an instrument.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum InstrumentState {
    /// Not yet opened.
    Pending,
    /// Opened and identity verified.
    Ready,
    /// Failed to open or wrong identity.
    Error,
    /// Closed.
    Closed,
}

/// Identity as reported by the instrument.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct IdentityModel {
    /// Manufacturer.
    pub manufacturer: String,
    /// Model.
    pub model: String,
    /// Serial number.
    pub serial: String,
    /// Firmware.
    pub firmware: String,
}

impl From<&InstrumentIdentity> for IdentityModel {
    fn from(i: &InstrumentIdentity) -> Self {
        Self {
            manufacturer: i.manufacturer.clone(),
            model: i.model.clone(),
            serial: i.serial.clone(),
            firmware: i.firmware.clone(),
        }
    }
}

/// Status of one instrument (same fields as the Python `InstrumentStatus`).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct InstrumentStatus {
    /// Instrument name.
    pub name: String,
    /// Driver as written in the YAML.
    pub driver: String,
    /// State.
    pub state: InstrumentState,
    /// Expected manufacturer.
    pub expected_manufacturer: String,
    /// Expected model.
    pub expected_model: String,
    /// Reported identity.
    pub identity: Option<IdentityModel>,
    /// Error, if any.
    pub error: Option<String>,
}

/// Status of the rack.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct RackStatus {
    /// Rack class id.
    pub rack_id: String,
    /// Description.
    pub description: String,
    /// `ready`, `error` or `closed`.
    pub state: String,
    /// Rack instance serial, if calibration was loaded.
    pub serial: Option<String>,
    /// True when running against emulators.
    pub emulated: bool,
    /// Instruments.
    pub instruments: Vec<InstrumentStatus>,
}

/// A shared, lockable instrument.
pub type SharedInstrument = Arc<Mutex<Box<dyn Instrument>>>;

struct Managed {
    config: InstrumentConfig,
    state: InstrumentState,
    instrument: Option<SharedInstrument>,
    identity: Option<InstrumentIdentity>,
    error: Option<String>,
}

/// An initialised rack.
pub struct Rack {
    config: RackConfig,
    instance: Option<RackInstance>,
    instruments: IndexMap<String, Managed>,
    env: DriverEnv,
    closed: bool,
}

fn lock(i: &SharedInstrument) -> MutexGuard<'_, Box<dyn Instrument>> {
    i.lock().unwrap_or_else(PoisonError::into_inner)
}

impl Rack {
    /// Opens and verifies every instrument. Instruments that fail are kept in
    /// the `Error` state (see [`Rack::status`]); the rest are usable.
    pub fn open(config: RackConfig, instance: Option<RackInstance>, mut env: DriverEnv) -> Self {
        let mut instruments = IndexMap::new();
        for (name, cfg) in &config.instruments {
            let mut m = Managed {
                config: cfg.clone(),
                state: InstrumentState::Pending,
                instrument: None,
                identity: None,
                error: None,
            };
            match open_instrument(name, &cfg.driver, &cfg.kwargs, &mut env) {
                Ok(inst) => Self::verify(&mut m, inst),
                Err(e) => {
                    tracing::error!("instrument {name}: {e}");
                    m.state = InstrumentState::Error;
                    m.error = Some(e.to_string());
                }
            }
            instruments.insert(name.clone(), m);
        }
        Self { config, instance, instruments, env, closed: false }
    }

    fn verify(m: &mut Managed, mut inst: Box<dyn Instrument>) {
        let expected = &m.config.identity;
        match inst.identity() {
            Ok(id) if id.manufacturer != expected.manufacturer => {
                m.error = Some(format!(
                    "manufacturer mismatch: expected {:?}, got {:?}",
                    expected.manufacturer, id.manufacturer
                ));
                m.identity = Some(id);
            }
            Ok(id) if id.model != expected.model => {
                m.error = Some(format!(
                    "model mismatch: expected {:?}, got {:?}",
                    expected.model, id.model
                ));
                m.identity = Some(id);
            }
            Ok(id) => {
                tracing::info!(
                    "instrument {}: {} {} (S/N {})",
                    m.config.name,
                    id.manufacturer,
                    id.model,
                    id.serial
                );
                m.identity = Some(id);
                m.state = InstrumentState::Ready;
                m.instrument = Some(Arc::new(Mutex::new(inst)));
                return;
            }
            Err(e) => m.error = Some(format!("identity query failed: {e}")),
        }
        tracing::error!("instrument {}: {}", m.config.name, m.error.as_deref().unwrap_or(""));
        m.state = InstrumentState::Error;
        let _ = inst.close();
    }

    /// Adds an instrument opened elsewhere (e.g. a Python escape-hatch driver
    /// wrapped by the SDK) in place of the configured entry, verifying identity.
    ///
    /// # Errors
    /// [`RackError::UnknownChannel`] if `name` is not an instrument of this rack.
    pub fn attach(&mut self, name: &str, inst: Box<dyn Instrument>) -> Result<InstrumentState> {
        let names = self.instruments.keys().cloned().collect::<Vec<_>>().join(", ");
        let m = self
            .instruments
            .get_mut(name)
            .ok_or_else(|| RackError::UnknownChannel { name: name.into(), available: names })?;
        m.error = None;
        Self::verify(m, inst);
        Ok(m.state)
    }

    /// The rack class configuration.
    pub fn config(&self) -> &RackConfig {
        &self.config
    }

    /// Rack id.
    pub fn rack_id(&self) -> &str {
        &self.config.rack_id
    }

    /// The driver environment (emulator handles in emulation mode).
    pub fn env(&self) -> &DriverEnv {
        &self.env
    }

    /// The loaded calibration instance.
    pub fn instance(&self) -> Option<&RackInstance> {
        self.instance.as_ref()
    }

    /// A calibration factor: instance file first, then the rack class
    /// `calibration:` section, else `default`.
    pub fn calibration(&self, name: &str, default: f64) -> f64 {
        self.instance
            .as_ref()
            .and_then(|i| i.calibration.get(name).copied())
            .or_else(|| self.config.calibration.get(name).copied())
            .unwrap_or(default)
    }

    /// True if every instrument is ready.
    pub fn is_ready(&self) -> bool {
        self.instruments.values().all(|m| m.state == InstrumentState::Ready)
    }

    /// Full status.
    pub fn status(&self) -> RackStatus {
        let state = if self.closed {
            "closed"
        } else if self.is_ready() {
            "ready"
        } else {
            "error"
        };
        RackStatus {
            rack_id: self.config.rack_id.clone(),
            description: self.config.description.clone(),
            state: state.into(),
            serial: self.instance.as_ref().map(|i| i.instance.serial_number.clone()),
            emulated: self.env.is_emulated(),
            instruments: self
                .instruments
                .keys()
                .filter_map(|n| self.instrument_status(n))
                .collect(),
        }
    }

    /// Status of one instrument.
    pub fn instrument_status(&self, name: &str) -> Option<InstrumentStatus> {
        let m = self.instruments.get(name)?;
        Some(InstrumentStatus {
            name: name.to_string(),
            driver: m.config.driver_spec.clone(),
            state: m.state,
            expected_manufacturer: m.config.identity.manufacturer.clone(),
            expected_model: m.config.identity.model.clone(),
            identity: m.identity.as_ref().map(IdentityModel::from),
            error: m.error.clone(),
        })
    }

    /// A ready instrument by name.
    pub fn instrument(&self, name: &str) -> Option<SharedInstrument> {
        self.instruments.get(name).and_then(|m| m.instrument.clone())
    }

    /// Instrument names.
    pub fn instrument_names(&self) -> Vec<String> {
        self.instruments.keys().cloned().collect()
    }

    /// All logical channels.
    pub fn channels(&self) -> Vec<LogicalChannel> {
        self.config.channels().cloned().collect()
    }

    fn channel(&self, name: &str) -> Result<(SharedInstrument, LogicalChannel)> {
        let ch = self.config.channel(name).cloned().ok_or_else(|| RackError::UnknownChannel {
            name: name.to_string(),
            available: self
                .config
                .channels()
                .map(|c| c.logical_name.as_str())
                .collect::<Vec<_>>()
                .join(", "),
        })?;
        let m = &self.instruments[&ch.instrument];
        let inst = m.instrument.clone().ok_or_else(|| RackError::NotReady {
            instrument: ch.instrument.clone(),
            channel: name.to_string(),
            reason: m.error.clone().unwrap_or_else(|| format!("{:?}", m.state).to_lowercase()),
        })?;
        Ok((inst, ch))
    }

    /// The kind of a logical channel, as reported by its (ready) instrument.
    pub fn channel_kind(&self, name: &str) -> Option<ChannelKind> {
        let (inst, _) = self.channel(name).ok()?;
        let i = lock(&inst);
        Self::kind_of(i.as_ref(), name)
    }

    fn kind_of(inst: &dyn Instrument, name: &str) -> Option<ChannelKind> {
        inst.channels().into_iter().find(|c| c.name == name).map(|c| c.kind)
    }

    fn wrap<T>(name: &str, r: std::result::Result<T, InstrumentError>) -> Result<T> {
        r.map_err(|source| RackError::Instrument { context: format!("channel {name:?}"), source })
    }

    fn wrong(name: &str, inst: &dyn Instrument, operation: &'static str) -> RackError {
        RackError::WrongKind {
            channel: name.to_string(),
            kind: Self::kind_of(inst, name).map_or_else(|| "unknown".into(), |k| format!("{k:?}")),
            operation,
        }
    }

    /// Reads an analog or temperature input (volts / °C).
    ///
    /// # Errors
    /// Unknown channel, instrument not ready, wrong channel kind or driver error.
    pub fn read_analog(&self, name: &str) -> Result<f64> {
        let (inst, _) = self.channel(name)?;
        let mut i = lock(&inst);
        if !matches!(
            Self::kind_of(i.as_ref(), name),
            Some(ChannelKind::AnalogIn | ChannelKind::Temperature)
        ) {
            return Err(Self::wrong(name, i.as_ref(), "analog read"));
        }
        let r = i.analog_in().map(|a| a.read(&ChannelRef::from(name)));
        match r {
            Some(r) => Self::wrap(name, r),
            None => Err(Self::wrong(name, i.as_ref(), "analog read")),
        }
    }

    /// Sets an analog output.
    ///
    /// # Errors
    /// Unknown channel, not ready, wrong kind, out of range or driver error.
    pub fn write_analog(&self, name: &str, volts: f64) -> Result<()> {
        let (inst, _) = self.channel(name)?;
        let mut i = lock(&inst);
        if Self::kind_of(i.as_ref(), name) != Some(ChannelKind::AnalogOut) {
            return Err(Self::wrong(name, i.as_ref(), "analog write"));
        }
        let r = i.analog_out().map(|a| a.write(&ChannelRef::from(name), volts));
        match r {
            Some(r) => Self::wrap(name, r),
            None => Err(Self::wrong(name, i.as_ref(), "analog write")),
        }
    }

    /// Reads a digital line.
    ///
    /// # Errors
    /// Unknown channel, not ready, wrong kind or driver error.
    pub fn read_digital(&self, name: &str) -> Result<bool> {
        let (inst, _) = self.channel(name)?;
        let mut i = lock(&inst);
        let r = i.digital_io().map(|d| d.read(&ChannelRef::from(name)));
        match r {
            Some(r) => Self::wrap(name, r),
            None => Err(Self::wrong(name, i.as_ref(), "digital read")),
        }
    }

    /// Drives a digital output.
    ///
    /// # Errors
    /// Unknown channel, not ready, input line, or driver error.
    pub fn write_digital(&self, name: &str, value: bool) -> Result<()> {
        let (inst, _) = self.channel(name)?;
        let mut i = lock(&inst);
        let r = i.digital_io().map(|d| d.write(&ChannelRef::from(name), value));
        match r {
            Some(r) => Self::wrap(name, r),
            None => Err(Self::wrong(name, i.as_ref(), "digital write")),
        }
    }

    /// Runs `f` with the power supply that owns channel `name`.
    ///
    /// # Errors
    /// Unknown channel, not ready, not a PSU channel, or driver error.
    pub fn with_psu<T>(
        &self,
        name: &str,
        f: impl FnOnce(
            &mut dyn hwtest_instruments::PowerSupply,
            &ChannelRef,
        ) -> std::result::Result<T, InstrumentError>,
    ) -> Result<T> {
        let (inst, _) = self.channel(name)?;
        let mut i = lock(&inst);
        let ch = ChannelRef::from(name);
        let r = i.power_supply().map(|p| f(p, &ch));
        match r {
            Some(r) => Self::wrap(name, r),
            None => Err(Self::wrong(name, i.as_ref(), "power supply control")),
        }
    }

    /// Closes every instrument.
    pub fn close(&mut self) {
        for (name, m) in &mut self.instruments {
            if let Some(inst) = m.instrument.take() {
                if let Err(e) = lock(&inst).close() {
                    tracing::warn!("closing {name}: {e}");
                }
                m.state = InstrumentState::Closed;
            }
        }
        self.closed = true;
    }
}

impl Drop for Rack {
    fn drop(&mut self) {
        if !self.closed {
            self.close();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use hwtest_instruments::daqhats::emulator::AnalogOut;

    fn rack_yaml() -> &'static str {
        r#"
rack: {id: bench, description: Bench}
instruments:
  dio:
    driver: mcc152
    identity: {manufacturer: Measurement Computing, model: MCC 152}
    kwargs:
      address: 0
      dio_channels: [{id: 0, name: relay, direction: OUTPUT}, {id: 1, name: door, direction: INPUT}]
      analog_channels: [{id: 0, name: control_voltage}]
  daq:
    driver: "hwtest_mcc.mcc118:create_instrument"
    identity: {manufacturer: Measurement Computing, model: MCC 118}
    kwargs: {address: 4, sample_rate: 1000, channels: [{id: 0, name: echo}]}
  psu:
    driver: bk_psu
    identity: {manufacturer: B&K Precision, model: "9130B"}
    kwargs: {visa_address: "TCPIP::10.0.0.9::5025::SOCKET", channels: [{id: 1, logical_name: dut_5v, max_voltage: 5.5}, {id: 2, logical_name: aux}]}
  wrong:
    driver: mcc134
    identity: {manufacturer: Measurement Computing, model: MCC 999}
    kwargs: {address: 1, channels: [{id: 0, name: tc}]}
calibration: {mcc118_scale_factor: 1.01}
"#
    }

    fn rack() -> Rack {
        let cfg = RackConfig::from_yaml_str(rack_yaml(), None).unwrap();
        Rack::open(cfg, None, DriverEnv::emulated())
    }

    #[test]
    fn identity_mismatch_isolated() {
        let r = rack();
        let st = r.status();
        assert_eq!(st.state, "error");
        let wrong = st.instruments.iter().find(|i| i.name == "wrong").unwrap();
        assert_eq!(wrong.state, InstrumentState::Error);
        assert_eq!(
            wrong.error.as_deref(),
            Some("model mismatch: expected \"MCC 999\", got \"MCC 134\"")
        );
        assert!(st
            .instruments
            .iter()
            .filter(|i| i.name != "wrong")
            .all(|i| i.state == InstrumentState::Ready));
        assert_eq!(st.instruments[1].driver, "hwtest_mcc.mcc118:create_instrument");
        let e = r.read_analog("tc").unwrap_err().to_string();
        assert!(
            e.contains("instrument \"wrong\" for channel \"tc\" is not ready: model mismatch"),
            "{e}"
        );
    }

    #[test]
    fn logical_channel_operations() {
        let r = rack();
        r.env().emulated_hats().unwrap().connect_analog(AnalogOut { address: 0, channel: 0 }, 4, 0);
        r.write_analog("control_voltage", 2.5).unwrap();
        assert_eq!(r.read_analog("echo").unwrap(), 2.5);
        r.write_digital("relay", true).unwrap();
        assert!(r.read_digital("relay").unwrap());
        assert!(r.write_digital("door", true).unwrap_err().to_string().contains("INPUT"));
        r.with_psu("dut_5v", |p, ch| p.apply(ch, 5.0, 1.0)).unwrap();
        assert_eq!(r.env().emulated_psu["psu"].channel_state(1).unwrap(), (5.0, 1.0, false));
        assert!(r
            .with_psu("dut_5v", |p, ch| p.set_voltage(ch, 9.0))
            .unwrap_err()
            .to_string()
            .contains("exceeds limit"));
        let e = r.write_analog("echo", 1.0).unwrap_err().to_string();
        assert_eq!(e, "channel \"echo\" (AnalogIn) does not support analog write");
        let e = r.read_analog("ehco").unwrap_err().to_string();
        assert!(
            e.starts_with(
                "unknown channel \"ehco\" (rack channels: relay, door, control_voltage, echo"
            ),
            "{e}"
        );
        assert!(r.with_psu("relay", |p, ch| p.set_voltage(ch, 1.0)).is_err());
    }

    #[test]
    fn calibration_lookup_order() {
        let cfg = RackConfig::from_yaml_str(rack_yaml(), None).unwrap();
        let mut inst = RackInstance::new("001", "bench", "", "now");
        inst.calibration.insert("uut_adc_scale_factor".into(), 0.99);
        let r = Rack::open(cfg, Some(inst), DriverEnv::emulated());
        assert_eq!(r.calibration("uut_adc_scale_factor", 1.0), 0.99, "instance file");
        assert_eq!(
            r.calibration("mcc118_scale_factor", 1.0),
            1.0,
            "instance default overrides class"
        );
        assert_eq!(r.calibration("other", 0.5), 0.5);
        assert_eq!(r.status().serial.as_deref(), Some("001"));
    }

    #[test]
    fn close_marks_instruments_closed() {
        let mut r = rack();
        r.close();
        let st = r.status();
        assert_eq!(st.state, "closed");
        assert!(r.read_analog("echo").is_err());
    }
}
