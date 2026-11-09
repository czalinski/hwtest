//! Binary streaming protocol: `StreamField`, `StreamSchema`, `StreamData`.

use std::hash::{Hash, Hasher};

use hwtest_core::streaming::{self, SampleValue};
use hwtest_core::{StreamData, StreamField, StreamSchema};
use pyo3::prelude::*;
use pyo3::pybacked::PyBackedBytes;
use pyo3::sync::PyOnceLock;
use pyo3::types::{PyBytes, PyFloat, PyIterator, PyList, PyTuple, PyType};

use crate::conv::{data_type_from_py, data_type_to_py, obj, repr_kw, type_error, CoreResultExt};

/// Encodes a length-prefixed UTF-8 string (max 255 bytes).
#[pyfunction]
#[pyo3(name = "_encode_string")]
pub fn encode_string<'py>(py: Python<'py>, s: &str) -> PyResult<Bound<'py, PyBytes>> {
    let mut out = Vec::with_capacity(s.len() + 1);
    streaming::encode_string(&mut out, s).py_err()?;
    Ok(PyBytes::new(py, &out))
}

/// Decodes a length-prefixed string, returning `(string, next_offset)`.
#[pyfunction]
#[pyo3(name = "_decode_string")]
pub fn decode_string(data: PyBackedBytes, offset: usize) -> PyResult<(String, usize)> {
    streaming::decode_string(&data, offset).py_err()
}

/// A field definition in a stream schema.
#[pyclass(frozen, eq, hash, module = "hwtest_core.types.streaming", name = "StreamField")]
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct PyStreamField {
    pub inner: StreamField,
}

#[pymethods]
impl PyStreamField {
    #[new]
    #[pyo3(signature = (name, dtype, unit = String::new()))]
    fn new(name: String, dtype: &Bound<'_, PyAny>, unit: String) -> PyResult<Self> {
        Ok(Self { inner: StreamField::new(name, data_type_from_py(dtype)?, unit) })
    }
    #[getter]
    fn name(&self) -> &str {
        &self.inner.name
    }
    #[getter]
    fn dtype<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        data_type_to_py(py, self.inner.dtype)
    }
    #[getter]
    fn unit(&self) -> &str {
        &self.inner.unit
    }
    fn to_bytes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        Ok(PyBytes::new(py, &self.inner.to_bytes().py_err()?))
    }
    /// Decodes a field at `offset`; returns `(field, next_offset)`.
    #[classmethod]
    fn from_bytes(
        _cls: &Bound<'_, PyType>,
        data: PyBackedBytes,
        offset: usize,
    ) -> PyResult<(Self, usize)> {
        let (inner, next) = StreamField::from_bytes(&data, offset).py_err()?;
        Ok((Self { inner }, next))
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "StreamField",
            &[
                ("name", obj(py, &self.inner.name)?),
                ("dtype", self.dtype(py)?),
                ("unit", obj(py, &self.inner.unit)?),
            ],
        )
    }
}

/// Stream schema. The CRC32 `schema_id` is computed on construction.
#[pyclass(frozen, eq, hash, module = "hwtest_core.types.streaming", name = "StreamSchema")]
pub struct PyStreamSchema {
    pub inner: StreamSchema,
    /// The `StreamField` objects, kept so `fields` is cheap and identity-stable.
    fields: Py<PyTuple>,
}

impl PartialEq for PyStreamSchema {
    fn eq(&self, other: &Self) -> bool {
        self.inner == other.inner
    }
}

impl Hash for PyStreamSchema {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.inner.hash(state);
    }
}

impl PyStreamSchema {
    pub fn from_inner(py: Python<'_>, inner: StreamSchema) -> PyResult<Self> {
        let objs = inner
            .fields()
            .iter()
            .map(|f| Py::new(py, PyStreamField { inner: f.clone() }))
            .collect::<PyResult<Vec<_>>>()?;
        Ok(Self { inner, fields: PyTuple::new(py, objs)?.unbind() })
    }
}

#[pymethods]
impl PyStreamSchema {
    #[new]
    fn new(source_id: String, fields: &Bound<'_, PyAny>) -> PyResult<Self> {
        let py = fields.py();
        let mut objs = Vec::new();
        let mut inner_fields = Vec::new();
        for item in fields.try_iter()? {
            let item = item?;
            let field = item
                .cast::<PyStreamField>()
                .map_err(|_| type_error("StreamSchema fields must be StreamField instances"))?;
            inner_fields.push(field.get().inner.clone());
            objs.push(item.clone());
        }
        Ok(Self {
            inner: StreamSchema::new(source_id, inner_fields),
            fields: PyTuple::new(py, objs)?.unbind(),
        })
    }
    #[getter]
    fn source_id(&self) -> &str {
        self.inner.source_id()
    }
    #[getter]
    fn fields(&self, py: Python<'_>) -> Py<PyTuple> {
        self.fields.clone_ref(py)
    }
    #[getter]
    fn schema_id(&self) -> u32 {
        self.inner.schema_id()
    }
    #[getter]
    fn sample_size(&self) -> usize {
        self.inner.sample_size()
    }
    fn get_field_offset(&self, field_name: &str) -> Option<usize> {
        self.inner.field_offset(field_name)
    }
    fn get_field<'py>(
        &self,
        py: Python<'py>,
        field_name: &str,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        self.inner.field_index(field_name).map(|i| self.fields.bind(py).get_item(i)).transpose()
    }
    fn to_bytes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        Ok(PyBytes::new(py, &self.inner.to_bytes().py_err()?))
    }
    #[classmethod]
    fn from_bytes(_cls: &Bound<'_, PyType>, py: Python<'_>, data: PyBackedBytes) -> PyResult<Self> {
        Self::from_inner(py, StreamSchema::from_bytes(&data).py_err()?)
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "StreamSchema",
            &[
                ("source_id", obj(py, self.inner.source_id())?),
                ("fields", self.fields.bind(py).clone().into_any()),
                ("schema_id", obj(py, self.inner.schema_id())?),
            ],
        )
    }
}

fn extract_sample_value(v: &Bound<'_, PyAny>) -> PyResult<SampleValue> {
    if v.is_instance_of::<PyFloat>() {
        return Ok(SampleValue::Float(v.extract()?));
    }
    if let Ok(i) = v.extract::<i64>() {
        return Ok(SampleValue::Int(i));
    }
    if let Ok(u) = v.extract::<u64>() {
        return Ok(SampleValue::UInt(u));
    }
    if let Ok(f) = v.extract::<f64>() {
        return Ok(SampleValue::Float(f));
    }
    Err(type_error(format!("Sample values must be int or float, got {}", v.get_type().name()?)))
}

fn sample_value_to_py(py: Python<'_>, v: SampleValue) -> PyResult<Bound<'_, PyAny>> {
    match v {
        SampleValue::Int(i) => obj(py, i),
        SampleValue::UInt(u) => obj(py, u),
        SampleValue::Float(f) => obj(py, f),
    }
}

/// A batch of evenly spaced samples.
#[pyclass(frozen, eq, module = "hwtest_core.types.streaming", name = "StreamData")]
pub struct PyStreamData {
    pub inner: StreamData,
    /// Lazily built `samples` tuple-of-tuples.
    samples: PyOnceLock<Py<PyTuple>>,
}

impl PartialEq for PyStreamData {
    fn eq(&self, other: &Self) -> bool {
        self.inner == other.inner
    }
}

impl From<StreamData> for PyStreamData {
    fn from(inner: StreamData) -> Self {
        Self { inner, samples: PyOnceLock::new() }
    }
}

#[pymethods]
impl PyStreamData {
    #[new]
    fn new(
        schema_id: u32,
        timestamp_ns: u64,
        period_ns: u64,
        samples: &Bound<'_, PyAny>,
    ) -> PyResult<Self> {
        let mut rows = Vec::new();
        for sample in samples.try_iter()? {
            let row = sample?
                .try_iter()?
                .map(|v| extract_sample_value(&v?))
                .collect::<PyResult<Vec<_>>>()?;
            rows.push(row);
        }
        Ok(StreamData { schema_id, timestamp_ns, period_ns, samples: rows }.into())
    }
    #[getter]
    fn schema_id(&self) -> u32 {
        self.inner.schema_id
    }
    #[getter]
    fn timestamp_ns(&self) -> u64 {
        self.inner.timestamp_ns
    }
    #[getter]
    fn period_ns(&self) -> u64 {
        self.inner.period_ns
    }
    #[getter]
    fn sample_count(&self) -> usize {
        self.inner.sample_count()
    }
    #[getter]
    fn samples(&self, py: Python<'_>) -> PyResult<Py<PyTuple>> {
        self.samples
            .get_or_try_init(py, || {
                let rows = self
                    .inner
                    .samples
                    .iter()
                    .map(|row| {
                        let vals = row
                            .iter()
                            .map(|v| sample_value_to_py(py, *v))
                            .collect::<PyResult<Vec<_>>>()?;
                        PyTuple::new(py, vals)
                    })
                    .collect::<PyResult<Vec<_>>>()?;
                Ok(PyTuple::new(py, rows)?.unbind())
            })
            .map(|t| t.clone_ref(py))
    }
    fn get_timestamp(&self, sample_index: u64) -> u64 {
        self.inner.timestamp(sample_index)
    }
    fn timestamps<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyIterator>> {
        PyList::new(py, self.inner.timestamps())?.try_iter()
    }
    fn to_bytes<'py>(
        &self,
        py: Python<'py>,
        schema: PyRef<'_, PyStreamSchema>,
    ) -> PyResult<Bound<'py, PyBytes>> {
        Ok(PyBytes::new(py, &self.inner.to_bytes(&schema.inner).py_err()?))
    }
    #[classmethod]
    fn from_bytes(
        _cls: &Bound<'_, PyType>,
        data: PyBackedBytes,
        schema: PyRef<'_, PyStreamSchema>,
    ) -> PyResult<Self> {
        Ok(StreamData::from_bytes(&data, &schema.inner).py_err()?.into())
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "StreamData",
            &[
                ("schema_id", obj(py, self.inner.schema_id)?),
                ("timestamp_ns", obj(py, self.inner.timestamp_ns)?),
                ("period_ns", obj(py, self.inner.period_ns)?),
                ("samples", self.samples(py)?.into_bound(py).into_any()),
            ],
        )
    }
}
