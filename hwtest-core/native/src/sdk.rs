//! Bindings behind the `hwtest` Python SDK: the rack, the run session and
//! the adapter that lets a Python escape-hatch driver join a Rust rack.

use std::path::PathBuf;
use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use hwtest_core::InstrumentIdentity;
use hwtest_instruments::daqhats::emulator::AnalogOut;
use hwtest_instruments::{
    AnalogInput, AnalogOutput, ChannelInfo, ChannelKind, ChannelRef, DigitalIo, DriverEnv,
    Instrument, InstrumentError,
};
use hwtest_rack::Rack;
use hwtest_run::{EventSink, Mode, RunOptions, Session, SharedRack, Step, StepOutcome};
use hwtest_spec::rack::DriverRef;
use hwtest_spec::RackConfig;
use indexmap::IndexMap;
use pyo3::exceptions::{PyKeyError, PyKeyboardInterrupt, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyType};
use serde_json::Value;

use crate::conv::{py_to_value, value_to_py};
use crate::records::PyMonitorResult;
use crate::spec::{spec_err, PyMonitorState, PyTestDefinition};

pyo3::create_exception!(
    hwtest_core._native,
    HardwareError,
    pyo3::exceptions::PyRuntimeError,
    "A rack, instrument or channel operation failed."
);

/// `(instrument, module, function, kwargs)`.
type PythonDriverEntry = (String, String, String, Py<PyAny>);

fn hw_err(e: impl std::fmt::Display) -> PyErr {
    HardwareError::new_err(e.to_string())
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

fn json_to_py(py: Python<'_>, v: &impl serde::Serialize) -> PyResult<Py<PyAny>> {
    let value = serde_json::to_value(v).map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
    Ok(value_to_py(py, &value)?.unbind())
}

// ---------------------------------------------------------------------------
// Python escape-hatch driver adapter
// ---------------------------------------------------------------------------

/// Wraps a Python driver object so it can be used as a rack instrument.
///
/// The object must provide `get_identity()` (an `InstrumentIdentity` or a
/// dict with manufacturer/model/serial/firmware) and `channels()` (a list of
/// dicts with `name`, `id` and `kind`: `analog_in`, `analog_out`, temperature,
/// `digital_in`, `digital_out`). Reads call `read(name)`, writes `write(name, value)`.
struct PyDriver {
    obj: Py<PyAny>,
    model: String,
    channels: Vec<ChannelInfo>,
}

fn py_comms(e: &PyErr) -> InstrumentError {
    InstrumentError::Comms(format!("Python driver: {e}"))
}

impl PyDriver {
    fn new(py: Python<'_>, obj: Py<PyAny>) -> PyResult<Self> {
        let o = obj.bind(py);
        let model = if o.hasattr("model")? {
            o.getattr("model")?.str()?.to_string()
        } else {
            o.get_type().name()?.to_string()
        };
        let mut channels = Vec::new();
        if o.hasattr("channels")? {
            for item in o.call_method0("channels")?.try_iter()? {
                let d = item?;
                let get = |k: &str| d.get_item(k);
                let kind: String = get("kind")?.extract()?;
                let kind = match kind.as_str() {
                    "analog_in" => ChannelKind::AnalogIn,
                    "analog_out" => ChannelKind::AnalogOut,
                    "temperature" => ChannelKind::Temperature,
                    "digital_in" => ChannelKind::DigitalIn,
                    "digital_out" => ChannelKind::DigitalOut,
                    other => {
                        return Err(PyValueError::new_err(format!(
                            "unknown channel kind {other:?}"
                        )))
                    }
                };
                channels.push(ChannelInfo {
                    name: get("name")?.extract()?,
                    id: get("id")?.extract()?,
                    kind,
                    unit: "",
                });
            }
        }
        Ok(Self { obj, model, channels })
    }

    fn read_f64(&self, name: String) -> hwtest_instruments::Result<f64> {
        Python::attach(|py| {
            let r = self.obj.bind(py).call_method1("read", (name,)).map_err(|e| py_comms(&e))?;
            r.extract::<f64>()
                .map_err(|e| InstrumentError::Comms(format!("Python driver read(): {e}")))
        })
    }

    fn read_bool(&self, name: String) -> hwtest_instruments::Result<bool> {
        Python::attach(|py| {
            let r = self.obj.bind(py).call_method1("read", (name,)).map_err(|e| py_comms(&e))?;
            r.extract::<bool>()
                .map_err(|e| InstrumentError::Comms(format!("Python driver read(): {e}")))
        })
    }

    fn write_value(&self, name: String, value: Value) -> hwtest_instruments::Result<()> {
        Python::attach(|py| {
            let v = value_to_py(py, &value).map_err(|e| py_comms(&e))?;
            self.obj.bind(py).call_method1("write", (name, v)).map_err(|e| py_comms(&e))?;
            Ok(())
        })
    }

    fn name(ch: &ChannelRef, channels: &[ChannelInfo]) -> String {
        match ch {
            ChannelRef::Name(n) => n.clone(),
            ChannelRef::Id(i) => channels
                .iter()
                .find(|c| c.id == *i)
                .map_or_else(|| i.to_string(), |c| c.name.clone()),
        }
    }
}

impl Instrument for PyDriver {
    fn model(&self) -> &str {
        &self.model
    }
    fn identity(&mut self) -> hwtest_instruments::Result<InstrumentIdentity> {
        Python::attach(|py| {
            let id = self.obj.bind(py).call_method0("get_identity").map_err(|e| py_comms(&e))?;
            let field = |k: &str| -> PyResult<String> {
                if id.is_instance_of::<PyDict>() {
                    id.get_item(k)?.extract()
                } else {
                    id.getattr(k)?.extract()
                }
            };
            Ok(InstrumentIdentity {
                manufacturer: field("manufacturer").map_err(|e| py_comms(&e))?,
                model: field("model").map_err(|e| py_comms(&e))?,
                serial: field("serial").unwrap_or_default(),
                firmware: field("firmware").unwrap_or_default(),
            })
        })
    }
    fn channels(&self) -> Vec<ChannelInfo> {
        self.channels.clone()
    }
    fn close(&mut self) -> hwtest_instruments::Result<()> {
        Python::attach(|py| {
            let o = self.obj.bind(py);
            if o.hasattr("close").unwrap_or(false) {
                o.call_method0("close").map_err(|e| py_comms(&e))?;
            }
            Ok(())
        })
    }
    fn analog_in(&mut self) -> Option<&mut dyn AnalogInput> {
        Some(self)
    }
    fn analog_out(&mut self) -> Option<&mut dyn AnalogOutput> {
        Some(self)
    }
    fn digital_io(&mut self) -> Option<&mut dyn DigitalIo> {
        Some(self)
    }
}

impl AnalogInput for PyDriver {
    fn read(&mut self, ch: &ChannelRef) -> hwtest_instruments::Result<f64> {
        self.read_f64(Self::name(ch, &self.channels))
    }
}

impl AnalogOutput for PyDriver {
    fn write(&mut self, ch: &ChannelRef, value: f64) -> hwtest_instruments::Result<()> {
        self.write_value(Self::name(ch, &self.channels), Value::from(value))
    }
    fn last_written(&mut self, _ch: &ChannelRef) -> hwtest_instruments::Result<Option<f64>> {
        Ok(None)
    }
}

impl DigitalIo for PyDriver {
    fn read(&mut self, ch: &ChannelRef) -> hwtest_instruments::Result<bool> {
        self.read_bool(Self::name(ch, &self.channels))
    }
    fn write(&mut self, ch: &ChannelRef, value: bool) -> hwtest_instruments::Result<()> {
        self.write_value(Self::name(ch, &self.channels), Value::from(value))
    }
}

// ---------------------------------------------------------------------------
// Rack
// ---------------------------------------------------------------------------

/// A test rack: instruments by logical channel name.
#[pyclass(from_py_object, module = "hwtest_core._native", name = "Rack")]
#[derive(Clone)]
pub struct PyRack {
    rack: SharedRack,
}

impl PyRack {
    fn with<T>(&self, py: Python<'_>, f: impl FnOnce(&Rack) -> T + Send) -> T
    where
        T: Send,
    {
        py.detach(|| f(&lock(&self.rack)))
    }
}

fn open_rack(config: RackConfig, serial: Option<&str>, emulate: bool) -> PyResult<Rack> {
    let instance = match serial {
        Some(s) => {
            let class = config
                .source_path
                .as_ref()
                .and_then(|p| p.file_stem())
                .map_or_else(|| config.rack_id.clone(), |s| s.to_string_lossy().into_owned());
            Some(hwtest_spec::load_instance(&class, Some(s), None, None).map_err(spec_err)?)
        }
        None => None,
    };
    let env = if emulate { DriverEnv::emulated() } else { DriverEnv::from_env() };
    Ok(Rack::open(config, instance, env))
}

#[pymethods]
impl PyRack {
    /// Opens a rack from a YAML file.
    #[classmethod]
    #[pyo3(signature = (path, serial = None, emulate = false))]
    fn open(
        _cls: &Bound<'_, PyType>,
        py: Python<'_>,
        path: PathBuf,
        serial: Option<String>,
        emulate: bool,
    ) -> PyResult<Self> {
        let config = RackConfig::from_file(&path).map_err(spec_err)?;
        let rack = py.detach(|| open_rack(config, serial.as_deref(), emulate))?;
        Ok(Self { rack: Arc::new(Mutex::new(rack)) })
    }

    /// Opens a rack by class name, searching `search_paths`, `.`, `./configs`
    /// and `$HWTEST_RACK_PATH`.
    #[classmethod]
    #[pyo3(signature = (rack_class, serial = None, emulate = false, search_paths = None))]
    fn find(
        _cls: &Bound<'_, PyType>,
        py: Python<'_>,
        rack_class: &str,
        serial: Option<String>,
        emulate: bool,
        search_paths: Option<Vec<PathBuf>>,
    ) -> PyResult<Self> {
        let dirs = hwtest_spec::search::rack_search_paths(&search_paths.unwrap_or_default());
        let config = RackConfig::find(rack_class, Some(&dirs)).map_err(spec_err)?;
        let rack = py.detach(|| open_rack(config, serial.as_deref(), emulate))?;
        Ok(Self { rack: Arc::new(Mutex::new(rack)) })
    }

    /// Rack class id.
    #[getter]
    fn rack_id(&self) -> String {
        lock(&self.rack).rack_id().to_string()
    }

    /// True if every instrument is ready.
    #[getter]
    fn ready(&self) -> bool {
        lock(&self.rack).is_ready()
    }

    /// True when running against emulators.
    #[getter]
    fn emulated(&self) -> bool {
        lock(&self.rack).env().is_emulated()
    }

    /// Full status as a dict.
    fn status(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        json_to_py(py, &lock(&self.rack).status())
    }

    /// Logical channels: `[{"name", "instrument", "channel_id", "type"}]`.
    fn channels(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let list: Vec<Value> = lock(&self.rack)
            .channels()
            .iter()
            .map(|c| serde_json::json!({"name": c.logical_name, "instrument": c.instrument, "channel_id": c.channel_id, "type": c.channel_type.as_str()}))
            .collect();
        json_to_py(py, &list)
    }

    /// Reads a channel: analog/temperature inputs return a float, digital
    /// lines a bool, power supply channels their measured voltage.
    fn read(&self, py: Python<'_>, channel: String) -> PyResult<Py<PyAny>> {
        let v = self.with(py, |r| -> Result<Value, String> {
            match r.channel_kind(&channel) {
                Some(ChannelKind::DigitalIn | ChannelKind::DigitalOut) => {
                    r.read_digital(&channel).map(Value::from)
                }
                Some(ChannelKind::Psu) => {
                    r.with_psu(&channel, |p, ch| p.measure_voltage(ch)).map(Value::from)
                }
                _ => r.read_analog(&channel).map(Value::from),
            }
            .map_err(|e| e.to_string())
        });
        match v {
            Ok(v) => Ok(value_to_py(py, &v)?.unbind()),
            Err(e) => Err(hw_err(e)),
        }
    }

    /// Writes a channel: analog outputs take volts, digital outputs a bool,
    /// power supply channels a voltage setpoint.
    fn write(&self, py: Python<'_>, channel: String, value: &Bound<'_, PyAny>) -> PyResult<()> {
        let as_bool = value.extract::<bool>().ok();
        let as_f64 = value.extract::<f64>().ok();
        self.with(py, |r| -> Result<(), String> {
            match r.channel_kind(&channel) {
                Some(ChannelKind::DigitalOut | ChannelKind::DigitalIn) => match as_bool {
                    Some(b) => r.write_digital(&channel, b).map_err(|e| e.to_string()),
                    None => Err(format!("channel {channel:?} is digital: write True or False")),
                },
                Some(ChannelKind::Psu) => match as_f64 {
                    Some(v) => r
                        .with_psu(&channel, |p, ch| p.set_voltage(ch, v))
                        .map_err(|e| e.to_string()),
                    None => Err(format!("channel {channel:?} is a power supply: write a voltage")),
                },
                _ => match as_f64 {
                    Some(v) => r.write_analog(&channel, v).map_err(|e| e.to_string()),
                    None => Err(format!("channel {channel:?} needs a number")),
                },
            }
        })
        .map_err(hw_err)
    }

    /// Power supply: voltage and current limit.
    fn psu_apply(&self, py: Python<'_>, channel: String, volts: f64, amps: f64) -> PyResult<()> {
        self.with(py, |r| {
            r.with_psu(&channel, |p, ch| p.apply(ch, volts, amps)).map_err(|e| e.to_string())
        })
        .map_err(hw_err)
    }

    /// Power supply: current limit.
    fn psu_set_current_limit(&self, py: Python<'_>, channel: String, amps: f64) -> PyResult<()> {
        self.with(py, |r| {
            r.with_psu(&channel, |p, ch| p.set_current_limit(ch, amps)).map_err(|e| e.to_string())
        })
        .map_err(hw_err)
    }

    /// Power supply: output on/off.
    fn psu_output(&self, py: Python<'_>, channel: String, on: bool) -> PyResult<()> {
        self.with(py, |r| {
            r.with_psu(&channel, |p, ch| p.set_output(ch, on)).map_err(|e| e.to_string())
        })
        .map_err(hw_err)
    }

    /// Power supply: `{"voltage", "current", "power", "setpoint", "current_limit", "output"}`.
    fn psu_status(&self, py: Python<'_>, channel: String) -> PyResult<Py<PyAny>> {
        let v = self
            .with(py, |r| {
                r.with_psu(&channel, |p, ch| {
                    Ok(serde_json::json!({
                        "voltage": p.measure_voltage(ch)?,
                        "current": p.measure_current(ch)?,
                        "power": p.measure_power(ch)?,
                        "setpoint": p.voltage_setpoint(ch)?,
                        "current_limit": p.current_limit(ch)?,
                        "output": p.output_enabled(ch)?,
                    }))
                })
                .map_err(|e| e.to_string())
            })
            .map_err(hw_err)?;
        Ok(value_to_py(py, &v)?.unbind())
    }

    /// A calibration factor (rack instance file, then rack class defaults).
    #[pyo3(signature = (name, default = 1.0))]
    fn calibration(&self, name: &str, default: f64) -> f64 {
        lock(&self.rack).calibration(name, default)
    }

    /// Python escape-hatch drivers declared in the rack YAML:
    /// `[(instrument, module, function, kwargs)]`.
    fn python_drivers(&self, py: Python<'_>) -> PyResult<Vec<PythonDriverEntry>> {
        let r = lock(&self.rack);
        r.config()
            .instruments
            .values()
            .filter_map(|i| match &i.driver {
                DriverRef::Python { module, function }
                    if hwtest_instruments::builtin_name(&i.driver).is_none() =>
                {
                    Some((i.name.clone(), module.clone(), function.clone(), i.kwargs.clone()))
                }
                _ => None,
            })
            .map(|(n, m, f, k)| Ok((n, m, f, value_to_py(py, &Value::Object(k))?.unbind())))
            .collect()
    }

    /// Attaches a Python driver object for an instrument in the rack YAML;
    /// returns the resulting state (`ready` or `error`).
    fn attach_python(&self, py: Python<'_>, name: &str, driver: Py<PyAny>) -> PyResult<String> {
        let adapter = PyDriver::new(py, driver)?;
        let state =
            py.detach(|| lock(&self.rack).attach(name, Box::new(adapter))).map_err(hw_err)?;
        Ok(format!("{state:?}").to_lowercase())
    }

    /// Closes all instruments.
    fn close(&self, py: Python<'_>) {
        py.detach(|| lock(&self.rack).close());
    }

    // --- emulation hooks (only in emulated racks) ---------------------------

    /// Emulation: wire an MCC 152 analog output to an MCC 118 input.
    #[pyo3(signature = (from_address, from_channel, to_address, to_channel, gain = 1.0))]
    fn emu_connect_analog(
        &self,
        from_address: u8,
        from_channel: u8,
        to_address: u8,
        to_channel: u8,
        gain: f64,
    ) -> PyResult<()> {
        let r = lock(&self.rack);
        let hats = r
            .env()
            .emulated_hats()
            .ok_or_else(|| PyRuntimeError::new_err("rack is not emulated"))?;
        hats.connect_analog_scaled(
            AnalogOut { address: from_address, channel: from_channel },
            to_address,
            to_channel,
            gain,
        );
        Ok(())
    }

    /// Emulation: set an MCC 118 input voltage.
    fn emu_set_analog_input(&self, address: u8, channel: u8, volts: f64) -> PyResult<()> {
        let r = lock(&self.rack);
        r.env()
            .emulated_hats()
            .ok_or_else(|| PyRuntimeError::new_err("rack is not emulated"))?
            .set_analog_input(address, channel, volts);
        Ok(())
    }

    /// Emulation: set an MCC 134 temperature.
    fn emu_set_temperature(&self, address: u8, channel: u8, celsius: f64) -> PyResult<()> {
        let r = lock(&self.rack);
        r.env()
            .emulated_hats()
            .ok_or_else(|| PyRuntimeError::new_err("rack is not emulated"))?
            .set_temperature(address, channel, celsius);
        Ok(())
    }

    /// Emulation: set the level driven onto an MCC 152 input.
    fn emu_set_digital_input(&self, address: u8, channel: u8, level: bool) -> PyResult<()> {
        let r = lock(&self.rack);
        r.env()
            .emulated_hats()
            .ok_or_else(|| PyRuntimeError::new_err("rack is not emulated"))?
            .set_digital_input(address, channel, level);
        Ok(())
    }

    /// Emulation: set an AD/DA board ADC input.
    fn emu_adda_set_input(&self, instrument: &str, channel: u8, volts: f64) -> PyResult<()> {
        let r = lock(&self.rack);
        r.env()
            .emulated_adda
            .get(instrument)
            .ok_or_else(|| PyKeyError::new_err(format!("no emulated AD/DA named {instrument:?}")))?
            .set_input(channel, volts);
        Ok(())
    }

    /// Emulation: set a PSU channel's measured current.
    fn emu_psu_set_current(&self, instrument: &str, channel: usize, amps: f64) -> PyResult<()> {
        let r = lock(&self.rack);
        r.env()
            .emulated_psu
            .get(instrument)
            .ok_or_else(|| PyKeyError::new_err(format!("no emulated PSU named {instrument:?}")))?
            .set_measured_current(channel, Some(amps));
        Ok(())
    }

    fn __repr__(&self) -> String {
        let r = lock(&self.rack);
        format!(
            "Rack({:?}, ready={}, emulated={})",
            r.rack_id(),
            r.is_ready(),
            r.env().is_emulated()
        )
    }
}

// ---------------------------------------------------------------------------
// Session
// ---------------------------------------------------------------------------

/// A test run (driven by `hwtest.Test`).
#[pyclass(module = "hwtest_core._native", name = "Session")]
pub struct PySession {
    session: Arc<Mutex<Session>>,
    rack: Option<PyRack>,
}

fn is_interrupt(py: Python<'_>, e: &PyErr) -> bool {
    e.is_instance_of::<PyKeyboardInterrupt>(py)
}

fn error_message(py: Python<'_>, e: &PyErr) -> String {
    // Full traceback to stderr for the engineer; a one-line summary for events.
    e.display(py);
    e.to_string()
}

#[pymethods]
impl PySession {
    /// Prepares a run. `options` keys: mode, `max_cycles`, `run_id`, `nats_url`,
    /// `db_path`, `unit_type`, units ({slot: serial}), offsite, loggers, events
    /// ("stdout" or None), tags.
    #[new]
    #[pyo3(signature = (definition, rack = None, **options))]
    fn new(
        py: Python<'_>,
        definition: PyRef<'_, PyTestDefinition>,
        rack: Option<PyRack>,
        options: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Self> {
        let mut o = RunOptions::default();
        if let Some(opts) = options {
            for (k, v) in opts.iter() {
                let key: String = k.extract()?;
                if v.is_none() {
                    continue;
                }
                match key.as_str() {
                    "mode" => {
                        o.mode = Mode::parse(&v.extract::<String>()?)
                            .map_err(|e| PyValueError::new_err(e.to_string()))?;
                    }
                    "max_cycles" => o.max_cycles = Some(v.extract()?),
                    "run_id" => o.run_id = Some(v.extract()?),
                    "nats_url" => o.nats_url = Some(v.extract()?),
                    "db_path" => o.db_path = Some(v.extract()?),
                    "unit_type" => o.unit_type = v.extract()?,
                    "units" => {
                        o.units = v
                            .extract::<std::collections::BTreeMap<u32, String>>()?
                            .into_iter()
                            .collect();
                    }
                    "offsite" => o.offsite = v.extract()?,
                    "loggers" => o.loggers = v.extract()?,
                    "events" => {
                        if v.extract::<String>()? == "stdout" {
                            o.events = EventSink::Stdout;
                        }
                    }
                    "tags" => {
                        o.tags = v
                            .extract::<std::collections::BTreeMap<String, String>>()?
                            .into_iter()
                            .collect();
                    }
                    other => {
                        return Err(PyValueError::new_err(format!("unknown run option {other:?}")))
                    }
                }
            }
        }
        let def = definition.inner.clone();
        let shared = rack.as_ref().map(|r| r.rack.clone());
        let session = py
            .detach(|| Session::open(def, shared, o))
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
        Ok(Self { session: Arc::new(Mutex::new(session)), rack })
    }

    /// Runs the test: `setup(ctx)` once, `step(ctx, state)` for every state
    /// of every cycle, `teardown(ctx)` always. Returns the summary dict.
    #[pyo3(signature = (ctx, step, setup = None, teardown = None))]
    fn run(
        &self,
        py: Python<'_>,
        ctx: Py<PyAny>,
        step: Py<PyAny>,
        setup: Option<Py<PyAny>>,
        teardown: Option<Py<PyAny>>,
    ) -> PyResult<Py<PyAny>> {
        let mut ok = true;
        if let Some(f) = &setup {
            if let Err(e) = f.call1(py, (ctx.clone_ref(py),)) {
                ok = false;
                let msg = if is_interrupt(py, &e) {
                    None
                } else {
                    Some(format!("setup: {}", error_message(py, &e)))
                };
                let s = self.session.clone();
                py.detach(|| match msg {
                    Some(m) => lock(&s).abort(&m),
                    None => lock(&s).stop(),
                });
            }
        }
        if ok {
            loop {
                let s = self.session.clone();
                let next = py.detach(|| lock(&s).next_step());
                let Step::Run(state) = next else { break };
                let st = Py::new(py, PyMonitorState { inner: state })?;
                let outcome = match step.call1(py, (ctx.clone_ref(py), st)) {
                    Ok(_) => StepOutcome::Ok,
                    Err(e) if is_interrupt(py, &e) => StepOutcome::Stopped,
                    Err(e) => StepOutcome::Error(error_message(py, &e)),
                };
                py.detach(|| lock(&s).complete_step(outcome));
            }
        }
        if let Some(f) = &teardown {
            if let Err(e) = f.call1(py, (ctx.clone_ref(py),)) {
                let msg = format!("teardown: {}", error_message(py, &e));
                lock(&self.session).log("error", &msg);
            }
        }
        let summary = py.detach(|| lock(&self.session).summary());
        json_to_py(py, &summary)
    }

    /// Records measurements (`{name: value}`) and returns monitor results.
    #[pyo3(signature = (values, topic = None))]
    fn measure(
        &self,
        py: Python<'_>,
        values: &Bound<'_, PyDict>,
        topic: Option<String>,
    ) -> PyResult<Vec<PyMonitorResult>> {
        let mut map = IndexMap::new();
        for (k, v) in values.iter() {
            let key: String = k.extract()?;
            let value: f64 = if let Ok(b) = v.extract::<bool>() {
                f64::from(u8::from(b))
            } else {
                v.extract().map_err(|_| {
                    PyValueError::new_err(format!("measurement {key:?} must be a number, got {v}"))
                })?
            };
            map.insert(key, value);
        }
        let s = self.session.clone();
        let results = py
            .detach(|| lock(&s).measure(&map, topic.as_deref()))
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(results.into_iter().map(|inner| PyMonitorResult { inner }).collect())
    }

    /// A parameter for the current state; `KeyError` if undefined.
    fn param(&self, py: Python<'_>, name: &str) -> PyResult<Py<PyAny>> {
        let v = lock(&self.session).param(name).cloned().ok_or_else(|| {
            PyKeyError::new_err(format!(
                "parameter {name:?} is not defined in case_parameters or the current state"
            ))
        })?;
        Ok(value_to_py(py, &v)?.unbind())
    }

    /// Sleeps, staying responsive to Ctrl-C / stop requests.
    fn sleep(&self, py: Python<'_>, seconds: f64) -> PyResult<()> {
        if seconds.is_nan() || seconds < 0.0 {
            return Err(PyValueError::new_err(format!("sleep time must be >= 0, got {seconds}")));
        }
        let end = Instant::now() + Duration::from_secs_f64(seconds);
        let cancel = lock(&self.session).cancel_handle();
        loop {
            let left = end.saturating_duration_since(Instant::now());
            if left.is_zero() {
                return Ok(());
            }
            py.detach(|| std::thread::sleep(left.min(Duration::from_millis(50))));
            py.check_signals()?;
            if cancel.load(Ordering::Relaxed) {
                return Err(PyKeyboardInterrupt::new_err("stop requested"));
            }
        }
    }

    /// Requests a stop at the next state boundary.
    fn request_stop(&self) {
        lock(&self.session).cancel_handle().store(true, Ordering::Relaxed);
    }

    /// Starts a ramp into the current state (monitors skip until `end_transition`).
    #[pyo3(signature = (description = "ramping"))]
    fn begin_transition(&self, description: &str) {
        lock(&self.session).begin_transition(description);
    }

    /// Ends a ramp.
    fn end_transition(&self) {
        lock(&self.session).end_transition();
    }

    /// Emits a log line to the runner.
    #[pyo3(signature = (message, level = "info"))]
    fn log(&self, message: &str, level: &str) {
        lock(&self.session).log(level, message);
    }

    /// The rack (if any).
    #[getter]
    fn rack(&self) -> Option<PyRack> {
        self.rack.clone()
    }

    /// Run id.
    #[getter]
    fn run_id(&self) -> String {
        lock(&self.session).run_id().to_string()
    }

    /// Mode name.
    #[getter]
    fn mode(&self) -> &'static str {
        lock(&self.session).mode().as_str()
    }

    /// Completed cycles so far.
    #[getter]
    fn cycle(&self) -> u64 {
        lock(&self.session).cycle()
    }

    /// The current state id.
    #[getter]
    fn state_id(&self) -> Option<String> {
        lock(&self.session).current_state().map(|s| s.state_id.clone())
    }

    /// UUT serials by slot.
    #[getter]
    fn units<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in lock(&self.session).units() {
            d.set_item(k, v)?;
        }
        Ok(d)
    }
}

/// Parses an `@@hwtest {json}` event line (None for ordinary output).
#[pyfunction]
pub fn parse_event_line(py: Python<'_>, line: &str) -> PyResult<Option<Py<PyAny>>> {
    hwtest_run::parse_event_line(line).map(|v| value_to_py(py, &v).map(Bound::unbind)).transpose()
}

/// Converts a JSON-able Python value (for tests of the conversion layer).
#[pyfunction]
pub fn _roundtrip(py: Python<'_>, v: &Bound<'_, PyAny>) -> PyResult<Py<PyAny>> {
    Ok(value_to_py(py, &py_to_value(v)?)?.unbind())
}

// ---------------------------------------------------------------------------
// CAN bus and UUT client
// ---------------------------------------------------------------------------

use hwtest_instruments::can::{self, CanBus, CanMessage};

fn can_to_py(py: Python<'_>, m: &CanMessage) -> PyResult<Py<PyAny>> {
    let d = PyDict::new(py);
    d.set_item("arbitration_id", m.arbitration_id)?;
    d.set_item("data", pyo3::types::PyBytes::new(py, &m.data))?;
    d.set_item("is_extended_id", m.is_extended_id)?;
    d.set_item("timestamp_ns", m.timestamp_ns)?;
    Ok(d.into_any().unbind())
}

/// A CAN interface (`can0`, `vcan0`, `virtual:<name>`). Received frames are
/// dicts: `{"arbitration_id", "data" (bytes), "is_extended_id", "timestamp_ns"}`.
#[pyclass(module = "hwtest_core._native", name = "CanBus")]
pub struct PyCanBus {
    bus: Arc<dyn CanBus>,
}

#[pymethods]
impl PyCanBus {
    #[new]
    #[pyo3(signature = (interface = "can0"))]
    fn new(interface: &str) -> PyResult<Self> {
        Ok(Self { bus: can::open_can(interface).map_err(hw_err)? })
    }

    /// Interface name.
    #[getter]
    fn name(&self) -> String {
        self.bus.name().to_string()
    }

    /// Sends a frame; ids above 0x7FF are sent as extended unless `extended` says otherwise.
    #[pyo3(signature = (arbitration_id, data = Vec::new(), extended = None))]
    fn send(
        &self,
        py: Python<'_>,
        arbitration_id: u32,
        data: Vec<u8>,
        extended: Option<bool>,
    ) -> PyResult<()> {
        let mut m = CanMessage::new(arbitration_id, &data);
        if let Some(e) = extended {
            m.is_extended_id = e;
        }
        py.detach(|| self.bus.send(&m)).map_err(hw_err)
    }

    /// The next frame, or None after `timeout` seconds.
    #[pyo3(signature = (timeout = 1.0))]
    fn recv(&self, py: Python<'_>, timeout: f64) -> PyResult<Option<Py<PyAny>>> {
        let m = py
            .detach(|| self.bus.recv(Duration::from_secs_f64(timeout.max(0.0))))
            .map_err(hw_err)?;
        m.map(|m| can_to_py(py, &m)).transpose()
    }

    /// Waits for a frame with `arbitration_id` (others are discarded); None on timeout.
    #[pyo3(signature = (arbitration_id, timeout = 1.0))]
    fn wait_for(
        &self,
        py: Python<'_>,
        arbitration_id: u32,
        timeout: f64,
    ) -> PyResult<Option<Py<PyAny>>> {
        let m = py
            .detach(|| {
                can::wait_for(
                    self.bus.as_ref(),
                    arbitration_id,
                    Duration::from_secs_f64(timeout.max(0.0)),
                )
            })
            .map_err(hw_err)?;
        m.map(|m| can_to_py(py, &m)).transpose()
    }

    /// Frames received during `duration` seconds (optionally only `arbitration_id`).
    #[pyo3(signature = (duration, arbitration_id = None))]
    fn collect(
        &self,
        py: Python<'_>,
        duration: f64,
        arbitration_id: Option<u32>,
    ) -> PyResult<Vec<Py<PyAny>>> {
        let ms = py
            .detach(|| {
                can::collect(
                    self.bus.as_ref(),
                    Duration::from_secs_f64(duration.max(0.0)),
                    arbitration_id,
                )
            })
            .map_err(hw_err)?;
        ms.iter().map(|m| can_to_py(py, m)).collect()
    }
}

/// Client for the UUT simulator's HTTP API.
#[pyclass(module = "hwtest_core._native", name = "UutClient")]
pub struct PyUutClient {
    client: hwtest_uut_sim::UutClient,
}

fn uut_err(e: hwtest_uut_sim::client::ClientError) -> PyErr {
    hw_err(e)
}

#[pymethods]
impl PyUutClient {
    /// `url` defaults to the `UUT_URL` environment variable.
    #[new]
    #[pyo3(signature = (url = None, timeout = 5.0))]
    fn new(url: Option<String>, timeout: f64) -> PyResult<Self> {
        let url = url
            .or_else(|| std::env::var("UUT_URL").ok())
            .ok_or_else(|| PyValueError::new_err("no UUT URL: pass url= or set UUT_URL"))?;
        Ok(Self { client: hwtest_uut_sim::UutClient::new(&url, Duration::from_secs_f64(timeout)) })
    }
    #[getter]
    fn url(&self) -> String {
        self.client.base_url().to_string()
    }
    fn health(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let v = py.detach(|| self.client.health()).map_err(uut_err)?;
        Ok(value_to_py(py, &v)?.unbind())
    }
    fn status(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let v = py.detach(|| self.client.status()).map_err(uut_err)?;
        Ok(value_to_py(py, &v)?.unbind())
    }
    fn dac_write(&self, py: Python<'_>, channel: u8, voltage: f64) -> PyResult<()> {
        py.detach(|| self.client.dac_write(channel, voltage)).map_err(uut_err)
    }
    fn dac_read(&self, py: Python<'_>, channel: u8) -> PyResult<f64> {
        py.detach(|| self.client.dac_read(channel)).map_err(uut_err)
    }
    fn adc_read(&self, py: Python<'_>, channel: u8) -> PyResult<f64> {
        py.detach(|| self.client.adc_read(channel)).map_err(uut_err)
    }
    #[pyo3(signature = (pin, output, pullup = false))]
    fn gpio_configure(&self, py: Python<'_>, pin: u8, output: bool, pullup: bool) -> PyResult<()> {
        py.detach(|| self.client.gpio_configure(pin, output, pullup)).map_err(uut_err)
    }
    fn gpio_write(&self, py: Python<'_>, pin: u8, value: bool) -> PyResult<()> {
        py.detach(|| self.client.gpio_write(pin, value)).map_err(uut_err)
    }
    fn gpio_read(&self, py: Python<'_>, pin: u8) -> PyResult<bool> {
        py.detach(|| self.client.gpio_read(pin)).map_err(uut_err)
    }
    #[pyo3(signature = (arbitration_id, data = Vec::new()))]
    fn can_send(&self, py: Python<'_>, arbitration_id: u32, data: Vec<u8>) -> PyResult<()> {
        py.detach(|| self.client.can_send(&CanMessage::new(arbitration_id, &data))).map_err(uut_err)
    }
    fn can_received(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let v = py.detach(|| self.client.can_received()).map_err(uut_err)?;
        Ok(value_to_py(py, &v)?.unbind())
    }
    fn can_clear_received(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.client.can_clear_received()).map_err(uut_err)
    }
    #[pyo3(signature = (enabled, id_offset = 0, filter_ids = None))]
    fn can_set_echo(
        &self,
        py: Python<'_>,
        enabled: bool,
        id_offset: i64,
        filter_ids: Option<Vec<u32>>,
    ) -> PyResult<()> {
        py.detach(|| self.client.can_set_echo(enabled, id_offset, filter_ids.as_deref()))
            .map_err(uut_err)
    }
    fn can_heartbeat(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let v = py.detach(|| self.client.can_heartbeat()).map_err(uut_err)?;
        Ok(value_to_py(py, &v)?.unbind())
    }
    fn failure_status(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let v = py.detach(|| self.client.failure_status()).map_err(uut_err)?;
        Ok(value_to_py(py, &v)?.unbind())
    }
    #[pyo3(signature = (delay_seconds = None, duration_seconds = None, voltage_offset = None))]
    fn failure_configure(
        &self,
        py: Python<'_>,
        delay_seconds: Option<f64>,
        duration_seconds: Option<f64>,
        voltage_offset: Option<f64>,
    ) -> PyResult<()> {
        py.detach(|| self.client.failure_configure(delay_seconds, duration_seconds, voltage_offset))
            .map_err(uut_err)
    }
    fn failure_reset(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.client.failure_reset()).map_err(uut_err)
    }
    /// Emulation only: the UUT DAC output actually driven (including faults).
    fn emu_dac_output(&self, py: Python<'_>, channel: u8) -> PyResult<f64> {
        py.detach(|| self.client.emulated_dac_output(channel)).map_err(uut_err)
    }
    /// Emulation only: set an emulated UUT ADC input.
    fn emu_adc_input(&self, py: Python<'_>, channel: u8, voltage: f64) -> PyResult<()> {
        py.detach(|| self.client.emulate_adc_input(channel, voltage)).map_err(uut_err)
    }
    fn __repr__(&self) -> String {
        format!("UutClient({:?})", self.client.base_url())
    }
}

/// An in-process UUT simulator with its HTTP API (for developing and testing
/// scripts without a UUT). Hardware is emulated; use `can_interface =
/// "virtual:<name>"` to share a CAN bus with `CanBus("virtual:<name>")`.
#[pyclass(module = "hwtest_core._native", name = "UutSimulator")]
pub struct PyUutSimulator {
    server: Mutex<Option<hwtest_uut_sim::server::BackgroundServer>>,
    url: String,
}

#[pymethods]
impl PyUutSimulator {
    #[new]
    #[pyo3(signature = (*, port = 0, host = "127.0.0.1", can_interface = None, loopback = false, heartbeat_interval_ms = 100, failure_delay = 0.0, failure_duration = 10.0, failure_offset = 1.0))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        port: u16,
        host: &str,
        can_interface: Option<String>,
        loopback: bool,
        heartbeat_interval_ms: u64,
        failure_delay: f64,
        failure_duration: f64,
        failure_offset: f64,
    ) -> PyResult<Self> {
        use hwtest_uut_sim::{AdDaBackend, GpioBackend, SimConfig, Simulator};
        let config = SimConfig {
            can_interface,
            heartbeat_interval_ms,
            adda: AdDaBackend::Emulated { loopback },
            gpio: GpioBackend::Emulated,
            failure_delay,
            failure_duration,
            failure_offset,
            ..SimConfig::default()
        };
        let server = py
            .detach(|| {
                hwtest_uut_sim::server::BackgroundServer::start(
                    Simulator::start(config),
                    host,
                    port,
                )
            })
            .map_err(hw_err)?;
        let url = format!("http://{}", server.addr());
        Ok(Self { server: Mutex::new(Some(server)), url })
    }

    /// Base URL (pass to `UutClient` or set as `UUT_URL`).
    #[getter]
    fn url(&self) -> String {
        self.url.clone()
    }

    /// Stops the server (also on garbage collection / `with` exit).
    fn close(&self, py: Python<'_>) {
        let s = self.server.lock().unwrap_or_else(PoisonError::into_inner).take();
        py.detach(move || drop(s));
    }

    fn __enter__(slf: Py<Self>) -> Py<Self> {
        slf
    }

    #[pyo3(signature = (*_args))]
    fn __exit__(&self, py: Python<'_>, _args: &Bound<'_, pyo3::types::PyTuple>) -> bool {
        self.close(py);
        false
    }

    fn __repr__(&self) -> String {
        format!("UutSimulator({:?})", self.url)
    }
}
