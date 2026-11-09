//! JSON-transported records: telemetry, state, threshold and monitor types.

use hwtest_core::threshold::BoundType;
use hwtest_core::{
    EnvironmentalState, MonitorResult, MonitorVerdict, StateThresholds, StateTransition,
    TelemetryMessage, TelemetryValue, Threshold, ThresholdBound, ThresholdViolation, ValueQuality,
};
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;
use pyo3::types::{PyBytes, PyDict, PyTuple, PyType};
use serde_json::Value;

use crate::common::PyTimestamp;
use crate::conv::{
    from_dict, from_json_bytes, hash_floats, obj, py_to_value, repr_kw, str_enum_from_py,
    str_enum_to_py, to_dict, to_json_bytes, type_error, value_to_py,
};

/// Adds `to_dict`/`from_dict`/`to_bytes`/`from_bytes` to a wrapper whose
/// `inner` field is a serde type (an extra `#[pymethods]` block).
macro_rules! json_methods {
    ($py:ident, $inner:ty) => {
        #[pymethods]
        impl $py {
            /// Serializes to a JSON-compatible dict.
            fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
                to_dict(py, &self.inner)
            }
            /// Deserializes from a dict.
            #[classmethod]
            fn from_dict(_cls: &Bound<'_, PyType>, data: &Bound<'_, PyAny>) -> PyResult<Self> {
                Ok(Self { inner: from_dict::<$inner>(data)? })
            }
            /// Serializes to UTF-8 JSON bytes.
            fn to_bytes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
                to_json_bytes(py, &self.inner)
            }
            /// Deserializes from UTF-8 JSON bytes.
            #[classmethod]
            fn from_bytes(_cls: &Bound<'_, PyType>, data: PyBackedBytes) -> PyResult<Self> {
                Ok(Self { inner: from_json_bytes::<$inner>(&data)? })
            }
        }
    };
}

fn timestamp(ts: &Bound<'_, PyAny>) -> PyResult<hwtest_core::Timestamp> {
    ts.cast::<PyTimestamp>().map(|t| t.get().inner.clone()).map_err(|_| {
        type_error(format!(
            "expected Timestamp, got {}",
            ts.get_type().name().map_or_else(|_| "?".into(), |n| n.to_string())
        ))
    })
}

// --------------------------------------------------------------------------
// Telemetry
// --------------------------------------------------------------------------

/// A single measurement with metadata.
#[pyclass(frozen, eq, module = "hwtest_core.types.telemetry", name = "TelemetryValue")]
#[derive(Clone, PartialEq)]
pub struct PyTelemetryValue {
    pub inner: TelemetryValue,
}

json_methods!(PyTelemetryValue, TelemetryValue);

#[pymethods]
impl PyTelemetryValue {
    #[new]
    #[pyo3(signature = (channel, value, unit, source_timestamp, publish_timestamp = None, quality = None))]
    fn new(
        channel: String,
        value: f64,
        unit: String,
        source_timestamp: &Bound<'_, PyAny>,
        publish_timestamp: Option<&Bound<'_, PyAny>>,
        quality: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: TelemetryValue {
                channel,
                value,
                unit,
                source_timestamp: timestamp(source_timestamp)?,
                publish_timestamp: publish_timestamp
                    .filter(|t| !t.is_none())
                    .map(timestamp)
                    .transpose()?,
                quality: quality
                    .map_or(Ok(ValueQuality::Good), |q| str_enum_from_py(q, "ValueQuality"))?,
            },
        })
    }
    #[getter]
    fn channel(&self) -> &str {
        &self.inner.channel
    }
    #[getter]
    fn value(&self) -> f64 {
        self.inner.value
    }
    #[getter]
    fn unit(&self) -> &str {
        &self.inner.unit
    }
    #[getter]
    fn source_timestamp(&self) -> PyTimestamp {
        self.inner.source_timestamp.clone().into()
    }
    #[getter]
    fn publish_timestamp(&self) -> Option<PyTimestamp> {
        self.inner.publish_timestamp.clone().map(Into::into)
    }
    #[getter]
    fn quality<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        str_enum_to_py(py, "ValueQuality", self.inner.quality)
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "TelemetryValue",
            &[
                ("channel", obj(py, &self.inner.channel)?),
                ("value", obj(py, self.inner.value)?),
                ("unit", obj(py, &self.inner.unit)?),
                ("source_timestamp", obj(py, self.source_timestamp())?),
                ("publish_timestamp", obj(py, self.publish_timestamp())?),
                ("quality", self.quality(py)?),
            ],
        )
    }
}

/// A batch of telemetry values from a single source.
#[pyclass(frozen, eq, module = "hwtest_core.types.telemetry", name = "TelemetryMessage")]
#[derive(Clone, PartialEq)]
pub struct PyTelemetryMessage {
    pub inner: TelemetryMessage,
}

json_methods!(PyTelemetryMessage, TelemetryMessage);

#[pymethods]
impl PyTelemetryMessage {
    #[new]
    fn new(source: String, values: &Bound<'_, PyAny>, sequence: u64) -> PyResult<Self> {
        let values = values
            .try_iter()?
            .map(|v| {
                let v = v?;
                v.cast::<PyTelemetryValue>().map(|t| t.get().inner.clone()).map_err(|_| {
                    type_error("TelemetryMessage values must be TelemetryValue instances")
                })
            })
            .collect::<PyResult<Vec<_>>>()?;
        Ok(Self { inner: TelemetryMessage { source, values, sequence } })
    }
    #[getter]
    fn source(&self) -> &str {
        &self.inner.source
    }
    #[getter]
    fn values<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyTuple>> {
        PyTuple::new(py, self.inner.values.iter().map(|v| PyTelemetryValue { inner: v.clone() }))
    }
    #[getter]
    fn sequence(&self) -> u64 {
        self.inner.sequence
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "TelemetryMessage",
            &[
                ("source", obj(py, &self.inner.source)?),
                ("values", self.values(py)?.into_any()),
                ("sequence", obj(py, self.inner.sequence)?),
            ],
        )
    }
}

// --------------------------------------------------------------------------
// State
// --------------------------------------------------------------------------

/// A discrete environmental condition.
#[pyclass(frozen, eq, module = "hwtest_core.types.state", name = "EnvironmentalState")]
#[derive(Clone, PartialEq)]
pub struct PyEnvironmentalState {
    pub inner: EnvironmentalState,
}

json_methods!(PyEnvironmentalState, EnvironmentalState);

#[pymethods]
impl PyEnvironmentalState {
    #[new]
    #[pyo3(signature = (state_id, name, description, is_transition = false, metadata = None))]
    fn new(
        state_id: String,
        name: String,
        description: String,
        is_transition: bool,
        metadata: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        let metadata = match metadata.map(py_to_value).transpose()? {
            None | Some(Value::Null) => serde_json::Map::new(),
            Some(Value::Object(m)) => m,
            Some(_) => return Err(type_error("metadata must be a mapping")),
        };
        Ok(Self {
            inner: EnvironmentalState { state_id, name, description, is_transition, metadata },
        })
    }
    #[getter]
    fn state_id(&self) -> &str {
        &self.inner.state_id
    }
    #[getter]
    fn name(&self) -> &str {
        &self.inner.name
    }
    #[getter]
    fn description(&self) -> &str {
        &self.inner.description
    }
    #[getter]
    fn is_transition(&self) -> bool {
        self.inner.is_transition
    }
    /// A copy of the metadata as a dict.
    #[getter]
    fn metadata<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        value_to_py(py, &Value::Object(self.inner.metadata.clone()))
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "EnvironmentalState",
            &[
                ("state_id", obj(py, &self.inner.state_id)?),
                ("name", obj(py, &self.inner.name)?),
                ("description", obj(py, &self.inner.description)?),
                ("is_transition", obj(py, self.inner.is_transition)?),
                ("metadata", self.metadata(py)?),
            ],
        )
    }
}

/// A change from one state to another.
#[pyclass(frozen, eq, module = "hwtest_core.types.state", name = "StateTransition")]
#[derive(Clone, PartialEq)]
pub struct PyStateTransition {
    pub inner: StateTransition,
}

json_methods!(PyStateTransition, StateTransition);

#[pymethods]
impl PyStateTransition {
    #[new]
    #[pyo3(signature = (from_state, to_state, timestamp, reason = String::new()))]
    fn new(
        from_state: Option<String>,
        to_state: String,
        timestamp: &Bound<'_, PyAny>,
        reason: String,
    ) -> PyResult<Self> {
        Ok(Self {
            inner: StateTransition {
                from_state,
                to_state,
                timestamp: self::timestamp(timestamp)?,
                reason,
            },
        })
    }
    #[getter]
    fn from_state(&self) -> Option<&str> {
        self.inner.from_state.as_deref()
    }
    #[getter]
    fn to_state(&self) -> &str {
        &self.inner.to_state
    }
    #[getter]
    fn timestamp(&self) -> PyTimestamp {
        self.inner.timestamp.clone().into()
    }
    #[getter]
    fn reason(&self) -> &str {
        &self.inner.reason
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "StateTransition",
            &[
                ("from_state", obj(py, self.from_state())?),
                ("to_state", obj(py, &self.inner.to_state)?),
                ("timestamp", obj(py, self.timestamp())?),
                ("reason", obj(py, &self.inner.reason)?),
            ],
        )
    }
}

// --------------------------------------------------------------------------
// Thresholds
// --------------------------------------------------------------------------

/// One side of a threshold.
#[pyclass(frozen, eq, module = "hwtest_core.types.threshold", name = "ThresholdBound")]
#[derive(Clone, PartialEq)]
pub struct PyThresholdBound {
    pub inner: ThresholdBound,
}

#[pymethods]
impl PyThresholdBound {
    #[new]
    #[pyo3(signature = (value, bound_type = None))]
    fn new(value: f64, bound_type: Option<&Bound<'_, PyAny>>) -> PyResult<Self> {
        let bound_type =
            bound_type.map_or(Ok(BoundType::Inclusive), |b| str_enum_from_py(b, "BoundType"))?;
        Ok(Self { inner: ThresholdBound { value, bound_type } })
    }
    #[getter]
    fn value(&self) -> f64 {
        self.inner.value
    }
    #[getter]
    fn bound_type<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        str_enum_to_py(py, "BoundType", self.inner.bound_type)
    }
    fn check_low(&self, test_value: f64) -> bool {
        self.inner.check_low(test_value)
    }
    fn check_high(&self, test_value: f64) -> bool {
        self.inner.check_high(test_value)
    }
    /// Serializes to a dict.
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        to_dict(py, &self.inner)
    }
    /// Deserializes from a dict.
    #[classmethod]
    fn from_dict(_cls: &Bound<'_, PyType>, data: &Bound<'_, PyAny>) -> PyResult<Self> {
        Ok(Self { inner: from_dict(data)? })
    }
    fn __hash__(&self) -> isize {
        let tag = if self.inner.bound_type == BoundType::Inclusive { "i" } else { "e" };
        hash_floats(tag, &[self.inner.value])
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "ThresholdBound",
            &[("value", obj(py, self.inner.value)?), ("bound_type", self.bound_type(py)?)],
        )
    }
}

fn bound_arg(b: Option<&Bound<'_, PyAny>>) -> PyResult<Option<ThresholdBound>> {
    b.filter(|b| !b.is_none())
        .map(|b| {
            b.cast::<PyThresholdBound>()
                .map(|t| t.get().inner)
                .map_err(|_| type_error("Threshold low/high must be ThresholdBound or None"))
        })
        .transpose()
}

/// Low/high limits for one channel.
#[pyclass(frozen, eq, module = "hwtest_core.types.threshold", name = "Threshold")]
#[derive(Clone, PartialEq)]
pub struct PyThreshold {
    pub inner: Threshold,
}

#[pymethods]
impl PyThreshold {
    #[new]
    #[pyo3(signature = (channel, low = None, high = None))]
    fn new(
        channel: String,
        low: Option<&Bound<'_, PyAny>>,
        high: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        Ok(Self { inner: Threshold::new(channel, bound_arg(low)?, bound_arg(high)?) })
    }
    #[getter]
    fn channel(&self) -> &str {
        &self.inner.channel
    }
    #[getter]
    fn low(&self) -> Option<PyThresholdBound> {
        self.inner.low.map(|inner| PyThresholdBound { inner })
    }
    #[getter]
    fn high(&self) -> Option<PyThresholdBound> {
        self.inner.high.map(|inner| PyThresholdBound { inner })
    }
    fn check(&self, value: f64) -> bool {
        self.inner.check(value)
    }
    /// Serializes to a dict.
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        to_dict(py, &self.inner)
    }
    /// Deserializes from a dict.
    #[classmethod]
    fn from_dict(_cls: &Bound<'_, PyType>, data: &Bound<'_, PyAny>) -> PyResult<Self> {
        Ok(Self { inner: from_dict(data)? })
    }
    fn __hash__(&self) -> isize {
        let side = |b: Option<ThresholdBound>| b.map_or(f64::NAN, |b| b.value);
        hash_floats(&self.inner.channel, &[side(self.inner.low), side(self.inner.high)])
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "Threshold",
            &[
                ("channel", obj(py, &self.inner.channel)?),
                ("low", obj(py, self.low())?),
                ("high", obj(py, self.high())?),
            ],
        )
    }
}

/// All channel thresholds for one state.
#[pyclass(frozen, eq, module = "hwtest_core.types.threshold", name = "StateThresholds")]
#[derive(Clone, PartialEq)]
pub struct PyStateThresholds {
    pub inner: StateThresholds,
}

json_methods!(PyStateThresholds, StateThresholds);

#[pymethods]
impl PyStateThresholds {
    #[new]
    fn new(state_id: String, thresholds: &Bound<'_, PyAny>) -> PyResult<Self> {
        let mut map = indexmap::IndexMap::new();
        for item in thresholds.call_method0("items")?.try_iter()? {
            let (k, v): (String, Bound<'_, PyAny>) = item?.extract()?;
            let t = v
                .cast::<PyThreshold>()
                .map_err(|_| type_error("StateThresholds values must be Threshold instances"))?;
            map.insert(k, t.get().inner.clone());
        }
        Ok(Self { inner: StateThresholds { state_id, thresholds: map } })
    }
    #[getter]
    fn state_id(&self) -> &str {
        &self.inner.state_id
    }
    /// A dict of channel -> Threshold.
    #[getter]
    fn thresholds<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let d = PyDict::new(py);
        for (k, v) in &self.inner.thresholds {
            d.set_item(k, PyThreshold { inner: v.clone() })?;
        }
        Ok(d)
    }
    fn get_threshold(&self, channel: &str) -> Option<PyThreshold> {
        self.inner.get(channel).map(|t| PyThreshold { inner: t.clone() })
    }
    fn check_value(&self, channel: &str, value: f64) -> Option<bool> {
        self.inner.check_value(channel, value)
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "StateThresholds",
            &[
                ("state_id", obj(py, &self.inner.state_id)?),
                ("thresholds", self.thresholds(py)?.into_any()),
            ],
        )
    }
}

// --------------------------------------------------------------------------
// Monitor results
// --------------------------------------------------------------------------

/// One value outside its threshold.
#[pyclass(frozen, eq, module = "hwtest_core.types.monitor", name = "ThresholdViolation")]
#[derive(Clone, PartialEq)]
pub struct PyThresholdViolation {
    pub inner: ThresholdViolation,
}

#[pymethods]
impl PyThresholdViolation {
    #[new]
    #[pyo3(signature = (channel, value, threshold, message = String::new()))]
    fn new(
        channel: String,
        value: f64,
        threshold: PyRef<'_, PyThreshold>,
        message: String,
    ) -> Self {
        Self {
            inner: ThresholdViolation {
                channel,
                value,
                threshold: threshold.inner.clone(),
                message,
            },
        }
    }
    #[getter]
    fn channel(&self) -> &str {
        &self.inner.channel
    }
    #[getter]
    fn value(&self) -> f64 {
        self.inner.value
    }
    #[getter]
    fn threshold(&self) -> PyThreshold {
        PyThreshold { inner: self.inner.threshold.clone() }
    }
    #[getter]
    fn message(&self) -> &str {
        &self.inner.message
    }
    /// Serializes to a dict.
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        to_dict(py, &self.inner)
    }
    /// Deserializes from a dict.
    #[classmethod]
    fn from_dict(_cls: &Bound<'_, PyType>, data: &Bound<'_, PyAny>) -> PyResult<Self> {
        Ok(Self { inner: from_dict(data)? })
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "ThresholdViolation",
            &[
                ("channel", obj(py, &self.inner.channel)?),
                ("value", obj(py, self.inner.value)?),
                ("threshold", obj(py, self.threshold())?),
                ("message", obj(py, &self.inner.message)?),
            ],
        )
    }
}

/// Result of a monitor evaluation.
#[pyclass(frozen, eq, module = "hwtest_core.types.monitor", name = "MonitorResult")]
#[derive(Clone, PartialEq)]
pub struct PyMonitorResult {
    pub inner: MonitorResult,
}

json_methods!(PyMonitorResult, MonitorResult);

#[pymethods]
impl PyMonitorResult {
    #[new]
    #[pyo3(signature = (monitor_id, verdict, timestamp, state_id, violations = None, message = String::new()))]
    fn new(
        monitor_id: String,
        verdict: &Bound<'_, PyAny>,
        timestamp: &Bound<'_, PyAny>,
        state_id: String,
        violations: Option<&Bound<'_, PyAny>>,
        message: String,
    ) -> PyResult<Self> {
        let violations = match violations {
            None => Vec::new(),
            Some(v) => v
                .try_iter()?
                .map(|v| {
                    let v = v?;
                    v.cast::<PyThresholdViolation>()
                        .map(|t| t.get().inner.clone())
                        .map_err(|_| type_error("violations must be ThresholdViolation instances"))
                })
                .collect::<PyResult<Vec<_>>>()?,
        };
        Ok(Self {
            inner: MonitorResult {
                monitor_id,
                verdict: str_enum_from_py::<MonitorVerdict>(verdict, "MonitorVerdict")?,
                timestamp: self::timestamp(timestamp)?,
                state_id,
                violations,
                message,
            },
        })
    }
    #[getter]
    fn monitor_id(&self) -> &str {
        &self.inner.monitor_id
    }
    #[getter]
    fn verdict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        str_enum_to_py(py, "MonitorVerdict", self.inner.verdict)
    }
    #[getter]
    fn timestamp(&self) -> PyTimestamp {
        self.inner.timestamp.clone().into()
    }
    #[getter]
    fn state_id(&self) -> &str {
        &self.inner.state_id
    }
    #[getter]
    fn violations<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyTuple>> {
        PyTuple::new(
            py,
            self.inner.violations.iter().map(|v| PyThresholdViolation { inner: v.clone() }),
        )
    }
    #[getter]
    fn message(&self) -> &str {
        &self.inner.message
    }
    #[getter]
    fn passed(&self) -> bool {
        self.inner.passed()
    }
    #[getter]
    fn failed(&self) -> bool {
        self.inner.failed()
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "MonitorResult",
            &[
                ("monitor_id", obj(py, &self.inner.monitor_id)?),
                ("verdict", self.verdict(py)?),
                ("timestamp", obj(py, self.timestamp())?),
                ("state_id", obj(py, &self.inner.state_id)?),
                ("violations", self.violations(py)?.into_any()),
                ("message", obj(py, &self.inner.message)?),
            ],
        )
    }
}
