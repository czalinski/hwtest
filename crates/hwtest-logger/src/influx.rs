//! `InfluxDB` v2 logger (HTTP line protocol, background batching).

use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use hwtest_core::{StreamData, StreamSchema};
use indexmap::IndexMap;
use serde::Deserialize;

use crate::error::{LoggerError, Result};
use crate::line_protocol::{point, FieldValue};
use crate::{check_schema, StreamLogger, Tags};

/// `InfluxDB` settings (`kwargs` of a `kind: influxdb` logger).
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct InfluxConfig {
    /// Server URL, e.g. `http://localhost:8086`.
    pub url: String,
    /// Organisation.
    pub org: String,
    /// Bucket.
    pub bucket: String,
    /// API token (falls back to the `token_env` environment variable).
    #[serde(default)]
    pub token: Option<String>,
    /// Environment variable holding the token.
    #[serde(default = "default_token_env")]
    pub token_env: String,
    /// Measurement name.
    #[serde(default = "default_measurement")]
    pub measurement: String,
    /// Points per write request.
    #[serde(default = "default_batch")]
    pub batch_size: usize,
    /// Maximum time points wait before being written.
    #[serde(default = "default_flush")]
    pub flush_interval_ms: u64,
}

fn default_token_env() -> String {
    "INFLUXDB_TOKEN".into()
}
fn default_measurement() -> String {
    "telemetry".into()
}
fn default_batch() -> usize {
    1000
}
fn default_flush() -> u64 {
    1000
}

impl InfluxConfig {
    /// The token from the config or environment.
    ///
    /// # Errors
    /// [`LoggerError::Config`] if neither is set.
    pub fn resolve_token(&self) -> Result<String> {
        self.token
            .clone()
            .filter(|t| !t.is_empty())
            .or_else(|| std::env::var(&self.token_env).ok().filter(|t| !t.is_empty()))
            .ok_or_else(|| {
                LoggerError::Config(format!("no InfluxDB token: set token or ${}", self.token_env))
            })
    }
}

/// Blocking client for the write and health endpoints.
#[derive(Clone)]
pub struct InfluxWriter {
    agent: ureq::Agent,
    write_url: String,
    health_url: String,
    auth: String,
}

impl InfluxWriter {
    /// Creates a writer.
    pub fn new(url: &str, org: &str, bucket: &str, token: &str) -> Self {
        let base = url.trim_end_matches('/');
        let enc = |s: &str| {
            s.bytes()
                .map(|b| {
                    if b.is_ascii_alphanumeric() || b"-_.~".contains(&b) {
                        (b as char).to_string()
                    } else {
                        format!("%{b:02X}")
                    }
                })
                .collect::<String>()
        };
        let agent = ureq::Agent::config_builder()
            .timeout_global(Some(Duration::from_secs(10)))
            .http_status_as_error(false)
            .build()
            .into();
        Self {
            agent,
            write_url: format!(
                "{base}/api/v2/write?org={}&bucket={}&precision=ns",
                enc(org),
                enc(bucket)
            ),
            health_url: format!("{base}/health"),
            auth: format!("Token {token}"),
        }
    }

    /// Writes line-protocol lines.
    ///
    /// # Errors
    /// [`LoggerError::Influx`] if the request fails or is rejected.
    pub fn write(&self, lines: &[String]) -> Result<()> {
        if lines.is_empty() {
            return Ok(());
        }
        let body = lines.join("\n");
        let mut resp = self
            .agent
            .post(&self.write_url)
            .header("Authorization", &self.auth)
            .header("Content-Type", "text/plain; charset=utf-8")
            .send(body.as_bytes())
            .map_err(|e| LoggerError::Influx(e.to_string()))?;
        let status = resp.status();
        if status.is_success() {
            Ok(())
        } else {
            let text = resp.body_mut().read_to_string().unwrap_or_default();
            Err(LoggerError::Influx(format!("write rejected ({status}): {}", text.trim())))
        }
    }

    /// True if `/health` reports `pass`.
    pub fn healthy(&self) -> bool {
        self.agent
            .get(&self.health_url)
            .call()
            .ok()
            .filter(|r| r.status().is_success())
            .and_then(|mut r| r.body_mut().read_to_string().ok())
            .is_some_and(|b| b.contains("\"pass\""))
    }
}

enum Msg {
    Lines(Vec<String>),
    Stop,
}

/// Failures seen by the background writer.
#[derive(Debug, Default, Clone)]
pub struct WriteStats {
    /// Points written.
    pub written: u64,
    /// Points that could not be written.
    pub failed: u64,
    /// Last error message.
    pub last_error: Option<String>,
}

/// Streams batches to `InfluxDB`. Writes happen on a background thread, so an
/// `InfluxDB` outage does not stall the test; failures are counted and
/// reported by [`StreamLogger::stop`].
pub struct InfluxDbStreamLogger {
    config: InfluxConfig,
    tags: Tags,
    schemas: IndexMap<String, StreamSchema>,
    tx: Option<mpsc::Sender<Msg>>,
    worker: Option<JoinHandle<()>>,
    stats: Arc<Mutex<WriteStats>>,
}

impl InfluxDbStreamLogger {
    /// Creates a logger (connects on `start`).
    pub fn new(config: InfluxConfig) -> Self {
        Self {
            config,
            tags: Tags::new(),
            schemas: IndexMap::new(),
            tx: None,
            worker: None,
            stats: Arc::default(),
        }
    }

    /// Write statistics so far.
    pub fn stats(&self) -> WriteStats {
        self.stats.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone()
    }
}

fn run_worker(
    writer: &InfluxWriter,
    rx: &mpsc::Receiver<Msg>,
    batch: usize,
    interval: Duration,
    stats: &Mutex<WriteStats>,
) {
    let mut buf: Vec<String> = Vec::new();
    let mut deadline = Instant::now() + interval;
    let flush = |buf: &mut Vec<String>| {
        if buf.is_empty() {
            return;
        }
        let n = buf.len() as u64;
        let result = writer.write(buf);
        let mut s = stats.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        match result {
            Ok(()) => s.written += n,
            Err(e) => {
                tracing::warn!("{e}");
                s.failed += n;
                s.last_error = Some(e.to_string());
            }
        }
        buf.clear();
    };
    loop {
        let timeout = deadline.saturating_duration_since(Instant::now());
        match rx.recv_timeout(timeout) {
            Ok(Msg::Lines(lines)) => {
                buf.extend(lines);
                while buf.len() >= batch {
                    let rest = buf.split_off(batch);
                    flush(&mut buf);
                    buf = rest;
                }
            }
            Ok(Msg::Stop) | Err(RecvTimeoutError::Disconnected) => {
                flush(&mut buf);
                return;
            }
            Err(RecvTimeoutError::Timeout) => {
                flush(&mut buf);
                deadline = Instant::now() + interval;
            }
        }
    }
}

impl StreamLogger for InfluxDbStreamLogger {
    fn start(&mut self, tags: &Tags) -> Result<()> {
        if self.tx.is_some() {
            return Ok(());
        }
        let token = self.config.resolve_token()?;
        let writer =
            InfluxWriter::new(&self.config.url, &self.config.org, &self.config.bucket, &token);
        self.tags.clone_from(tags);
        let (tx, rx) = mpsc::channel();
        let batch = self.config.batch_size.max(1);
        let interval = Duration::from_millis(self.config.flush_interval_ms.max(1));
        let stats = self.stats.clone();
        self.worker =
            Some(std::thread::spawn(move || run_worker(&writer, &rx, batch, interval, &stats)));
        self.tx = Some(tx);
        Ok(())
    }

    fn register_schema(&mut self, topic: &str, schema: StreamSchema) {
        self.schemas.insert(topic.to_string(), schema);
    }

    fn log(&mut self, topic: &str, data: &StreamData) -> Result<()> {
        let tx = self.tx.as_ref().ok_or(LoggerError::NotStarted)?;
        let schema = check_schema(&self.schemas, topic, data)?;
        let mut lines = Vec::with_capacity(data.samples.len());
        for (i, sample) in data.samples.iter().enumerate() {
            let tags = std::iter::once(("topic", topic))
                .chain(self.tags.iter().map(|(k, v)| (k.as_str(), v.as_str())));
            let fields = schema
                .fields()
                .iter()
                .zip(sample)
                .map(|(f, v)| (f.name.as_str(), FieldValue::from(*v)));
            if let Some(line) =
                point(&self.config.measurement, tags, fields, i128::from(data.timestamp(i as u64)))
            {
                lines.push(line);
            }
        }
        tx.send(Msg::Lines(lines)).map_err(|_| LoggerError::Influx("writer thread stopped".into()))
    }

    fn stop(&mut self) -> Result<()> {
        if let Some(tx) = self.tx.take() {
            let _ = tx.send(Msg::Stop);
        }
        if let Some(w) = self.worker.take() {
            let _ = w.join();
        }
        let s = self.stats();
        match s.last_error {
            Some(e) if s.failed > 0 => Err(LoggerError::Influx(format!(
                "{} of {} points not written; last error: {e}",
                s.failed,
                s.failed + s.written
            ))),
            _ => Ok(()),
        }
    }
}

impl Drop for InfluxDbStreamLogger {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use hwtest_core::{DataType, StreamField};
    use std::io::{BufRead, BufReader, Read, Write};
    use std::net::TcpListener;

    type Requests = Arc<Mutex<Vec<(String, String)>>>;

    /// Minimal HTTP server recording request bodies (stands in for `InfluxDB`).
    fn fake_influx(status: u16) -> (String, Requests) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let seen = Arc::new(Mutex::new(Vec::new()));
        let seen2 = seen.clone();
        std::thread::spawn(move || {
            for stream in listener.incoming().flatten() {
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut request_line = String::new();
                reader.read_line(&mut request_line).unwrap();
                let mut len = 0;
                let mut auth = String::new();
                loop {
                    let mut h = String::new();
                    reader.read_line(&mut h).unwrap();
                    let lower = h.to_ascii_lowercase();
                    if let Some(v) = lower.strip_prefix("content-length:") {
                        len = v.trim().parse().unwrap();
                    }
                    if lower.starts_with("authorization:") {
                        auth = h.trim().to_ascii_lowercase();
                    }
                    if h == "\r\n" {
                        break;
                    }
                }
                let mut body = vec![0; len];
                reader.read_exact(&mut body).unwrap();
                seen2.lock().unwrap().push((
                    format!("{} | {auth}", request_line.trim()),
                    String::from_utf8(body).unwrap(),
                ));
                let mut s = stream;
                write!(
                    s,
                    "HTTP/1.1 {status} X\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}"
                )
                .unwrap();
            }
        });
        (url, seen)
    }

    fn logger(url: String, batch: usize) -> InfluxDbStreamLogger {
        InfluxDbStreamLogger::new(InfluxConfig {
            url,
            org: "hw test".into(),
            bucket: "telemetry".into(),
            token: Some("tok".into()),
            token_env: "UNUSED".into(),
            measurement: "voltage_echo".into(),
            batch_size: batch,
            flush_interval_ms: 50,
        })
    }

    #[test]
    fn writes_batches_with_tags() {
        let (url, seen) = fake_influx(204);
        let mut l = logger(url, 2);
        let schema = StreamSchema::new("daq", vec![StreamField::new("v", DataType::F64, "V")]);
        l.start(&Tags::from([("test_run_id".to_string(), "r1".to_string())])).unwrap();
        l.register_schema("echo", schema.clone());
        let data = StreamData {
            schema_id: schema.schema_id(),
            timestamp_ns: 5,
            period_ns: 1,
            samples: vec![vec![1.0.into()], vec![2.5.into()], vec![3.0.into()]],
        };
        l.log("echo", &data).unwrap();
        l.stop().unwrap();
        let seen = seen.lock().unwrap();
        assert_eq!(seen.len(), 2, "{seen:?}"); // batch of 2, then the rest on stop
        assert_eq!(seen[0].0, "POST /api/v2/write?org=hw%20test&bucket=telemetry&precision=ns HTTP/1.1 | authorization: token tok");
        assert_eq!(seen[0].1, "voltage_echo,test_run_id=r1,topic=echo v=1.0 5\nvoltage_echo,test_run_id=r1,topic=echo v=2.5 6");
        assert_eq!(seen[1].1, "voltage_echo,test_run_id=r1,topic=echo v=3.0 7");
        assert_eq!(l.stats().written, 3);
    }

    #[test]
    fn interval_flush_and_rejection_reported() {
        let (url, seen) = fake_influx(400);
        let mut l = logger(url, 1000);
        let schema = StreamSchema::new("daq", vec![StreamField::new("v", DataType::F64, "V")]);
        l.start(&Tags::new()).unwrap();
        l.register_schema("t", schema.clone());
        l.log(
            "t",
            &StreamData {
                schema_id: schema.schema_id(),
                timestamp_ns: 0,
                period_ns: 0,
                samples: vec![vec![1.0.into()]],
            },
        )
        .unwrap();
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(seen.lock().unwrap().len(), 1, "flushed by interval, not by stop");
        let err = l.stop().unwrap_err().to_string();
        assert!(err.contains("1 of 1 points not written") && err.contains("400"), "{err}");
    }

    #[test]
    fn missing_token() {
        let mut l = InfluxDbStreamLogger::new(InfluxConfig {
            token: None,
            token_env: "HWTEST_NO_SUCH_TOKEN".into(),
            ..logger("http://x".into(), 1).config.clone()
        });
        assert!(l.start(&Tags::new()).unwrap_err().to_string().contains("$HWTEST_NO_SUCH_TOKEN"));
    }
}
