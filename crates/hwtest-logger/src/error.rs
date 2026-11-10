//! Logger errors.

/// Logger error.
#[derive(Debug, thiserror::Error)]
pub enum LoggerError {
    /// Invalid configuration.
    #[error("invalid logger configuration: {0}")]
    Config(String),
    /// `log` before `start` (or after `stop`).
    #[error("logger is not started")]
    NotStarted,
    /// No schema registered for the topic.
    #[error("no schema registered for topic {0:?}")]
    UnknownTopic(String),
    /// Batch does not match the registered schema.
    #[error("schema id mismatch for topic {topic:?}: data has {data:#x}, expected {expected:#x}")]
    SchemaMismatch {
        /// Topic.
        topic: String,
        /// Batch schema id.
        data: u32,
        /// Registered schema id.
        expected: u32,
    },
    /// File system error.
    #[error("{path}: {message}")]
    Io {
        /// Path involved.
        path: String,
        /// OS error.
        message: String,
    },
    /// `InfluxDB` rejected a request or was unreachable.
    #[error("InfluxDB: {0}")]
    Influx(String),
}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, LoggerError>;

pub(crate) fn io(path: &std::path::Path) -> impl FnOnce(std::io::Error) -> LoggerError + '_ {
    move |e| LoggerError::Io { path: path.display().to_string(), message: e.to_string() }
}
