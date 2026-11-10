//! YAML specifications: test definitions and the bounds monitor.

use std::path::PathBuf;

use hwtest_core::monitor::{BoundsMonitor, StateBounds};
use hwtest_core::Timestamp;
use hwtest_spec::testdef::{self, LoggerKind, MonitorKind};
use hwtest_spec::{LoggerDef, MonitorDef, MonitorState, SpecError, TestCaseInfo, TestDefinition};
use pyo3::exceptions::{PyFileNotFoundError, PyKeyError, PyOSError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PySet, PyTuple, PyType};
use serde_json::{Map, Value};

use crate::bounds::bound_to_py;
use crate::conv::{obj, py_to_value, repr_kw, type_error, value_to_py, SpecError as PySpecError};
use crate::records::{PyEnvironmentalState, PyMonitorResult};

/// Maps a spec error onto Python exceptions (`FileNotFoundError`, `OSError`,
/// `hwtest_core.errors.SpecError`).
pub fn spec_err(err: SpecError) -> PyErr {
    match err {
        SpecError::NotFound { .. } => PyFileNotFoundError::new_err(err.to_string()),
        SpecError::Io { .. } => PyOSError::new_err(err.to_string()),
        SpecError::Invalid { .. } => PySpecError::new_err(err.to_string()),
    }
}

fn map_to_py<'py>(py: Python<'py>, m: &Map<String, Value>) -> PyResult<Bound<'py, PyAny>> {
    value_to_py(py, &Value::Object(m.clone()))
}

fn path_to_py<'py>(py: Python<'py>, p: Option<&PathBuf>) -> PyResult<Bound<'py, PyAny>> {
    match p {
        Some(p) => py.import("pathlib")?.getattr("Path")?.call1((p.clone(),)),
        None => Ok(py.None().into_bound(py)),
    }
}

fn paths_arg(paths: Option<&Bound<'_, PyAny>>) -> PyResult<Option<Vec<PathBuf>>> {
    paths
        .filter(|p| !p.is_none())
        .map(|p| p.try_iter()?.map(|x| x?.extract::<PathBuf>()).collect())
        .transpose()
}

fn state_bounds_to_py<'py>(py: Python<'py>, b: &StateBounds) -> PyResult<Bound<'py, PyDict>> {
    let out = PyDict::new(py);
    for (state, fields) in b.states() {
        let d = PyDict::new(py);
        for (field, bound) in fields {
            d.set_item(field, bound_to_py(py, bound.clone())?)?;
        }
        out.set_item(state, d)?;
    }
    Ok(out)
}

/// Test case identification.
#[pyclass(frozen, eq, module = "hwtest_core.spec", name = "TestCaseInfo")]
#[derive(Clone, PartialEq)]
pub struct PyTestCaseInfo {
    inner: TestCaseInfo,
}

#[pymethods]
impl PyTestCaseInfo {
    /// Tells pytest this is not a test class (its name starts with "Test").
    #[classattr]
    #[allow(non_upper_case_globals)] // Python attribute name
    const __test__: bool = false;
    #[getter]
    fn id(&self) -> &str {
        &self.inner.id
    }
    #[getter]
    fn name(&self) -> &str {
        &self.inner.name
    }
    #[getter]
    fn version(&self) -> &str {
        &self.inner.version
    }
    #[getter]
    fn description(&self) -> &str {
        &self.inner.description
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "TestCaseInfo",
            &[
                ("id", obj(py, &self.inner.id)?),
                ("name", obj(py, &self.inner.name)?),
                ("version", obj(py, &self.inner.version)?),
            ],
        )
    }
}

/// An environmental state from `monitor_states`.
#[pyclass(frozen, eq, module = "hwtest_core.spec", name = "MonitorState")]
#[derive(Clone, PartialEq)]
pub struct PyMonitorState {
    inner: MonitorState,
}

#[pymethods]
impl PyMonitorState {
    #[getter]
    fn id(&self) -> &str {
        &self.inner.id
    }
    #[getter]
    fn name(&self) -> &str {
        &self.inner.name
    }
    #[getter]
    fn description(&self) -> &str {
        &self.inner.description
    }
    /// Per-state parameters (every key except `name` and `description`).
    #[getter]
    fn parameters<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        map_to_py(py, &self.inner.parameters)
    }
    /// The `target_voltage` parameter, or 0.0 (kept for existing tests).
    #[getter]
    fn target_voltage(&self) -> f64 {
        self.inner.parameters.get("target_voltage").and_then(Value::as_f64).unwrap_or(0.0)
    }
    /// Always empty; kept for compatibility. Use `parameters`.
    #[getter]
    #[allow(clippy::unused_self)] // Python property
    fn metadata<'py>(&self, py: Python<'py>) -> Bound<'py, PyDict> {
        PyDict::new(py)
    }
    /// The equivalent `EnvironmentalState` (parameters become metadata).
    fn to_environmental_state(&self) -> PyEnvironmentalState {
        PyEnvironmentalState { inner: self.inner.to_environmental_state() }
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "MonitorState",
            &[
                ("id", obj(py, &self.inner.id)?),
                ("name", obj(py, &self.inner.name)?),
                ("parameters", self.parameters(py)?),
            ],
        )
    }
}

/// A monitor definition.
#[pyclass(frozen, module = "hwtest_core.spec", name = "MonitorDef")]
#[derive(Clone)]
pub struct PyMonitorDef {
    inner: MonitorDef,
}

#[pymethods]
impl PyMonitorDef {
    #[getter]
    fn name(&self) -> &str {
        &self.inner.name
    }
    /// `"bounds"` (built in) or `"python"` (custom class).
    #[getter]
    fn kind(&self) -> &'static str {
        match self.inner.kind {
            MonitorKind::Bounds => "bounds",
            MonitorKind::Python { .. } => "python",
        }
    }
    #[getter]
    fn module(&self) -> &str {
        match &self.inner.kind {
            MonitorKind::Bounds => "hwtest_testcase",
            MonitorKind::Python { module, .. } => module,
        }
    }
    #[getter]
    fn class_name(&self) -> &str {
        match &self.inner.kind {
            MonitorKind::Bounds => "Monitor",
            MonitorKind::Python { class, .. } => class,
        }
    }
    #[getter]
    fn kwargs<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        map_to_py(py, &self.inner.kwargs)
    }
    /// `{state: {field: bound}}`, including `default`.
    #[getter]
    fn configuration<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        state_bounds_to_py(py, &self.inner.bounds)
    }
    #[getter]
    fn slot_number(&self) -> Option<u32> {
        self.inner.slot
    }
    #[getter]
    fn is_uut_monitor(&self) -> bool {
        self.inner.is_uut_monitor()
    }
    #[getter]
    fn is_system_monitor(&self) -> bool {
        !self.inner.is_uut_monitor()
    }
    /// The bound for `field_name` in `state_id` (falling back to `default`).
    fn get_bounds<'py>(
        &self,
        py: Python<'py>,
        state_id: &str,
        field_name: &str,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        self.inner.bounds.get(state_id, field_name).map(|b| bound_to_py(py, b.clone())).transpose()
    }
    /// Every field with a bound in any state.
    fn get_all_fields<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PySet>> {
        PySet::new(py, self.inner.bounds.fields())
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "MonitorDef",
            &[
                ("name", obj(py, &self.inner.name)?),
                ("kind", obj(py, self.kind())?),
                ("slot_number", obj(py, self.inner.slot)?),
            ],
        )
    }
}

/// A logger definition.
#[pyclass(frozen, module = "hwtest_core.spec", name = "LoggerDef")]
#[derive(Clone)]
pub struct PyLoggerDef {
    inner: LoggerDef,
}

#[pymethods]
impl PyLoggerDef {
    #[getter]
    fn name(&self) -> &str {
        &self.inner.name
    }
    /// `"csv"`, `"influxdb"` or `"python"`.
    #[getter]
    fn kind(&self) -> &'static str {
        match self.inner.kind {
            LoggerKind::Csv => "csv",
            LoggerKind::InfluxDb => "influxdb",
            LoggerKind::Python { .. } => "python",
        }
    }
    #[getter]
    fn module(&self) -> &str {
        match &self.inner.kind {
            LoggerKind::Csv | LoggerKind::InfluxDb => "hwtest_logger",
            LoggerKind::Python { module, .. } => module,
        }
    }
    #[getter]
    fn class_name(&self) -> &str {
        match &self.inner.kind {
            LoggerKind::Csv => "CsvStreamLogger",
            LoggerKind::InfluxDb => "InfluxDbStreamLogger",
            LoggerKind::Python { class, .. } => class,
        }
    }
    /// Constructor arguments as written (may contain `${VAR}`).
    #[getter]
    fn kwargs<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        map_to_py(py, &self.inner.kwargs)
    }
    /// Constructor arguments with `${VAR}` / `${VAR:default}` resolved.
    fn resolved_kwargs<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        map_to_py(py, &self.inner.resolved_kwargs().map_err(spec_err)?)
    }
    #[getter]
    fn topics(&self) -> Vec<String> {
        self.inner.topics.clone()
    }
    #[getter]
    fn enabled(&self) -> bool {
        self.inner.enabled
    }
    #[getter]
    fn ignore_offsite(&self) -> bool {
        self.inner.ignore_offsite
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "LoggerDef",
            &[
                ("name", obj(py, &self.inner.name)?),
                ("kind", obj(py, self.kind())?),
                ("topics", obj(py, self.topics())?),
            ],
        )
    }
}

/// A validated test definition.
#[pyclass(frozen, module = "hwtest_core.spec", name = "TestDefinition")]
pub struct PyTestDefinition {
    inner: TestDefinition,
}

fn key_error(kind: &str, key: &str) -> PyErr {
    PyKeyError::new_err(format!("{kind} {key:?} is not defined"))
}

#[pymethods]
impl PyTestDefinition {
    /// Tells pytest this is not a test class (its name starts with "Test").
    #[classattr]
    #[allow(non_upper_case_globals)] // Python attribute name
    const __test__: bool = false;
    /// Loads and validates a YAML file.
    #[classmethod]
    fn from_yaml(_cls: &Bound<'_, PyType>, path: PathBuf) -> PyResult<Self> {
        Ok(Self { inner: TestDefinition::from_file(path).map_err(spec_err)? })
    }
    /// Validates a dict with the same structure as the YAML file.
    #[classmethod]
    fn from_dict(_cls: &Bound<'_, PyType>, data: &Bound<'_, PyAny>) -> PyResult<Self> {
        Ok(Self { inner: TestDefinition::from_json(&py_to_value(data)?).map_err(spec_err)? })
    }
    /// Validates YAML text.
    #[classmethod]
    fn from_yaml_str(_cls: &Bound<'_, PyType>, text: &str) -> PyResult<Self> {
        Ok(Self { inner: TestDefinition::from_yaml_str(text, None).map_err(spec_err)? })
    }
    #[getter]
    fn test_case(&self) -> PyTestCaseInfo {
        PyTestCaseInfo { inner: self.inner.test_case.clone() }
    }
    #[getter]
    fn rack_id(&self) -> Option<&str> {
        self.inner.rack.as_deref()
    }
    #[getter]
    fn case_parameters<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        map_to_py(py, &self.inner.case_parameters)
    }
    #[getter]
    fn monitor_states<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in &self.inner.monitor_states {
            d.set_item(k, PyMonitorState { inner: v.clone() })?;
        }
        Ok(d)
    }
    #[getter]
    fn state_sequence(&self) -> Vec<String> {
        self.inner.state_sequence.clone()
    }
    #[getter]
    fn monitors<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in &self.inner.monitors {
            d.set_item(k, PyMonitorDef { inner: v.clone() })?;
        }
        Ok(d)
    }
    #[getter]
    fn loggers<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in &self.inner.loggers {
            d.set_item(k, PyLoggerDef { inner: v.clone() })?;
        }
        Ok(d)
    }
    /// `{requirement: {state: {field: bound}}}`.
    #[getter]
    fn functional_requirements<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in &self.inner.functional_requirements {
            d.set_item(k, state_bounds_to_py(py, v)?)?;
        }
        Ok(d)
    }
    #[getter]
    fn source_path<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        path_to_py(py, self.inner.source_path.as_ref())
    }
    fn get_state(&self, state_id: &str) -> PyResult<PyMonitorState> {
        self.inner
            .monitor_states
            .get(state_id)
            .map(|s| PyMonitorState { inner: s.clone() })
            .ok_or_else(|| key_error("State", state_id))
    }
    fn get_monitor(&self, monitor_name: &str) -> PyResult<PyMonitorDef> {
        self.inner
            .monitors
            .get(monitor_name)
            .map(|m| PyMonitorDef { inner: m.clone() })
            .ok_or_else(|| key_error("Monitor", monitor_name))
    }
    fn get_logger(&self, logger_name: &str) -> PyResult<PyLoggerDef> {
        self.inner
            .loggers
            .get(logger_name)
            .map(|l| PyLoggerDef { inner: l.clone() })
            .ok_or_else(|| key_error("Logger", logger_name))
    }
    #[pyo3(signature = (offsite = false))]
    fn get_enabled_loggers(&self, offsite: bool) -> Vec<PyLoggerDef> {
        self.inner
            .enabled_loggers(offsite)
            .into_iter()
            .map(|l| PyLoggerDef { inner: l.clone() })
            .collect()
    }
    /// A parameter: the state's override if present, else `case_parameters`.
    #[pyo3(signature = (name, state_id = None))]
    fn get_parameter<'py>(
        &self,
        py: Python<'py>,
        name: &str,
        state_id: Option<&str>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let v = self.inner.parameter(name, state_id).ok_or_else(|| key_error("Parameter", name))?;
        value_to_py(py, v)
    }
    fn get_states_in_sequence(&self) -> Vec<PyMonitorState> {
        self.inner
            .states_in_sequence()
            .into_iter()
            .map(|s| PyMonitorState { inner: s.clone() })
            .collect()
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "TestDefinition",
            &[("test_case", obj(py, self.test_case())?), ("source_path", self.source_path(py)?)],
        )
    }
}

/// State-dependent bounds monitor built from a `MonitorDef`.
#[pyclass(module = "hwtest_core.spec", name = "Monitor")]
pub struct PyMonitor {
    def: MonitorDef,
    engine: BoundsMonitor,
}

#[pymethods]
impl PyMonitor {
    #[new]
    #[pyo3(signature = (monitor_def, monitor_id = None))]
    fn new(monitor_def: PyRef<'_, PyMonitorDef>, monitor_id: Option<String>) -> PyResult<Self> {
        let def = monitor_def.inner.clone();
        if let MonitorKind::Python { module, class } = &def.kind {
            return Err(type_error(format!(
                "monitor {:?} is implemented by Python class {module}.{class}, not the built-in bounds monitor",
                def.name
            )));
        }
        let mut engine = def.create_monitor();
        if let Some(id) = monitor_id {
            engine.monitor_id = id;
        }
        Ok(Self { def, engine })
    }
    #[getter]
    fn monitor_id(&self) -> &str {
        &self.engine.monitor_id
    }
    #[getter]
    fn monitor_def(&self) -> PyMonitorDef {
        PyMonitorDef { inner: self.def.clone() }
    }
    #[getter]
    fn is_uut_monitor(&self) -> bool {
        self.def.is_uut_monitor()
    }
    #[getter]
    fn is_system_monitor(&self) -> bool {
        !self.def.is_uut_monitor()
    }
    #[getter]
    fn slot_number(&self) -> Option<u32> {
        self.def.slot
    }
    /// Evaluates `{field: value}` against the bounds for `state`.
    fn evaluate(
        &mut self,
        values: &Bound<'_, PyAny>,
        state: PyRef<'_, PyEnvironmentalState>,
    ) -> PyResult<PyMonitorResult> {
        let mut map: Vec<(String, f64)> = Vec::new();
        for item in values.call_method0("items")?.try_iter()? {
            let (k, v): (String, f64) = item?.extract()?;
            map.push((k, v));
        }
        let lookup = |f: &str| map.iter().find(|(k, _)| k == f).map(|(_, v)| *v);
        let inner = self.engine.evaluate(lookup, &state.inner, &Timestamp::now("local"));
        Ok(PyMonitorResult { inner })
    }
    /// Evaluates a single field.
    fn evaluate_single(
        &mut self,
        field_name: &str,
        value: f64,
        state: PyRef<'_, PyEnvironmentalState>,
    ) -> PyMonitorResult {
        let inner = self.engine.evaluate(
            |f| (f == field_name).then_some(value),
            &state.inner,
            &Timestamp::now("local"),
        );
        PyMonitorResult { inner }
    }
    /// `{field: {"bound_type", "value", "interval"?}}` for `state`.
    fn get_bounds_info<'py>(
        &self,
        py: Python<'py>,
        state: PyRef<'_, PyEnvironmentalState>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let out = PyDict::new(py);
        for field in self.engine.bounds().fields() {
            let Some(b) = self.engine.bounds().get(&state.inner.state_id, field) else { continue };
            let info = PyDict::new(py);
            info.set_item("bound_type", b.key())?;
            info.set_item("value", value_to_py(py, &b.args())?)?;
            if let Some((lo, hi)) = b.interval() {
                info.set_item("interval", PyTuple::new(py, [lo, hi])?)?;
            }
            out.set_item(field, info)?;
        }
        Ok(out)
    }
    /// Unlocks all `within_baseline` checks.
    fn reset(&mut self) {
        self.engine = self.def.create_monitor_with_id(&self.engine.monitor_id);
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "Monitor",
            &[
                ("monitor_id", obj(py, &self.engine.monitor_id)?),
                ("slot_number", obj(py, self.def.slot)?),
            ],
        )
    }
}

/// Finds a definition file for `test_id`; `None` if not found.
#[pyfunction]
#[pyo3(signature = (test_id, search_paths = None))]
pub fn find_definition_file<'py>(
    py: Python<'py>,
    test_id: &str,
    search_paths: Option<&Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyAny>> {
    let dirs = paths_arg(search_paths)?;
    path_to_py(py, testdef::find_definition_file(test_id, dirs.as_deref()).as_ref())
}

/// Loads a definition from `path`, or finds it by `test_id`.
#[pyfunction]
#[pyo3(signature = (test_id, path = None, search_paths = None))]
pub fn load_definition(
    test_id: &str,
    path: Option<PathBuf>,
    search_paths: Option<&Bound<'_, PyAny>>,
) -> PyResult<PyTestDefinition> {
    let dirs = paths_arg(search_paths)?;
    Ok(PyTestDefinition {
        inner: testdef::load_definition(test_id, path.as_deref(), dirs.as_deref())
            .map_err(spec_err)?,
    })
}

/// Validates test definition files; returns `[(path, error_or_None)]`.
#[pyfunction]
pub fn check_definitions(py: Python<'_>, paths: Vec<PathBuf>) -> PyResult<Bound<'_, PyList>> {
    let rows = paths
        .into_iter()
        .map(|p| {
            let err = TestDefinition::from_file(&p).err().map(|e| e.to_string());
            (p, err)
        })
        .collect::<Vec<_>>();
    PyList::new(py, rows)
}
