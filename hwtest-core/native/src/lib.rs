//! `hwtest_core._native`: `PyO3` bindings over the `hwtest-core` Rust crate.
//!
//! The public Python API lives in the `hwtest_core` package, whose modules
//! re-export these classes under their historical paths
//! (`hwtest_core.types.streaming.StreamSchema`, ...). Python enums
//! (`DataType`, `ValueQuality`, ...) and exceptions stay defined in Python and
//! are bridged by value.

// PyO3 extracts arguments by value, and attribute names such as `from_state`
// mirror the Python API, so these two pedantic lints are noise here.
#![allow(clippy::needless_pass_by_value, clippy::wrong_self_convention)]

mod bounds;
mod common;
mod conv;
mod records;
mod sdk;
mod spec;
mod streaming;

use pyo3::prelude::*;

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;

    m.add_class::<common::PyTimestamp>()?;
    m.add_class::<common::PyInstrumentIdentity>()?;

    m.add("MSG_TYPE_SCHEMA", hwtest_core::streaming::MSG_TYPE_SCHEMA)?;
    m.add("MSG_TYPE_DATA", hwtest_core::streaming::MSG_TYPE_DATA)?;
    m.add_class::<streaming::PyStreamField>()?;
    m.add_class::<streaming::PyStreamSchema>()?;
    m.add_class::<streaming::PyStreamData>()?;
    m.add_function(wrap_pyfunction!(streaming::encode_string, m)?)?;
    m.add_function(wrap_pyfunction!(streaming::decode_string, m)?)?;

    m.add_class::<records::PyTelemetryValue>()?;
    m.add_class::<records::PyTelemetryMessage>()?;
    m.add_class::<records::PyEnvironmentalState>()?;
    m.add_class::<records::PyStateTransition>()?;
    m.add_class::<records::PyThresholdBound>()?;
    m.add_class::<records::PyThreshold>()?;
    m.add_class::<records::PyStateThresholds>()?;
    m.add_class::<records::PyThresholdViolation>()?;
    m.add_class::<records::PyMonitorResult>()?;

    m.add_class::<bounds::PyWithinTolerance>()?;
    m.add_class::<bounds::PyWithinRange>()?;
    m.add_class::<bounds::PyWithinBaseline>()?;
    m.add_class::<bounds::PyLessThan>()?;
    m.add_class::<bounds::PyGreaterThan>()?;
    m.add_class::<bounds::PyGoodInterval>()?;
    m.add_class::<bounds::PyBadInterval>()?;
    m.add_class::<bounds::PyGoodValues>()?;
    m.add_class::<bounds::PyBadValues>()?;
    m.add_class::<bounds::PySpecial>()?;
    m.add_function(wrap_pyfunction!(bounds::bound_check_from_dict, m)?)?;

    m.add_class::<spec::PyTestCaseInfo>()?;
    m.add_class::<spec::PyMonitorState>()?;
    m.add_class::<spec::PyMonitorDef>()?;
    m.add_class::<spec::PyLoggerDef>()?;
    m.add_class::<spec::PyTestDefinition>()?;
    m.add_class::<spec::PyMonitor>()?;
    m.add_function(wrap_pyfunction!(spec::find_definition_file, m)?)?;
    m.add_function(wrap_pyfunction!(spec::load_definition, m)?)?;
    m.add_function(wrap_pyfunction!(spec::check_definitions, m)?)?;
    m.add_function(wrap_pyfunction!(spec::check_spec_files, m)?)?;

    m.add_class::<sdk::PyRack>()?;
    m.add_class::<sdk::PySession>()?;
    m.add_class::<sdk::PyCanBus>()?;
    m.add_class::<sdk::PyUutClient>()?;
    m.add_class::<sdk::PyUutSimulator>()?;
    m.add("HardwareError", m.py().get_type::<sdk::HardwareError>())?;
    m.add_function(wrap_pyfunction!(sdk::parse_event_line, m)?)?;
    m.add_function(wrap_pyfunction!(sdk::_roundtrip, m)?)?;
    Ok(())
}
