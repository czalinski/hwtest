//! Connection and stream configuration.

use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::error::{NatsError, Result};

/// Where a new subscriber starts reading.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliverPolicy {
    /// Everything still retained by the stream.
    All,
    /// The last message, then new ones.
    Last,
    /// Only messages published after subscribing (default; schemas are
    /// re-published every second so a live subscriber needs nothing older).
    #[default]
    New,
}

/// NATS settings. Field names match the original Python `NatsConfig`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct NatsConfig {
    /// Server URLs.
    pub servers: Vec<String>,
    /// `JetStream` stream name.
    pub stream_name: String,
    /// Subject prefix.
    pub subject_prefix: String,
    /// Connect timeout in seconds.
    pub connect_timeout: f64,
    /// Delay between reconnect attempts in seconds.
    pub reconnect_time_wait: f64,
    /// Maximum reconnect attempts (-1 = unlimited).
    pub max_reconnect_attempts: i64,
    /// Seconds between schema re-broadcasts.
    pub schema_publish_interval: f64,
    /// Stream retention in seconds.
    pub max_age: f64,
    /// User name (with `password`).
    pub user: Option<String>,
    /// Password.
    pub password: Option<String>,
    /// Token authentication.
    pub token: Option<String>,
    /// Where new data subscribers start.
    pub consumer_deliver_policy: DeliverPolicy,
}

impl Default for NatsConfig {
    fn default() -> Self {
        Self {
            servers: vec!["nats://localhost:4222".into()],
            stream_name: "TELEMETRY".into(),
            subject_prefix: "telemetry".into(),
            connect_timeout: 5.0,
            reconnect_time_wait: 1.0,
            max_reconnect_attempts: -1,
            schema_publish_interval: 1.0,
            max_age: 86_400.0,
            user: None,
            password: None,
            token: None,
            consumer_deliver_policy: DeliverPolicy::New,
        }
    }
}

impl NatsConfig {
    /// Default configuration for a single server URL.
    pub fn from_url(url: impl Into<String>) -> Self {
        Self { servers: vec![url.into()], ..Self::default() }
    }

    /// Checks the configuration.
    ///
    /// # Errors
    /// [`NatsError::Config`] describing the first problem.
    pub fn validate(&self) -> Result<()> {
        let bad = |m: &str| Err(NatsError::Config(m.into()));
        if self.servers.is_empty() {
            return bad("at least one server URL is required");
        }
        if self.connect_timeout <= 0.0 {
            return bad("connect_timeout must be positive");
        }
        if self.schema_publish_interval <= 0.0 {
            return bad("schema_publish_interval must be positive");
        }
        if self.max_age <= 0.0 {
            return bad("max_age must be positive");
        }
        if self.subject_prefix.is_empty() || self.subject_prefix.contains([' ', '*', '>']) {
            return bad("subject_prefix must be a plain subject token");
        }
        Ok(())
    }

    /// `{prefix}.{source}.data`
    pub fn data_subject(&self, source_id: &str) -> String {
        format!("{}.{source_id}.data", self.subject_prefix)
    }

    /// `{prefix}.{source}.schema`
    pub fn schema_subject(&self, source_id: &str) -> String {
        format!("{}.{source_id}.schema", self.subject_prefix)
    }

    /// `{prefix}.{source}.>` (schema and data).
    pub fn source_filter(&self, source_id: &str) -> String {
        format!("{}.{source_id}.>", self.subject_prefix)
    }

    /// `{prefix}.state`
    pub fn state_subject(&self) -> String {
        format!("{}.state", self.subject_prefix)
    }

    /// `{prefix}.monitor.results`
    pub fn monitor_results_subject(&self) -> String {
        format!("{}.monitor.results", self.subject_prefix)
    }

    pub(crate) fn secs(v: f64) -> Duration {
        Duration::from_secs_f64(v.max(0.0))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn subjects_match_python() {
        let c = NatsConfig::default();
        assert_eq!(c.data_subject("daq"), "telemetry.daq.data");
        assert_eq!(c.schema_subject("daq"), "telemetry.daq.schema");
        assert_eq!(c.source_filter("daq"), "telemetry.daq.>");
        assert_eq!(c.state_subject(), "telemetry.state");
        assert_eq!(c.monitor_results_subject(), "telemetry.monitor.results");
    }

    #[test]
    fn validation_and_yaml_style_config() {
        assert!(NatsConfig::default().validate().is_ok());
        assert!(NatsConfig { servers: vec![], ..NatsConfig::default() }.validate().is_err());
        assert!(NatsConfig { subject_prefix: "a.>".into(), ..NatsConfig::default() }
            .validate()
            .is_err());
        let c: NatsConfig = serde_json::from_str(
            r#"{"servers": ["nats://rack:4222"], "consumer_deliver_policy": "all"}"#,
        )
        .unwrap();
        assert_eq!(c.consumer_deliver_policy, DeliverPolicy::All);
        assert_eq!(c.stream_name, "TELEMETRY");
        assert!(serde_json::from_str::<NatsConfig>(r#"{"sever": []}"#).is_err());
    }
}
