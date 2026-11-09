//! Error type shared by all hwtest-core modules.

/// Errors produced by hwtest-core.
///
/// The variants map onto the Python exception hierarchy in
/// `hwtest_core.errors` (see the binding crate).
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum Error {
    /// Invalid threshold or bound check definition (Python: `ThresholdError`).
    #[error("{0}")]
    Threshold(String),
    /// Malformed or inconsistent binary streaming frame (Python: `ValueError`).
    #[error("{0}")]
    Protocol(String),
    /// JSON (de)serialization failure (Python: `SerializationError`).
    #[error("{0}")]
    Serialization(String),
}

/// Convenience alias for `Result<T, hwtest_core::Error>`.
pub type Result<T> = std::result::Result<T, Error>;

impl From<serde_json::Error> for Error {
    fn from(err: serde_json::Error) -> Self {
        Error::Serialization(err.to_string())
    }
}
