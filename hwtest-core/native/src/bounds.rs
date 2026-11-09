//! Bound check classes (`WithinRange`, `GoodValues`, ...) and
//! `bound_check_from_dict`.

use hwtest_core::bounds::{
    BadInterval, BadValues, BoundCheck, GoodInterval, GoodValues, GreaterThan, LessThan, Special,
    WithinBaseline, WithinRange, WithinTolerance,
};
use pyo3::prelude::*;
use pyo3::types::{PyFrozenSet, PyType};

use crate::conv::{
    hash_floats, obj, py_to_value, repr_kw, value_to_py, CoreResultExt, ThresholdError,
};

/// Parses `data` and requires it to be of the expected bound type.
fn parse_as(data: &Bound<'_, PyAny>, key: &str) -> PyResult<BoundCheck> {
    let check = BoundCheck::from_json(&py_to_value(data)?).py_err()?;
    if check.key() == key {
        Ok(check)
    } else {
        Err(ThresholdError::new_err(format!("Expected a {key:?} bound, got {:?}", check.key())))
    }
}

fn to_dict_of(py: Python<'_>, check: BoundCheck) -> PyResult<Bound<'_, PyAny>> {
    value_to_py(py, &check.to_json())
}

fn int_set(values: &Bound<'_, PyAny>) -> PyResult<Vec<i64>> {
    values.try_iter()?.map(|v| v?.extract::<i64>()).collect()
}

/// Defines a frozen pyclass wrapping a float-only bound check.
macro_rules! float_bound {
    (
        $py:ident, $name:literal, $core:ident,
        new($($arg:ident),*) => $ctor:expr,
        fields { $($field:ident),* }
        $(, extra { $($extra:tt)* })?
    ) => {
        #[doc = concat!("The `", $name, "` bound check.")]
        #[pyclass(frozen, eq, module = "hwtest_core.types.bounds", name = $name)]
        #[derive(Clone, PartialEq)]
        pub struct $py {
            pub inner: $core,
        }

        #[pymethods]
        impl $py {
            #[new]
            fn new($($arg: f64),*) -> PyResult<Self> {
                Ok(Self { inner: $ctor.py_err()? })
            }
            $(
                #[getter]
                fn $field(&self) -> f64 {
                    self.inner.$field()
                }
            )*
            /// Evaluates the check.
            fn check(&self, value: f64) -> bool {
                self.inner.check(value)
            }
            /// Serializes to `{key: args}`.
            fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
                to_dict_of(py, self.inner.into())
            }
            /// Deserializes from `{key: args}`.
            #[classmethod]
            fn from_dict(_cls: &Bound<'_, PyType>, data: &Bound<'_, PyAny>) -> PyResult<Self> {
                match parse_as(data, $core::KEY)? {
                    BoundCheck::$core(inner) => Ok(Self { inner }),
                    _ => unreachable!("parse_as checked the key"),
                }
            }
            fn __hash__(&self) -> isize {
                hash_floats($name, &[$(self.inner.$field()),*])
            }
            fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
                repr_kw($name, &[$((stringify!($field), obj(py, self.inner.$field())?)),*])
            }
            $($($extra)*)?
        }
    };
}

float_bound!(PyWithinTolerance, "WithinTolerance", WithinTolerance,
    new(center, fraction) => WithinTolerance::new(center, fraction),
    fields { center, fraction });
float_bound!(PyWithinRange, "WithinRange", WithinRange,
    new(center, delta) => WithinRange::new(center, delta),
    fields { center, delta });
float_bound!(PyLessThan, "LessThan", LessThan,
    new(limit) => Ok::<_, hwtest_core::Error>(LessThan::new(limit)),
    fields { limit });
float_bound!(PyGreaterThan, "GreaterThan", GreaterThan,
    new(limit) => Ok::<_, hwtest_core::Error>(GreaterThan::new(limit)),
    fields { limit });
float_bound!(PyGoodInterval, "GoodInterval", GoodInterval,
    new(low, high) => GoodInterval::new(low, high),
    fields { low, high });
float_bound!(PyBadInterval, "BadInterval", BadInterval,
    new(low, high) => BadInterval::new(low, high),
    fields { low, high });

/// Two-phase baseline check (stateful: locks on first pass).
#[pyclass(eq, module = "hwtest_core.types.bounds", name = "WithinBaseline")]
#[derive(Clone, PartialEq)]
pub struct PyWithinBaseline {
    pub inner: WithinBaseline,
}

#[pymethods]
impl PyWithinBaseline {
    #[new]
    fn new(nominal: f64, init_delta: f64, tight_delta: f64) -> PyResult<Self> {
        Ok(Self { inner: WithinBaseline::new(nominal, init_delta, tight_delta).py_err()? })
    }
    #[getter]
    fn nominal(&self) -> f64 {
        self.inner.nominal()
    }
    #[getter]
    fn init_delta(&self) -> f64 {
        self.inner.init_delta()
    }
    #[getter]
    fn tight_delta(&self) -> f64 {
        self.inner.tight_delta()
    }
    #[getter]
    fn is_locked(&self) -> bool {
        self.inner.is_locked()
    }
    #[getter]
    fn baseline_value(&self) -> Option<f64> {
        self.inner.baseline()
    }
    /// Clears the locked baseline.
    fn reset(&mut self) {
        self.inner.reset();
    }
    /// Evaluates the check, locking the baseline on the first pass.
    fn check(&mut self, value: f64) -> bool {
        self.inner.check(value)
    }
    /// Serializes to `{"within_baseline": [nominal, init, tight(, baseline)]}`.
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        to_dict_of(py, self.inner.into())
    }
    /// Deserializes, restoring the baseline if present.
    #[classmethod]
    fn from_dict(_cls: &Bound<'_, PyType>, data: &Bound<'_, PyAny>) -> PyResult<Self> {
        match parse_as(data, WithinBaseline::KEY)? {
            BoundCheck::WithinBaseline(inner) => Ok(Self { inner }),
            _ => unreachable!("parse_as checked the key"),
        }
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw(
            "WithinBaseline",
            &[
                ("nominal", obj(py, self.inner.nominal())?),
                ("init_delta", obj(py, self.inner.init_delta())?),
                ("tight_delta", obj(py, self.inner.tight_delta())?),
            ],
        )
    }
}

/// Defines a frozen pyclass wrapping an integer-set bound check.
macro_rules! set_bound {
    ($py:ident, $name:literal, $core:ident) => {
        #[doc = concat!("The `", $name, "` bound check.")]
        #[pyclass(frozen, eq, hash, module = "hwtest_core.types.bounds", name = $name)]
        #[derive(Clone, PartialEq, Eq, Hash)]
        pub struct $py {
            pub inner: $core,
        }

        #[pymethods]
        impl $py {
            #[new]
            fn new(values: &Bound<'_, PyAny>) -> PyResult<Self> {
                Ok(Self { inner: $core::new(int_set(values)?) })
            }
            /// The integer set as a `frozenset`.
            #[getter]
            fn values<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyFrozenSet>> {
                PyFrozenSet::new(py, self.inner.values().iter())
            }
            /// Evaluates the check (value is rounded half-to-even first).
            fn check(&self, value: f64) -> bool {
                self.inner.check(value)
            }
            /// Serializes to `{key: sorted list}`.
            fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
                to_dict_of(py, self.inner.clone().into())
            }
            /// Deserializes from `{key: list}`.
            #[classmethod]
            fn from_dict(_cls: &Bound<'_, PyType>, data: &Bound<'_, PyAny>) -> PyResult<Self> {
                match parse_as(data, $core::KEY)? {
                    BoundCheck::$core(inner) => Ok(Self { inner }),
                    _ => unreachable!("parse_as checked the key"),
                }
            }
            fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
                repr_kw($name, &[("values", self.values(py)?.into_any())])
            }
        }
    };
}

set_bound!(PyGoodValues, "GoodValues", GoodValues);
set_bound!(PyBadValues, "BadValues", BadValues);

/// Special bound check (`kind="any"` always passes).
#[pyclass(frozen, eq, hash, module = "hwtest_core.types.bounds", name = "Special")]
#[derive(Clone, PartialEq, Eq, Hash)]
pub struct PySpecial {
    pub inner: Special,
}

#[pymethods]
impl PySpecial {
    #[new]
    fn new(kind: String) -> PyResult<Self> {
        Ok(Self { inner: Special::new(kind).py_err()? })
    }
    #[getter]
    fn kind(&self) -> &str {
        self.inner.kind()
    }
    /// Evaluates the check (always true for `any`).
    fn check(&self, value: f64) -> bool {
        self.inner.check(value)
    }
    /// Serializes to `{"special": kind}`.
    fn to_dict<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        to_dict_of(py, self.inner.clone().into())
    }
    /// Deserializes from `{"special": kind}`.
    #[classmethod]
    fn from_dict(_cls: &Bound<'_, PyType>, data: &Bound<'_, PyAny>) -> PyResult<Self> {
        match parse_as(data, Special::KEY)? {
            BoundCheck::Special(inner) => Ok(Self { inner }),
            _ => unreachable!("parse_as checked the key"),
        }
    }
    fn __repr__(&self, py: Python<'_>) -> PyResult<String> {
        repr_kw("Special", &[("kind", obj(py, self.inner.kind())?)])
    }
}

/// Wraps a core bound check in the matching Python class.
pub fn bound_to_py(py: Python<'_>, check: BoundCheck) -> PyResult<Bound<'_, PyAny>> {
    Ok(match check {
        BoundCheck::WithinTolerance(inner) => {
            Bound::new(py, PyWithinTolerance { inner })?.into_any()
        }
        BoundCheck::WithinRange(inner) => Bound::new(py, PyWithinRange { inner })?.into_any(),
        BoundCheck::WithinBaseline(inner) => Bound::new(py, PyWithinBaseline { inner })?.into_any(),
        BoundCheck::LessThan(inner) => Bound::new(py, PyLessThan { inner })?.into_any(),
        BoundCheck::GreaterThan(inner) => Bound::new(py, PyGreaterThan { inner })?.into_any(),
        BoundCheck::GoodInterval(inner) => Bound::new(py, PyGoodInterval { inner })?.into_any(),
        BoundCheck::BadInterval(inner) => Bound::new(py, PyBadInterval { inner })?.into_any(),
        BoundCheck::GoodValues(inner) => Bound::new(py, PyGoodValues { inner })?.into_any(),
        BoundCheck::BadValues(inner) => Bound::new(py, PyBadValues { inner })?.into_any(),
        BoundCheck::Special(inner) => Bound::new(py, PySpecial { inner })?.into_any(),
    })
}

/// Creates the right bound check class from `{key: args}`.
#[pyfunction]
pub fn bound_check_from_dict<'py>(data: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    bound_to_py(data.py(), BoundCheck::from_json(&py_to_value(data)?).py_err()?)
}
