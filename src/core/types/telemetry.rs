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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_telemetry_value_serialization() {
        let value = TelemetryValue {
            channel: "voltage".to_string(),
            value: 3.3,
            unit: "V".to_string(),
            source_timestamp: 1620000000.0,
            publish_timestamp: 1620000001.0,
            quality: ValueQuality::Good,
        };
        let serialized = serde_json::to_string(&value).unwrap();
        assert_eq!(
            serialized,
            r#"{"channel":"voltage","value":3.3,"unit":"V","source_timestamp":1620000000.0,"publish_timestamp":1620000001.0,"quality":"good"}"#
        );
    }

    #[test]
    fn test_telemetry_message_serialization() {
        let value = TelemetryValue {
            channel: "voltage".to_string(),
            value: 3.3,
            unit: "V".to_string(),
            source_timestamp: 1620000000.0,
            publish_timestamp: 1620000001.0,
            quality: ValueQuality::Good,
        };
        let message = TelemetryMessage {
            source: "psu".to_string(),
            values: vec![value],
            sequence: 1,
        };
        let serialized = serde_json::to_string(&message).unwrap();
        assert_eq!(
            serialized,
            r#"{"source":"psu","values":[{"channel":"voltage","value":3.3,"unit":"V","source_timestamp":1620000000.0,"publish_timestamp":1620000001.0,"quality":"good"}],"sequence":1}"#
        );
    }
}
