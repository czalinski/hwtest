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
