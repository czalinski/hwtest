//! Shared conversion helpers: errors, JSON <-> Python, enums, reprs.

use std::hash::{DefaultHasher, Hash, Hasher};

use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use pyo3::BoundObject;
use serde::de::DeserializeOwned;
use serde::Serialize;
use serde_json::{Map, Number, Value};

pyo3::import_exception!(hwtest_core.errors, ThresholdError);
pyo3::import_exception!(hwtest_core.errors, SerializationError);
pyo3::import_exception!(hwtest_core.errors, SpecError);

/// Maps a core error onto the Python exception hierarchy.
pub fn to_pyerr(err: hwtest_core::Error) -> PyErr {
    match err {
        hwtest_core::Error::Threshold(m) => ThresholdError::new_err(m),
        hwtest_core::Error::Protocol(m) => PyValueError::new_err(m),
        hwtest_core::Error::Serialization(m) => SerializationError::new_err(m),
    }
}

/// `.py_err()` on core results.
pub trait CoreResultExt<T> {
    /// Converts the error into a Python exception.
    fn py_err(self) -> PyResult<T>;
}

impl<T> CoreResultExt<T> for hwtest_core::Result<T> {
    fn py_err(self) -> PyResult<T> {
        self.map_err(to_pyerr)
    }
}

fn ser_err(err: &serde_json::Error) -> PyErr {
    SerializationError::new_err(err.to_string())
}

/// Converts a JSON value into the equivalent Python object.
pub fn value_to_py<'py>(py: Python<'py>, v: &Value) -> PyResult<Bound<'py, PyAny>> {
    Ok(match v {
        Value::Null => py.None().into_bound(py),
        Value::Bool(b) => PyBool::new(py, *b).to_owned().into_any(),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i.into_pyobject(py)?.into_any()
            } else if let Some(u) = n.as_u64() {
                u.into_pyobject(py)?.into_any()
            } else {
                n.as_f64().unwrap_or(f64::NAN).into_pyobject(py)?.into_any()
            }
        }
        Value::String(s) => PyString::new(py, s).into_any(),
        Value::Array(items) => {
            let items = items.iter().map(|x| value_to_py(py, x)).collect::<PyResult<Vec<_>>>()?;
            PyList::new(py, items)?.into_any()
        }
        Value::Object(map) => {
            let d = PyDict::new(py);
            for (k, x) in map {
                d.set_item(k, value_to_py(py, x)?)?;
            }
            d.into_any()
        }
    })
}

/// Converts a JSON-compatible Python object (dict/list/tuple/str/int/float/
/// bool/None, or an `Enum` member) into a JSON value. Non-finite floats
/// become `null`.
pub fn py_to_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    if obj.is_none() {
        return Ok(Value::Null);
    }
    if let Ok(b) = obj.cast::<PyBool>() {
        return Ok(Value::Bool(b.is_true()));
    }
    if obj.is_instance_of::<PyInt>() {
        if let Ok(i) = obj.extract::<i64>() {
            return Ok(Value::from(i));
        }
        if let Ok(u) = obj.extract::<u64>() {
            return Ok(Value::from(u));
        }
        return Err(SerializationError::new_err(format!("Integer out of 64-bit range: {obj}")));
    }
    if let Ok(f) = obj.cast::<PyFloat>() {
        return Ok(Number::from_f64(f.value()).map_or(Value::Null, Value::Number));
    }
    if obj.is_instance_of::<PyString>() {
        return Ok(Value::String(obj.extract()?));
    }
    if let Ok(d) = obj.cast::<PyDict>() {
        return dict_items_to_value(d.items().as_any());
    }
    if obj.is_instance_of::<PyList>() || obj.is_instance_of::<PyTuple>() {
        return obj
            .try_iter()?
            .map(|x| py_to_value(&x?))
            .collect::<PyResult<Vec<_>>>()
            .map(Value::Array);
    }
    // Enum members (e.g. a ValueQuality stored in metadata) serialize as their value.
    let enum_type = obj.py().import("enum")?.getattr("Enum")?;
    if obj.is_instance(&enum_type)? {
        return py_to_value(&obj.getattr("value")?);
    }
    // Other mappings (MappingProxyType, OrderedDict subclasses already handled above).
    if obj.hasattr("items")? && obj.hasattr("keys")? {
        return dict_items_to_value(&obj.call_method0("items")?);
    }
    Err(SerializationError::new_err(format!(
        "Object of type {} is not JSON serializable",
        obj.get_type().name()?
    )))
}

fn dict_items_to_value(items: &Bound<'_, PyAny>) -> PyResult<Value> {
    let mut map = Map::new();
    for item in items.try_iter()? {
        let (k, v): (Bound<'_, PyAny>, Bound<'_, PyAny>) = item?.extract()?;
        let key = if k.is_instance_of::<PyString>() { k.extract()? } else { k.str()?.extract()? };
        map.insert(key, py_to_value(&v)?);
    }
    Ok(Value::Object(map))
}

/// `to_dict()` for any serde type.
pub fn to_dict<'py, T: Serialize>(py: Python<'py>, v: &T) -> PyResult<Bound<'py, PyAny>> {
    value_to_py(py, &serde_json::to_value(v).map_err(|e| ser_err(&e))?)
}

/// `from_dict()` for any serde type.
pub fn from_dict<T: DeserializeOwned>(data: &Bound<'_, PyAny>) -> PyResult<T> {
    serde_json::from_value(py_to_value(data)?).map_err(|e| ser_err(&e))
}

/// `to_bytes()` (UTF-8 JSON) for any serde type.
pub fn to_json_bytes<'py, T: Serialize>(py: Python<'py>, v: &T) -> PyResult<Bound<'py, PyBytes>> {
    Ok(PyBytes::new(py, &serde_json::to_vec(v).map_err(|e| ser_err(&e))?))
}

/// `from_bytes()` (UTF-8 JSON) for any serde type.
pub fn from_json_bytes<T: DeserializeOwned>(data: &[u8]) -> PyResult<T> {
    serde_json::from_slice(data).map_err(|e| ser_err(&e))
}

/// Looks up a Python enum class from `hwtest_core._enums`.
fn enum_class<'py>(py: Python<'py>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    py.import("hwtest_core._enums")?.getattr(name)
}

/// Converts a Rust enum with a string serde representation into the Python
/// enum member of the same value.
pub fn str_enum_to_py<'py, T: Serialize>(
    py: Python<'py>,
    class: &str,
    v: T,
) -> PyResult<Bound<'py, PyAny>> {
    let value = serde_json::to_value(v).map_err(|e| ser_err(&e))?;
    enum_class(py, class)?.call1((value.as_str().unwrap_or_default(),))
}

/// Accepts a Python enum member *or* its raw value (`"good"`) and converts it.
pub fn str_enum_from_py<T: DeserializeOwned>(obj: &Bound<'_, PyAny>, class: &str) -> PyResult<T> {
    let member = enum_class(obj.py(), class)?.call1((obj,))?;
    let value: String = member.getattr("value")?.extract()?;
    serde_json::from_value(Value::String(value)).map_err(|e| ser_err(&e))
}

/// `DataType` member for a core data type.
pub fn data_type_to_py(py: Python<'_>, t: hwtest_core::DataType) -> PyResult<Bound<'_, PyAny>> {
    enum_class(py, "DataType")?.call1((t.code(),))
}

/// Accepts a `DataType` member or its integer code.
pub fn data_type_from_py(obj: &Bound<'_, PyAny>) -> PyResult<hwtest_core::DataType> {
    let member = enum_class(obj.py(), "DataType")?.call1((obj,))?;
    let code: u8 = member.getattr("value")?.extract()?;
    hwtest_core::DataType::from_code(code)
        .ok_or_else(|| PyValueError::new_err(format!("Unknown data type code: {code}")))
}

/// Builds a dataclass-style repr: `Name(a=repr(a), b=repr(b))`.
pub fn repr_kw(name: &str, fields: &[(&str, Bound<'_, PyAny>)]) -> PyResult<String> {
    let parts = fields
        .iter()
        .map(|(k, v)| Ok(format!("{k}={}", v.repr()?)))
        .collect::<PyResult<Vec<_>>>()?;
    Ok(format!("{name}({})", parts.join(", ")))
}

/// Converts any value to a Python object for use in [`repr_kw`].
pub fn obj<'py, T: IntoPyObject<'py>>(py: Python<'py>, v: T) -> PyResult<Bound<'py, PyAny>> {
    v.into_pyobject(py).map(BoundObject::into_any).map(BoundObject::into_bound).map_err(Into::into)
}

/// Hash for float-valued records (`-0.0` and `0.0` hash equal, like Python).
#[allow(clippy::cast_possible_wrap, clippy::cast_possible_truncation)]
pub fn hash_floats(tag: &str, values: &[f64]) -> isize {
    let mut h = DefaultHasher::new();
    tag.hash(&mut h);
    for v in values {
        let v = if *v == 0.0 { 0.0 } else { *v };
        v.to_bits().hash(&mut h);
    }
    h.finish() as isize
}

/// Raises `TypeError` with a consistent message.
pub fn type_error(msg: impl Into<String>) -> PyErr {
    PyTypeError::new_err(msg.into())
}
