//! `Timestamp` and `InstrumentIdentity`.

use hwtest_core::common::LOCAL_SOURCE;
use hwtest_core::{InstrumentIdentity, Timestamp};
use pyo3::prelude::*;
use pyo3::types::PyType;

use crate::conv::{obj, repr_kw};

/// High-resolution timestamp with nanosecond precision and source tracking.
#[pyclass(frozen, eq, hash, ord, module = "hwtest_core.types.common", name = "Timestamp")]
#[derive(Clone, PartialEq, Eq, Hash, PartialOrd)]
pub struct PyTimestamp {
    pub inner: Timestamp,
}

impl From<Timestamp> for PyTimestamp {
    fn from(inner: Timestamp) -> Self {
        Self { inner }
    }
}

fn epoch(py: Python<'_>) -> PyResult<Bound<'_, PyAny>> {
    let dt = py.import("datetime")?;
    let utc = dt.getattr("timezone")?.getattr("utc")?;
    dt.getattr("datetime")?.call1((1970, 1, 1, 0, 0, 0, 0, utc))
}

#[pymethods]
impl PyTimestamp {
    #[new]
    #[pyo3(signature = (unix_ns, source = None))]
    fn new(unix_ns: i64, source: Option<String>) -> Self {
        Timestamp::new(unix_ns, source.unwrap_or_else(|| LOCAL_SOURCE.into())).into()
    }

    /// Current wall-clock time.
    #[classmethod]
    #[pyo3(signature = (source = None))]
    fn now(_cls: &Bound<'_, PyType>, source: Option<String>) -> Self {
        Timestamp::now(source.unwrap_or_else(|| LOCAL_SOURCE.into())).into()
    }

    /// Converts a `datetime` exactly (naive datetimes are local time).
    #[classmethod]
    #[pyo3(signature = (dt, source = None))]
    fn from_datetime(
        _cls: &Bound<'_, PyType>,
        dt: &Bound<'_, PyAny>,
        source: Option<String>,
    ) -> PyResult<Self> {
        let py = dt.py();
        let aware = if dt.getattr("tzinfo")?.is_none() || dt.call_method0("utcoffset")?.is_none() {
            dt.call_method0("astimezone")?
        } else {
            dt.clone()
        };
        let delta = aware.sub(epoch(py)?)?;
        let days: i64 = delta.getattr("days")?.extract()?;
        let secs: i64 = delta.getattr("seconds")?.extract()?;
        let micros: i64 = delta.getattr("microseconds")?.extract()?;
        let unix_ns = (days * 86_400 + secs) * 1_000_000_000 + micros * 1_000;
        Ok(Timestamp::new(unix_ns, source.unwrap_or_else(|| LOCAL_SOURCE.into())).into())
    }

    /// UTC `datetime` (rounded half-to-even to microseconds).
    fn to_datetime<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let ns = self.inner.unix_ns;
        let (q, r) = (ns.div_euclid(1_000), ns.rem_euclid(1_000));
        let micros = if r > 500 || (r == 500 && q % 2 != 0) { q + 1 } else { q };
        let td = py.import("datetime")?.getattr("timedelta")?.call1((0, 0, micros))?;
        epoch(py)?.add(td)
    }

    #[getter]
    fn unix_ns(&self) -> i64 {
        self.inner.unix_ns
    }
    #[getter]
    fn source(&self) -> &str {
        &self.inner.source
    }
    #[getter]
    fn unix_seconds(&self) -> f64 {
        self.inner.unix_seconds()
    }
    #[getter]
    fn unix_ms(&self) -> i64 {
        self.inner.unix_ms()
    }
    #[getter]
    fn unix_us(&self) -> i64 {
        self.inner.unix_us()
    }

    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "Timestamp",
            &[("unix_ns", obj(py, self.inner.unix_ns)?), ("source", obj(py, &self.inner.source)?)],
        )
    }
}

/// Instrument identification (the four `*IDN?` fields).
#[pyclass(frozen, eq, hash, module = "hwtest_core.types.common", name = "InstrumentIdentity")]
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct PyInstrumentIdentity {
    pub inner: InstrumentIdentity,
}

#[pymethods]
impl PyInstrumentIdentity {
    #[new]
    fn new(manufacturer: String, model: String, serial: String, firmware: String) -> Self {
        Self { inner: InstrumentIdentity { manufacturer, model, serial, firmware } }
    }
    #[getter]
    fn manufacturer(&self) -> &str {
        &self.inner.manufacturer
    }
    #[getter]
    fn model(&self) -> &str {
        &self.inner.model
    }
    #[getter]
    fn serial(&self) -> &str {
        &self.inner.serial
    }
    #[getter]
    fn firmware(&self) -> &str {
        &self.inner.firmware
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        let i = &self.inner;
        repr_kw(
            "InstrumentIdentity",
            &[
                ("manufacturer", obj(py, &i.manufacturer)?),
                ("model", obj(py, &i.model)?),
                ("serial", obj(py, &i.serial)?),
                ("firmware", obj(py, &i.firmware)?),
            ],
        )
    }
}
