//! Errors for NATS operations.

/// NATS transport error.
#[derive(Debug, thiserror::Error)]
pub enum NatsError {
    /// Could not connect.
    #[error("failed to connect to NATS {servers}: {message}")]
    Connect {
        /// Server list.
        servers: String,
        /// Cause.
        message: String,
    },
    /// A `JetStream` or core NATS operation failed.
    #[error("NATS {operation} failed: {message}")]
    Operation {
        /// What was attempted.
        operation: &'static str,
        /// Cause.
        message: String,
    },
    /// Timed out waiting for something.
    #[error("timed out waiting for {0}")]
    Timeout(String),
    /// Invalid payload or schema mismatch.
    #[error(transparent)]
    Protocol(#[from] hwtest_core::Error),
    /// Invalid configuration.
    #[error("invalid NATS configuration: {0}")]
    Config(String),
}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, NatsError>;

pub(crate) fn op(operation: &'static str) -> impl FnOnce(&dyn std::fmt::Display) -> NatsError {
    move |e| NatsError::Operation { operation, message: e.to_string() }
}

/// Maps any displayable error into [`NatsError::Operation`].
pub(crate) trait OpExt<T> {
    fn op(self, operation: &'static str) -> Result<T>;
}

impl<T, E: std::fmt::Display> OpExt<T> for std::result::Result<T, E> {
    fn op(self, operation: &'static str) -> Result<T> {
        self.map_err(|e| op(operation)(&e))
    }
}
