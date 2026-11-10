//! Monitor verdicts and results.

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};

use crate::bounds::BoundCheck;
use crate::common::{ChannelId, MonitorId, StateId, Timestamp};
use crate::error::Result;
use crate::state::{default_source, nullable_f64, EnvironmentalState};
use crate::streaming::{StreamData, StreamSchema};
use crate::threshold::{Threshold, ThresholdBound};

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

/// Bound checks per environmental state and field.
///
/// The reserved state [`StateBounds::DEFAULT_STATE`] (`"default"`) supplies
/// the bound for any state that does not list the field itself.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct StateBounds {
    states: IndexMap<StateId, IndexMap<String, BoundCheck>>,
}

impl StateBounds {
    /// Name of the fallback state.
    pub const DEFAULT_STATE: &'static str = "default";

    /// Wraps a `state -> field -> bound` map.
    pub fn new(states: IndexMap<StateId, IndexMap<String, BoundCheck>>) -> Self {
        Self { states }
    }

    /// The underlying map, in definition order.
    pub fn states(&self) -> &IndexMap<StateId, IndexMap<String, BoundCheck>> {
        &self.states
    }

    /// True if no bounds are defined.
    pub fn is_empty(&self) -> bool {
        self.states.values().all(IndexMap::is_empty)
    }

    /// The bound for `field` in `state`, falling back to the default state.
    pub fn get(&self, state: &str, field: &str) -> Option<&BoundCheck> {
        self.states
            .get(state)
            .and_then(|b| b.get(field))
            .or_else(|| self.states.get(Self::DEFAULT_STATE).and_then(|b| b.get(field)))
    }

    fn get_mut(&mut self, state: &str, field: &str) -> Option<&mut BoundCheck> {
        let in_state = self.states.get(state).is_some_and(|b| b.contains_key(field));
        let key = if in_state { state } else { Self::DEFAULT_STATE };
        self.states.get_mut(key).and_then(|b| b.get_mut(field))
    }

    /// Every field that has a bound in any state, in first-seen order.
    pub fn fields(&self) -> Vec<&str> {
        let mut out: Vec<&str> = Vec::new();
        for field in self.states.values().flat_map(IndexMap::keys) {
            if !out.contains(&field.as_str()) {
                out.push(field);
            }
        }
        out
    }
}

/// Converts a bound's acceptance interval into a [`Threshold`] for reporting.
/// Infinite sides are left open (JSON cannot represent infinity).
pub fn bound_to_threshold(channel: &str, bound: &BoundCheck) -> Threshold {
    let side = |v: f64, exclusive: bool| {
        v.is_finite().then(|| {
            if exclusive {
                ThresholdBound::exclusive(v)
            } else {
                ThresholdBound::inclusive(v)
            }
        })
    };
    let (low, high) = match bound {
        BoundCheck::LessThan(b) => (None, side(b.limit(), true)),
        BoundCheck::GreaterThan(b) => (side(b.limit(), true), None),
        other => {
            other.interval().map_or((None, None), |(lo, hi)| (side(lo, false), side(hi, false)))
        }
    };
    Threshold::new(channel, low, high)
}

/// Evaluates measurements against state-dependent [`StateBounds`].
///
/// Stateful: `within_baseline` checks lock their baseline on first pass.
#[derive(Debug, Clone, PartialEq)]
pub struct BoundsMonitor {
    /// Monitor identifier used in results.
    pub monitor_id: MonitorId,
    bounds: StateBounds,
}

impl BoundsMonitor {
    /// Creates a monitor.
    pub fn new(monitor_id: impl Into<MonitorId>, bounds: StateBounds) -> Self {
        Self { monitor_id: monitor_id.into(), bounds }
    }

    /// The configured bounds (including any locked baselines).
    pub fn bounds(&self) -> &StateBounds {
        &self.bounds
    }

    /// Evaluates the values available through `value_of` in `state`.
    ///
    /// - Transition states are skipped (`Skip`).
    /// - Fields without a value, without a bound, or bound to `special: any`
    ///   are not checked.
    /// - Any failing check gives `Fail` with one violation per field;
    ///   nothing checked gives `Skip`; otherwise `Pass`.
    pub fn evaluate(
        &mut self,
        value_of: impl Fn(&str) -> Option<f64>,
        state: &EnvironmentalState,
        timestamp: &Timestamp,
    ) -> MonitorResult {
        let result = |verdict, violations, message| MonitorResult {
            monitor_id: self.monitor_id.clone(),
            verdict,
            timestamp: timestamp.clone(),
            state_id: state.state_id.clone(),
            violations,
            message,
        };
        if state.is_transition {
            return result(
                MonitorVerdict::Skip,
                Vec::new(),
                "Skipping evaluation during state transition".to_string(),
            );
        }

        let fields: Vec<String> = self.bounds.fields().into_iter().map(str::to_owned).collect();
        let mut violations = Vec::new();
        let (mut checked, mut skipped) = (0usize, 0usize);
        for field in &fields {
            let Some(measured) = value_of(field) else { continue };
            let Some(bound) = self.bounds.get_mut(&state.state_id, field) else {
                skipped += 1;
                continue;
            };
            if bound.is_any() {
                skipped += 1;
                continue;
            }
            checked += 1;
            if !bound.check(measured) {
                let message = match bound.interval() {
                    Some((lo, hi)) => format!("{field}={measured:.4} outside [{lo:.4}, {hi:.4}]"),
                    None => format!("{field}={measured:.4} failed {} check", bound.key()),
                };
                violations.push(ThresholdViolation {
                    channel: field.clone(),
                    value: measured,
                    threshold: bound_to_threshold(field, bound),
                    message,
                });
            }
        }

        if !violations.is_empty() {
            let summary: Vec<&str> = violations.iter().map(|v| v.message.as_str()).collect();
            let message = format!("Failed: {}", summary.join("; "));
            return result(MonitorVerdict::Fail, violations, message);
        }
        if checked == 0 {
            return result(
                MonitorVerdict::Skip,
                Vec::new(),
                format!("No fields to check (skipped {skipped})"),
            );
        }
        result(
            MonitorVerdict::Pass,
            Vec::new(),
            format!("All {checked} field(s) within bounds for {}", state.name),
        )
    }

    /// Evaluates every sample of a stream batch and summarises the batch in
    /// one result.
    ///
    /// Each sample is checked at its own timestamp. The batch fails if any
    /// sample fails; at most `max_violations` violations are kept (the message
    /// reports the total). A batch with no checked fields is `Skip`.
    pub fn evaluate_batch(
        &mut self,
        schema: &StreamSchema,
        data: &StreamData,
        state: &EnvironmentalState,
        max_violations: usize,
    ) -> MonitorResult {
        let fields: Vec<&str> = schema.fields().iter().map(|f| f.name.as_str()).collect();
        let start = Timestamp::local(i64::try_from(data.timestamp_ns).unwrap_or(i64::MAX));
        let mut violations = Vec::new();
        let mut failed_samples = 0usize;
        let mut first_fail: Option<Timestamp> = None;
        let mut last: Option<MonitorResult> = None;
        for (i, sample) in data.samples.iter().enumerate() {
            let ts = Timestamp::local(i64::try_from(data.timestamp(i as u64)).unwrap_or(i64::MAX));
            let lookup = |f: &str| {
                fields.iter().position(|n| *n == f).and_then(|i| sample.get(i)).map(|v| v.as_f64())
            };
            let r = self.evaluate(lookup, state, &ts);
            if r.verdict == MonitorVerdict::Fail {
                failed_samples += 1;
                first_fail.get_or_insert(ts);
                let room = max_violations.saturating_sub(violations.len());
                violations.extend(r.violations.iter().take(room).cloned());
            }
            last = Some(r);
        }
        let Some(last) = last else {
            return MonitorResult {
                monitor_id: self.monitor_id.clone(),
                verdict: MonitorVerdict::Skip,
                timestamp: start,
                state_id: state.state_id.clone(),
                violations: Vec::new(),
                message: "Empty batch".to_string(),
            };
        };
        if failed_samples == 0 {
            let message = match last.verdict {
                MonitorVerdict::Pass => {
                    format!("{} sample(s) within bounds for {}", data.samples.len(), state.name)
                }
                _ => last.message,
            };
            return MonitorResult { timestamp: start, message, ..last };
        }
        let first = violations.first().map_or(String::new(), |v| v.message.clone());
        MonitorResult {
            monitor_id: self.monitor_id.clone(),
            verdict: MonitorVerdict::Fail,
            timestamp: first_fail.unwrap_or(start),
            state_id: state.state_id.clone(),
            violations,
            message: format!(
                "Failed: {failed_samples} of {} sample(s) out of bounds (first: {first})",
                data.samples.len()
            ),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn bounds(spec: &serde_json::Value) -> StateBounds {
        let mut states = IndexMap::new();
        for (state, fields) in spec.as_object().unwrap() {
            let mut m = IndexMap::new();
            for (field, b) in fields.as_object().unwrap() {
                m.insert(field.clone(), BoundCheck::from_json(b).unwrap());
            }
            states.insert(state.clone(), m);
        }
        StateBounds::new(states)
    }

    fn state(id: &str) -> EnvironmentalState {
        EnvironmentalState::new(id, id.to_uppercase(), "")
    }

    fn values<'a>(pairs: &'a [(&'a str, f64)]) -> impl Fn(&str) -> Option<f64> + 'a {
        move |f| pairs.iter().find(|(k, _)| *k == f).map(|(_, v)| *v)
    }

    fn echo_monitor() -> BoundsMonitor {
        BoundsMonitor::new(
            "echo",
            bounds(&json!({
                "default": {"v": {"special": "any"}},
                "low": {"v": {"within_range": [1.0, 0.3]}},
                "high": {"v": {"within_range": [4.0, 0.3]}, "i": {"less_than": 2.0}},
            })),
        )
    }

    #[test]
    fn pass_fail_skip_like_python() {
        let mut m = echo_monitor();
        let ts = Timestamp::local(1);
        let r = m.evaluate(values(&[("v", 1.1)]), &state("low"), &ts);
        assert_eq!(r.verdict, MonitorVerdict::Pass);
        assert_eq!(r.message, "All 1 field(s) within bounds for LOW");

        let r = m.evaluate(values(&[("v", 3.0), ("i", 2.5)]), &state("high"), &ts);
        assert_eq!(r.verdict, MonitorVerdict::Fail);
        assert_eq!(
            r.message,
            "Failed: v=3.0000 outside [3.7000, 4.3000]; i=2.5000 outside [-inf, 2.0000]"
        );
        assert_eq!(r.violations[1].threshold.high, Some(ThresholdBound::exclusive(2.0)));
        assert_eq!(r.violations[1].threshold.low, None);

        // State without own bounds falls back to default -> special: any -> skipped.
        let r = m.evaluate(values(&[("v", 99.0)]), &state("other"), &ts);
        assert_eq!(
            (r.verdict, r.message.as_str()),
            (MonitorVerdict::Skip, "No fields to check (skipped 1)")
        );

        let mut transition = state("low");
        transition.is_transition = true;
        assert_eq!(
            m.evaluate(values(&[("v", 99.0)]), &transition, &ts).verdict,
            MonitorVerdict::Skip
        );
    }

    #[test]
    fn violation_results_serialize() {
        // The Python monitor crashed building violations; these must round-trip.
        let mut m = echo_monitor();
        let r = m.evaluate(values(&[("i", 3.0), ("v", 4.0)]), &state("high"), &Timestamp::local(0));
        let back = MonitorResult::from_json_bytes(&r.to_json_bytes().unwrap()).unwrap();
        assert_eq!(back, r);
    }

    #[test]
    fn baseline_locks_per_state_entry() {
        let mut m =
            BoundsMonitor::new("b", bounds(&json!({"s": {"t": {"within_baseline": [25, 5, 1]}}})));
        let ts = Timestamp::local(0);
        assert!(m.evaluate(values(&[("t", 27.0)]), &state("s"), &ts).passed());
        assert!(m.evaluate(values(&[("t", 27.9)]), &state("s"), &ts).passed());
        assert!(m.evaluate(values(&[("t", 29.0)]), &state("s"), &ts).failed());
        let locked = m.bounds().get("s", "t").unwrap().to_json();
        assert_eq!(locked, json!({"within_baseline": [25.0, 5.0, 1.0, 27.0]}));
    }

    #[test]
    fn non_interval_violation_message() {
        let mut m =
            BoundsMonitor::new("m", bounds(&json!({"s": {"mode": {"good_values": [0, 1]}}})));
        let r = m.evaluate(values(&[("mode", 5.0)]), &state("s"), &Timestamp::local(0));
        assert_eq!(r.message, "Failed: mode=5.0000 failed good_values check");
        assert_eq!((r.violations[0].threshold.low, r.violations[0].threshold.high), (None, None));
    }

    #[test]
    fn batch_evaluation_summarises_samples() {
        use crate::common::DataType;
        use crate::streaming::{StreamField, StreamSchema};
        let schema = StreamSchema::new(
            "daq",
            vec![
                StreamField::new("v", DataType::F64, "V"),
                StreamField::new("i", DataType::F64, "A"),
            ],
        );
        let data = StreamData {
            schema_id: schema.schema_id(),
            timestamp_ns: 1_000,
            period_ns: 10,
            samples: vec![
                vec![4.0.into(), 1.0.into()],
                vec![3.0.into(), 2.5.into()],
                vec![4.1.into(), 3.0.into()],
            ],
        };
        let mut m = echo_monitor();
        let r = m.evaluate_batch(&schema, &data, &state("high"), 2);
        assert_eq!(r.verdict, MonitorVerdict::Fail);
        assert_eq!(r.timestamp, Timestamp::local(1_010)); // second sample
        assert_eq!(r.violations.len(), 2); // capped
        assert!(
            r.message.starts_with("Failed: 2 of 3 sample(s) out of bounds (first: v=3.0000"),
            "{}",
            r.message
        );

        let ok = StreamData { samples: vec![vec![4.0.into(), 1.0.into()]], ..data.clone() };
        let r = m.evaluate_batch(&schema, &ok, &state("high"), 2);
        assert_eq!(
            (r.verdict, r.message.as_str()),
            (MonitorVerdict::Pass, "1 sample(s) within bounds for HIGH")
        );
        let empty = StreamData { samples: vec![], ..data };
        assert_eq!(
            m.evaluate_batch(&schema, &empty, &state("high"), 2).verdict,
            MonitorVerdict::Skip
        );
    }

    #[test]
    fn fields_in_first_seen_order() {
        assert_eq!(echo_monitor().bounds().fields(), vec!["v", "i"]);
    }

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
