//! Low/high thresholds per channel and per environmental state.

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};

use crate::common::{ChannelId, StateId};
use crate::error::Result;

/// Whether a bound includes its limit value.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BoundType {
    /// `value >= low` / `value <= high`.
    #[default]
    Inclusive,
    /// `value > low` / `value < high`.
    Exclusive,
}

/// One side of a [`Threshold`].
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct ThresholdBound {
    /// Limit value.
    pub value: f64,
    /// Inclusive or exclusive.
    #[serde(default)]
    pub bound_type: BoundType,
}

impl ThresholdBound {
    /// Inclusive bound.
    pub fn inclusive(value: f64) -> Self {
        Self { value, bound_type: BoundType::Inclusive }
    }

    /// Exclusive bound.
    pub fn exclusive(value: f64) -> Self {
        Self { value, bound_type: BoundType::Exclusive }
    }

    /// True if `test_value` satisfies this bound used as a lower limit.
    pub fn check_low(&self, test_value: f64) -> bool {
        match self.bound_type {
            BoundType::Inclusive => test_value >= self.value,
            BoundType::Exclusive => test_value > self.value,
        }
    }

    /// True if `test_value` satisfies this bound used as an upper limit.
    pub fn check_high(&self, test_value: f64) -> bool {
        match self.bound_type {
            BoundType::Inclusive => test_value <= self.value,
            BoundType::Exclusive => test_value < self.value,
        }
    }
}

/// Acceptable range for one channel. A missing side is unbounded.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Threshold {
    /// Channel this threshold applies to.
    pub channel: ChannelId,
    /// Lower limit.
    #[serde(default)]
    pub low: Option<ThresholdBound>,
    /// Upper limit.
    #[serde(default)]
    pub high: Option<ThresholdBound>,
}

impl Threshold {
    /// Creates a threshold.
    pub fn new(
        channel: impl Into<ChannelId>,
        low: Option<ThresholdBound>,
        high: Option<ThresholdBound>,
    ) -> Self {
        Self { channel: channel.into(), low, high }
    }

    /// True if `value` is within both limits.
    pub fn check(&self, value: f64) -> bool {
        self.low.is_none_or(|b| b.check_low(value)) && self.high.is_none_or(|b| b.check_high(value))
    }
}

/// All channel thresholds that apply in one environmental state.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StateThresholds {
    /// State these thresholds apply to.
    pub state_id: StateId,
    /// Thresholds keyed by channel, in definition order.
    pub thresholds: IndexMap<ChannelId, Threshold>,
}

impl StateThresholds {
    /// Threshold for `channel`, if defined.
    pub fn get(&self, channel: &str) -> Option<&Threshold> {
        self.thresholds.get(channel)
    }

    /// Checks `value` against `channel`'s threshold; `None` if undefined.
    pub fn check_value(&self, channel: &str, value: f64) -> Option<bool> {
        self.get(channel).map(|t| t.check(value))
    }

    /// Serializes to JSON bytes.
    ///
    /// # Errors
    /// Never fails in practice; returns [`crate::Error::Serialization`] on failure.
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inclusive_and_exclusive() {
        let t = Threshold::new(
            "x",
            Some(ThresholdBound::exclusive(0.0)),
            Some(ThresholdBound::inclusive(10.0)),
        );
        assert!(!t.check(0.0) && t.check(0.001) && t.check(10.0) && !t.check(10.001));
        assert!(Threshold::new("any", None, None).check(-1e300));
    }

    #[test]
    fn missing_or_null_sides_are_unbounded() {
        let t: Threshold =
            serde_json::from_str(r#"{"channel":"i","low":null,"high":{"value":5.0}}"#).unwrap();
        assert_eq!(t.low, None);
        assert_eq!(t.high, Some(ThresholdBound::inclusive(5.0)));
        let t: Threshold = serde_json::from_str(r#"{"channel":"i"}"#).unwrap();
        assert!(t.low.is_none() && t.high.is_none());
    }

    #[test]
    fn state_thresholds_lookup() {
        let mut thresholds = IndexMap::new();
        thresholds.insert(
            "v3v3".to_string(),
            Threshold::new(
                "v3v3",
                Some(ThresholdBound::inclusive(3.0)),
                Some(ThresholdBound::inclusive(3.6)),
            ),
        );
        let st = StateThresholds { state_id: "room".into(), thresholds };
        assert_eq!(st.check_value("v3v3", 3.3), Some(true));
        assert_eq!(st.check_value("v3v3", 4.0), Some(false));
        assert_eq!(st.check_value("other", 0.0), None);
        let back = StateThresholds::from_json_bytes(&st.to_json_bytes().unwrap()).unwrap();
        assert_eq!(back, st);
    }
}
