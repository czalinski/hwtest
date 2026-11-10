//! CSV file logger.

use std::fs::File;
use std::io::BufWriter;
use std::path::{Path, PathBuf};

use hwtest_core::streaming::SampleValue;
use hwtest_core::{StreamData, StreamSchema};
use indexmap::IndexMap;
use serde::Deserialize;
use serde_json::json;

use crate::error::{io, LoggerError, Result};
use crate::{check_schema, StreamLogger, Tags};

/// CSV logger settings (`kwargs` of a `kind: csv` logger).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CsvLoggerConfig {
    /// Root directory.
    pub output_dir: PathBuf,
    /// Use `{test_type}/{test_case_id}/{test_run_id}` sub-directories.
    #[serde(default = "yes")]
    pub organize_by_tags: bool,
    /// Rows between flushes.
    #[serde(default = "hundred")]
    pub buffer_size: usize,
}

fn yes() -> bool {
    true
}
fn hundred() -> usize {
    100
}

struct TopicWriter {
    writer: csv::Writer<BufWriter<File>>,
    schema: StreamSchema,
    pending: usize,
}

/// Writes one CSV file per topic plus `metadata.json`.
pub struct CsvStreamLogger {
    config: CsvLoggerConfig,
    tags: Tags,
    schemas: IndexMap<String, StreamSchema>,
    writers: IndexMap<String, TopicWriter>,
    dir: Option<PathBuf>,
}

impl CsvStreamLogger {
    /// Creates a logger (no files are created until `start`).
    pub fn new(config: CsvLoggerConfig) -> Self {
        Self {
            config,
            tags: Tags::new(),
            schemas: IndexMap::new(),
            writers: IndexMap::new(),
            dir: None,
        }
    }

    /// The directory of the current (or last) session.
    pub fn log_directory(&self) -> Option<&Path> {
        self.dir.as_deref()
    }

    fn write_metadata(&self) -> Result<()> {
        let Some(dir) = &self.dir else { return Ok(()) };
        let mut meta = serde_json::Map::new();
        for (k, v) in &self.tags {
            meta.insert(k.clone(), json!(v));
        }
        meta.insert("topics".into(), json!(self.writers.keys().collect::<Vec<_>>()));
        let schemas: serde_json::Map<String, serde_json::Value> = self
            .writers
            .iter()
            .map(|(topic, w)| {
                let fields: Vec<_> = w
                    .schema
                    .fields()
                    .iter()
                    .map(|f| json!({"name": f.name, "dtype": format!("{:?}", f.dtype), "unit": f.unit}))
                    .collect();
                (
                    topic.clone(),
                    json!({
                        "source_id": w.schema.source_id(),
                        "schema_id": format!("{:#x}", w.schema.schema_id()),
                        "fields": fields,
                    }),
                )
            })
            .collect();
        meta.insert("schemas".into(), serde_json::Value::Object(schemas));
        let path = dir.join("metadata.json");
        let text = serde_json::to_string_pretty(&meta).expect("metadata serializes");
        std::fs::write(&path, text).map_err(io(&path))
    }

    fn file_name(topic: &str) -> String {
        format!("{}.csv", topic.replace(['/', '.'], "_"))
    }
}

pub(crate) fn sample_text(v: SampleValue) -> String {
    match v {
        SampleValue::Int(i) => i.to_string(),
        SampleValue::UInt(u) => u.to_string(),
        SampleValue::Float(f) => f.to_string(),
    }
}

impl StreamLogger for CsvStreamLogger {
    fn start(&mut self, tags: &Tags) -> Result<()> {
        if self.dir.is_some() && !self.writers.is_empty() {
            return Ok(());
        }
        self.tags.clone_from(tags);
        let mut dir = self.config.output_dir.clone();
        if self.config.organize_by_tags {
            for key in ["test_type", "test_case_id", "test_run_id"] {
                dir.push(tags.get(key).map_or("unknown", String::as_str));
            }
        }
        std::fs::create_dir_all(&dir).map_err(io(&dir))?;
        self.dir = Some(dir);
        Ok(())
    }

    fn register_schema(&mut self, topic: &str, schema: StreamSchema) {
        self.schemas.insert(topic.to_string(), schema);
    }

    fn log(&mut self, topic: &str, data: &StreamData) -> Result<()> {
        let dir = self.dir.clone().ok_or(LoggerError::NotStarted)?;
        let schema = check_schema(&self.schemas, topic, data)?.clone();
        if !self.writers.contains_key(topic) {
            let path = dir.join(Self::file_name(topic));
            let file = File::create(&path).map_err(io(&path))?;
            let mut writer = csv::Writer::from_writer(BufWriter::new(file));
            let mut header = vec!["timestamp_ns".to_string()];
            header.extend(schema.fields().iter().map(|f| f.name.clone()));
            writer.write_record(&header).map_err(|e| LoggerError::Io {
                path: path.display().to_string(),
                message: e.to_string(),
            })?;
            self.writers.insert(topic.to_string(), TopicWriter { writer, schema, pending: 0 });
            // Written now (and again at stop) so crashed runs remain importable.
            self.write_metadata()?;
        }
        let w = self.writers.get_mut(topic).expect("inserted above");
        let err =
            |e: csv::Error| LoggerError::Io { path: topic.to_string(), message: e.to_string() };
        for (i, sample) in data.samples.iter().enumerate() {
            let mut row = Vec::with_capacity(sample.len() + 1);
            row.push(data.timestamp(i as u64).to_string());
            row.extend(sample.iter().map(|v| sample_text(*v)));
            w.writer.write_record(&row).map_err(err)?;
        }
        w.pending += data.samples.len();
        if w.pending >= self.config.buffer_size {
            w.writer
                .flush()
                .map_err(|e| LoggerError::Io { path: topic.to_string(), message: e.to_string() })?;
            w.pending = 0;
        }
        Ok(())
    }

    fn stop(&mut self) -> Result<()> {
        for (topic, w) in &mut self.writers {
            w.writer
                .flush()
                .map_err(|e| LoggerError::Io { path: topic.clone(), message: e.to_string() })?;
        }
        self.write_metadata()?;
        self.writers.clear();
        self.dir = None;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use hwtest_core::{DataType, StreamField};

    #[test]
    fn writes_csv_and_metadata() {
        let root = tempfile::tempdir().unwrap();
        let mut logger = CsvStreamLogger::new(CsvLoggerConfig {
            output_dir: root.path().to_path_buf(),
            organize_by_tags: true,
            buffer_size: 1,
        });
        let schema = StreamSchema::new(
            "daq",
            vec![
                StreamField::new("v", DataType::F64, "V"),
                StreamField::new("n", DataType::I32, ""),
            ],
        );
        let tags = Tags::from([
            ("test_type".to_string(), "hass".to_string()),
            ("test_case_id".to_string(), "echo".to_string()),
            ("test_run_id".to_string(), "r1".to_string()),
        ]);
        assert!(matches!(
            logger.log(
                "x",
                &StreamData { schema_id: 0, timestamp_ns: 0, period_ns: 0, samples: vec![] }
            ),
            Err(LoggerError::NotStarted)
        ));
        logger.start(&tags).unwrap();
        logger.register_schema("voltage.echo", schema.clone());
        let data = StreamData {
            schema_id: schema.schema_id(),
            timestamp_ns: 100,
            period_ns: 10,
            samples: vec![vec![3.3.into(), 1i64.into()], vec![(-0.5).into(), 2i64.into()]],
        };
        logger.log("voltage.echo", &data).unwrap();
        let dir = root.path().join("hass/echo/r1");
        assert!(dir.join("metadata.json").exists(), "metadata written before stop");
        logger.stop().unwrap();
        let csv = std::fs::read_to_string(dir.join("voltage_echo.csv")).unwrap();
        assert_eq!(csv, "timestamp_ns,v,n\n100,3.3,1\n110,-0.5,2\n");
        let meta: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(dir.join("metadata.json")).unwrap())
                .unwrap();
        assert_eq!(meta["test_run_id"], "r1");
        assert_eq!(meta["topics"], json!(["voltage.echo"]));
        assert_eq!(
            meta["schemas"]["voltage.echo"]["fields"][0],
            json!({"name": "v", "dtype": "F64", "unit": "V"})
        );
        assert_eq!(
            meta["schemas"]["voltage.echo"]["schema_id"],
            format!("{:#x}", schema.schema_id())
        );
    }

    #[test]
    fn rejects_unknown_topic_and_mismatch() {
        let root = tempfile::tempdir().unwrap();
        let mut logger = CsvStreamLogger::new(CsvLoggerConfig {
            output_dir: root.path().into(),
            organize_by_tags: false,
            buffer_size: 10,
        });
        logger.start(&Tags::new()).unwrap();
        let data = StreamData { schema_id: 7, timestamp_ns: 0, period_ns: 0, samples: vec![] };
        assert!(matches!(logger.log("t", &data), Err(LoggerError::UnknownTopic(_))));
        logger.register_schema(
            "t",
            StreamSchema::new("s", vec![StreamField::new("a", DataType::U8, "")]),
        );
        assert!(matches!(logger.log("t", &data), Err(LoggerError::SchemaMismatch { .. })));
    }
}
