use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Debug)]
#[serde(rename_all = "lowercase")]
pub enum ValueQuality {
    Good,
    Bad,
    Unknown,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct TelemetryValue {
    channel: String,
    value: f64,
    unit: String,
    source_timestamp: f64,
    publish_timestamp: f64,
    quality: ValueQuality,
}

#[derive(Serialize, Deserialize, Debug)]
pub struct TelemetryMessage {
    source: String,
    values: Vec<TelemetryValue>,
    sequence: u64,
}
