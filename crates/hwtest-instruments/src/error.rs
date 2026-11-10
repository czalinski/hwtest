//! Instrument errors.

/// Instrument error. Messages name the instrument/channel involved.
#[derive(Debug, thiserror::Error)]
pub enum InstrumentError {
    /// Invalid driver configuration (`kwargs`).
    #[error("invalid configuration: {0}")]
    Config(String),
    /// Unknown channel name or id.
    #[error("unknown channel {0}")]
    UnknownChannel(String),
    /// A value outside the instrument's range.
    #[error("{0}")]
    OutOfRange(String),
    /// The operation is not valid in the current state (e.g. read while scanning).
    #[error("{0}")]
    State(String),
    /// The instrument does not support this capability.
    #[error("{instrument} does not support {capability}")]
    Unsupported {
        /// Instrument model.
        instrument: String,
        /// Capability name.
        capability: &'static str,
    },
    /// Communication failure (I/O, timeout, bus error, library error).
    #[error("{0}")]
    Comms(String),
    /// The instrument reported errors (e.g. SCPI error queue).
    #[error("instrument error(s): {0}")]
    Instrument(String),
    /// A required system library is missing.
    #[error("{0}")]
    Library(String),
}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, InstrumentError>;

pub(crate) fn comms(
    context: impl std::fmt::Display,
) -> impl FnOnce(&dyn std::fmt::Display) -> InstrumentError {
    move |e| InstrumentError::Comms(format!("{context}: {e}"))
}

/// Maps any displayable error into [`InstrumentError::Comms`] with context.
pub(crate) trait CommsExt<T> {
    fn comms(self, context: impl std::fmt::Display) -> Result<T>;
}

impl<T, E: std::fmt::Display> CommsExt<T> for std::result::Result<T, E> {
    fn comms(self, context: impl std::fmt::Display) -> Result<T> {
        self.map_err(|e| comms(context)(&e))
    }
}
