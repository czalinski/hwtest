use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Debug)]
pub struct EnvironmentalState {
    state_id: String,
    name: String,
    description: String,
    is_transition: bool,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct StateTransition {
    from_state: Option<String>,
    to_state: String,
    timestamp: f64,
    reason: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_environmental_state_serialization() {
        let state = EnvironmentalState {
            state_id: "ambient".to_string(),
            name: "Ambient".to_string(),
            description: "Room temperature, no stress".to_string(),
            is_transition: false,
        };
        let serialized = serde_json::to_string(&state).unwrap();
        assert_eq!(
            serialized,
            r#"{"state_id":"ambient","name":"Ambient","description":"Room temperature, no stress","is_transition":false}"#
        );
    }

    #[test]
    fn test_state_transition_serialization() {
        let transition = StateTransition {
            from_state: Some("ambient".to_string()),
            to_state: "hot".to_string(),
            timestamp: 1620000000.0,
            reason: "Begin thermal stress".to_string(),
        };
        let serialized = serde_json::to_string(&transition).unwrap();
        assert_eq!(
            serialized,
            r#"{"from_state":"ambient","to_state":"hot","timestamp":1620000000.0,"reason":"Begin thermal stress"}"#
        );
    }
}
