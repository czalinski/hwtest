//! Core data types and wire protocols for hwtest.
//!
//! This crate is the single source of truth for the hwtest data model. It has
//! no Python dependency; the `hwtest_core` Python package is a thin `PyO3`
//! binding over it, so Python test cases and future Rust services share one
//! implementation and one wire format.
//!
//! Modules:
//! - [`common`]: [`Timestamp`], [`DataType`], [`InstrumentIdentity`], ID aliases
//! - [`streaming`]: the big-endian binary streaming protocol (schema + data frames)
//! - [`telemetry`]: JSON telemetry values and batches
//! - [`state`]: environmental states and transitions
//! - [`threshold`]: low/high threshold definitions per state
//! - [`bounds`]: YAML/JSON-configurable bound checks (`{"within_range": [3.3, 0.1]}`)
//! - [`monitor`]: monitor verdicts and results
//!
//! JSON representations are byte-compatible with the original Python
//! implementation; `tests/python_compat.rs` checks this against golden
//! fixtures generated from it.

pub mod bounds;
pub mod common;
pub mod error;
pub mod monitor;
pub mod state;
pub mod streaming;
pub mod telemetry;
pub mod threshold;

pub use bounds::{
    BadInterval, BadValues, BoundCheck, GoodInterval, GoodValues, GreaterThan, LessThan, Special,
    WithinBaseline, WithinRange, WithinTolerance,
};
pub use common::{
    ChannelId, DataType, InstrumentIdentity, MonitorId, SourceId, StateId, Timestamp,
};
pub use error::{Error, Result};
pub use monitor::{MonitorResult, MonitorVerdict, ThresholdViolation};
pub use state::{EnvironmentalState, StateTransition};
pub use streaming::{SampleValue, StreamData, StreamField, StreamSchema};
pub use telemetry::{TelemetryMessage, TelemetryValue, ValueQuality};
pub use threshold::{BoundType, StateThresholds, Threshold, ThresholdBound};
