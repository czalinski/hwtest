//! Environmental states (ambient, hot soak, vibration, ...) and transitions.

use serde::{Deserialize, Deserializer, Serialize};
use serde_json::{Map, Value};

use crate::common::{StateId, Timestamp, LOCAL_SOURCE};
use crate::error::Result;

pub(crate) fn default_source() -> String {
    LOCAL_SOURCE.to_owned()
}

/// Deserializes a measurement that may be `null` (how non-finite values are
/// written to JSON) as NaN.
pub(crate) fn nullable_f64<'de, D: Deserializer<'de>>(d: D) -> std::result::Result<f64, D::Error> {
    Ok(Option::<f64>::deserialize(d)?.unwrap_or(f64::NAN))
}

/// A discrete environmental condition. Transition states suspend monitor
/// evaluation while conditions are changing.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EnvironmentalState {
    /// Unique state identifier.
    pub state_id: StateId,
    /// Human-readable name.
    pub name: String,
    /// Description.
    pub description: String,
    /// True while moving between stable states.
    #[serde(default)]
    pub is_transition: bool,
    /// Arbitrary state data (e.g. target temperature).
    #[serde(default)]
    pub metadata: Map<String, Value>,
}

impl EnvironmentalState {
    /// Creates a stable (non-transition) state with no metadata.
    pub fn new(
        state_id: impl Into<StateId>,
        name: impl Into<String>,
        description: impl Into<String>,
    ) -> Self {
        Self {
            state_id: state_id.into(),
            name: name.into(),
            description: description.into(),
            is_transition: false,
            metadata: Map::new(),
        }
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

/// A change from one state to another.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(into = "StateTransitionWire", from = "StateTransitionWire")]
pub struct StateTransition {
    /// Previous state, `None` for the initial state.
    pub from_state: Option<StateId>,
    /// New state.
    pub to_state: StateId,
    /// When the transition happened.
    pub timestamp: Timestamp,
    /// Why it happened.
    pub reason: String,
}

/// Treats `null` and `""` as absent, like the Python `if data.get(...)`.
fn non_empty<'de, D: Deserializer<'de>>(d: D) -> std::result::Result<Option<String>, D::Error> {
    Ok(Option::<String>::deserialize(d)?.filter(|s| !s.is_empty()))
}

#[derive(Serialize, Deserialize)]
struct StateTransitionWire {
    #[serde(default, deserialize_with = "non_empty")]
    from_state: Option<String>,
    to_state: String,
    timestamp: i64,
    #[serde(default = "default_source")]
    timestamp_source: String,
    #[serde(default)]
    reason: String,
}

impl From<StateTransition> for StateTransitionWire {
    fn from(t: StateTransition) -> Self {
        Self {
            from_state: t.from_state,
            to_state: t.to_state,
            timestamp: t.timestamp.unix_ns,
            timestamp_source: t.timestamp.source,
            reason: t.reason,
        }
    }
}

impl From<StateTransitionWire> for StateTransition {
    fn from(w: StateTransitionWire) -> Self {
        Self {
            from_state: w.from_state,
            to_state: w.to_state,
            timestamp: Timestamp::new(w.timestamp, w.timestamp_source),
            reason: w.reason,
        }
    }
}

impl StateTransition {
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn state_defaults() {
        let s: EnvironmentalState =
            serde_json::from_value(json!({"state_id": "a", "name": "A", "description": ""}))
                .unwrap();
        assert!(!s.is_transition);
        assert!(s.metadata.is_empty());
    }

    #[test]
    fn transition_wire_format() {
        let t = StateTransition {
            from_state: None,
            to_state: "hot".into(),
            timestamp: Timestamp::new(5, "ptp"),
            reason: String::new(),
        };
        let v = serde_json::to_value(&t).unwrap();
        assert_eq!(
            v,
            json!({"from_state": null, "to_state": "hot", "timestamp": 5,
                   "timestamp_source": "ptp", "reason": ""})
        );
        let back: StateTransition =
            serde_json::from_value(json!({"from_state": "", "to_state": "x", "timestamp": 1}))
                .unwrap();
        assert_eq!(back.from_state, None);
        assert_eq!(back.timestamp, Timestamp::local(1));
    }
}
