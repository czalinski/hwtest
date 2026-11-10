//! Telemetry loggers: CSV files and `InfluxDB`.
//!
//! Both implement [`StreamLogger`]. [`create_logger`] builds one from a test
//! definition's `loggers:` entry (`kind: csv` / `kind: influxdb`).
//!
//! CSV layout (importable into `InfluxDB` later with `hwtest-import-csv`):
//! ```text
//! {output_dir}/{test_type}/{test_case_id}/{test_run_id}/{topic}.csv   timestamp_ns,<fields...>
//! {output_dir}/{test_type}/{test_case_id}/{test_run_id}/metadata.json tags + schemas
//! ```

pub mod csv_logger;
pub mod error;
pub mod influx;
pub mod line_protocol;

use hwtest_core::{StreamData, StreamSchema};
use hwtest_spec::testdef::{LoggerDef, LoggerKind};
use indexmap::IndexMap;

pub use csv_logger::{CsvLoggerConfig, CsvStreamLogger};
pub use error::{LoggerError, Result};
pub use influx::{InfluxConfig, InfluxDbStreamLogger, InfluxWriter};

/// Run tags (`test_type`, `test_case_id`, `test_run_id`, ...).
pub type Tags = IndexMap<String, String>;

/// A sink for stream batches, organised by topic.
pub trait StreamLogger: Send {
    /// Starts a logging session with run tags.
    ///
    /// # Errors
    /// [`LoggerError`] if the backend cannot be prepared.
    fn start(&mut self, tags: &Tags) -> Result<()>;

    /// Registers the schema of a topic before logging it.
    fn register_schema(&mut self, topic: &str, schema: StreamSchema);

    /// Logs one batch.
    ///
    /// # Errors
    /// [`LoggerError`] if not started, the topic is unknown, the schema id
    /// differs, or the backend fails.
    fn log(&mut self, topic: &str, data: &StreamData) -> Result<()>;

    /// Flushes and ends the session.
    ///
    /// # Errors
    /// [`LoggerError`] if flushing fails.
    fn stop(&mut self) -> Result<()>;
}

/// Creates a logger from a test definition entry (resolving `${VAR}` kwargs).
///
/// # Errors
/// [`LoggerError::Config`] for unknown/invalid kwargs, unset variables, or
/// Python-implemented loggers (which the Python SDK loads itself).
pub fn create_logger(def: &LoggerDef) -> Result<Box<dyn StreamLogger>> {
    let kwargs = def.resolved_kwargs().map_err(|e| LoggerError::Config(e.to_string()))?;
    let value = serde_json::Value::Object(kwargs);
    let bad =
        |e: serde_json::Error| LoggerError::Config(format!("loggers.{}.kwargs: {e}", def.name));
    match &def.kind {
        LoggerKind::Csv => {
            Ok(Box::new(CsvStreamLogger::new(serde_json::from_value(value).map_err(bad)?)))
        }
        LoggerKind::InfluxDb => {
            Ok(Box::new(InfluxDbStreamLogger::new(serde_json::from_value(value).map_err(bad)?)))
        }
        LoggerKind::Python { module, class } => Err(LoggerError::Config(format!(
            "logger {:?} is implemented in Python ({module}.{class})",
            def.name
        ))),
    }
}

pub(crate) fn check_schema<'a>(
    schemas: &'a IndexMap<String, StreamSchema>,
    topic: &str,
    data: &StreamData,
) -> Result<&'a StreamSchema> {
    let schema = schemas.get(topic).ok_or_else(|| LoggerError::UnknownTopic(topic.to_string()))?;
    if schema.schema_id() != data.schema_id {
        return Err(LoggerError::SchemaMismatch {
            topic: topic.to_string(),
            data: data.schema_id,
            expected: schema.schema_id(),
        });
    }
    Ok(schema)
}

#[cfg(test)]
mod tests {
    use super::*;
    use hwtest_spec::TestDefinition;

    #[test]
    fn factory_from_definition() {
        let d = TestDefinition::from_yaml_str(
            "test_case: {id: t}\nloggers:\n  csv: {kind: csv, kwargs: {output_dir: /tmp/x}}\n  bad: {kind: csv, kwargs: {output_dri: /tmp}}\n  py: {module: a, class: B}\n  influx: {kind: influxdb, kwargs: {url: 'http://h:8086', org: o, bucket: b}}\n",
            None,
        )
        .unwrap();
        assert!(create_logger(&d.loggers["csv"]).is_ok());
        assert!(create_logger(&d.loggers["influx"]).is_ok());
        let e = create_logger(&d.loggers["bad"]).err().unwrap().to_string();
        assert!(e.contains("loggers.bad.kwargs: unknown field `output_dri`"), "{e}");
        assert!(create_logger(&d.loggers["py"])
            .err()
            .unwrap()
            .to_string()
            .contains("implemented in Python"));
    }
}
