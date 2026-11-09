//! Monitor verdicts and results.

use serde::{Deserialize, Serialize};

use crate::common::{ChannelId, MonitorId, StateId, Timestamp};
use crate::error::Result;
use crate::state::{default_source, nullable_f64};
use crate::threshold::Threshold;

/// Outcome of one monitor evaluation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MonitorVerdict {
    /// All checked values within limits.
    Pass,
    /// At least one violation.
    Fail,
    /// Not evaluated (e.g. during a transition).
    Skip,
    /// Evaluation itself failed.
    Error,
}

/// One value outside its threshold.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ThresholdViolation {
    /// Offending channel.
    pub channel: ChannelId,
    /// Offending value (`null` in JSON if non-finite).
    #[serde(deserialize_with = "nullable_f64")]
    pub value: f64,
    /// Threshold that was violated.
    pub threshold: Threshold,
    /// Human-readable description.
    #[serde(default)]
    pub message: String,
}

/// Result of evaluating a monitor against a batch of telemetry.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(into = "MonitorResultWire", from = "MonitorResultWire")]
pub struct MonitorResult {
    /// Monitor that produced the result.
    pub monitor_id: MonitorId,
    /// Verdict.
    pub verdict: MonitorVerdict,
    /// Evaluation time.
    pub timestamp: Timestamp,
    /// State the evaluation was made in.
    pub state_id: StateId,
    /// Violations (empty unless the verdict is `Fail`).
    pub violations: Vec<ThresholdViolation>,
    /// Summary message.
    pub message: String,
}

impl MonitorResult {
    /// True if the verdict is `Pass`.
    pub fn passed(&self) -> bool {
        self.verdict == MonitorVerdict::Pass
    }

    /// True if the verdict is `Fail`.
    pub fn failed(&self) -> bool {
        self.verdict == MonitorVerdict::Fail
    }

    /// Serializes to JSON bytes.
    ///
    /// # Errors
    /// [`crate::Error::Serialization`] on failure.
    pub fn to_json_bytes(&self) -> Result<Vec<u8>> {
        Ok(serde_json::to_vec(self)?)
    }

    /// Parses JSON bytes.
    ///
    /// # Errors
    /// [`crate::Error::Serialization`] if the JSON is malformed.
    pub fn from_json_bytes(data: &[u8]) -> Result<Self> {
        Ok(serde_json::from_slice(data)?)
    }
}

#[derive(Serialize, Deserialize)]
struct MonitorResultWire {
    monitor_id: String,
    verdict: MonitorVerdict,
    timestamp: i64,
    #[serde(default = "default_source")]
    timestamp_source: String,
    state_id: String,
    #[serde(default)]
    violations: Vec<ThresholdViolation>,
    #[serde(default)]
    message: String,
}

impl From<MonitorResult> for MonitorResultWire {
    fn from(r: MonitorResult) -> Self {
        Self {
            monitor_id: r.monitor_id,
            verdict: r.verdict,
            timestamp: r.timestamp.unix_ns,
            timestamp_source: r.timestamp.source,
            state_id: r.state_id,
            violations: r.violations,
            message: r.message,
        }
    }
}

impl From<MonitorResultWire> for MonitorResult {
    fn from(w: MonitorResultWire) -> Self {
        Self {
            monitor_id: w.monitor_id,
            verdict: w.verdict,
            timestamp: Timestamp::new(w.timestamp, w.timestamp_source),
            state_id: w.state_id,
            violations: w.violations,
            message: w.message,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::threshold::ThresholdBound;

    #[test]
    fn result_round_trip_and_flags() {
        let r = MonitorResult {
            monitor_id: "m".into(),
            verdict: MonitorVerdict::Fail,
            timestamp: Timestamp::new(5, "ntp"),
            state_id: "hot".into(),
            violations: vec![ThresholdViolation {
                channel: "i".into(),
                value: 2.5,
                threshold: Threshold::new("i", None, Some(ThresholdBound::inclusive(2.0))),
                message: "over".into(),
            }],
            message: "Failed".into(),
        };
        assert!(r.failed() && !r.passed());
        assert_eq!(MonitorResult::from_json_bytes(&r.to_json_bytes().unwrap()).unwrap(), r);
    }
}
