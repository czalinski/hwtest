//! JSON telemetry values and batches (the non-streaming transport).

use serde::{Deserialize, Serialize};

use crate::common::{ChannelId, SourceId, Timestamp};
use crate::error::Result;
use crate::state::{default_source, nullable_f64};

/// Trustworthiness of a measurement.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ValueQuality {
    /// Valid and current.
    #[default]
    Good,
    /// Possibly inaccurate (e.g. sensor warming up).
    Uncertain,
    /// Known invalid (e.g. sensor fault).
    Bad,
    /// Outdated (e.g. communication timeout).
    Stale,
}

/// One measurement from one channel.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(into = "TelemetryValueWire", from = "TelemetryValueWire")]
pub struct TelemetryValue {
    /// Channel that was measured.
    pub channel: ChannelId,
    /// Measured value.
    pub value: f64,
    /// Unit, e.g. `"V"`.
    pub unit: String,
    /// When the measurement was taken.
    pub source_timestamp: Timestamp,
    /// When it was published, if known.
    pub publish_timestamp: Option<Timestamp>,
    /// Quality indicator.
    pub quality: ValueQuality,
}

#[derive(Serialize, Deserialize)]
struct TelemetryValueWire {
    channel: String,
    #[serde(deserialize_with = "nullable_f64")]
    value: f64,
    unit: String,
    source_timestamp: i64,
    #[serde(default = "default_source")]
    source_timestamp_source: String,
    #[serde(default)]
    publish_timestamp: Option<i64>,
    #[serde(default)]
    publish_timestamp_source: Option<String>,
    #[serde(default)]
    quality: ValueQuality,
}

impl From<TelemetryValue> for TelemetryValueWire {
    fn from(v: TelemetryValue) -> Self {
        let (publish_timestamp, publish_timestamp_source) = match v.publish_timestamp {
            Some(t) => (Some(t.unix_ns), Some(t.source)),
            None => (None, None),
        };
        Self {
            channel: v.channel,
            value: v.value,
            unit: v.unit,
            source_timestamp: v.source_timestamp.unix_ns,
            source_timestamp_source: v.source_timestamp.source,
            publish_timestamp,
            publish_timestamp_source,
            quality: v.quality,
        }
    }
}

impl From<TelemetryValueWire> for TelemetryValue {
    fn from(w: TelemetryValueWire) -> Self {
        Self {
            channel: w.channel,
            value: w.value,
            unit: w.unit,
            source_timestamp: Timestamp::new(w.source_timestamp, w.source_timestamp_source),
            publish_timestamp: w.publish_timestamp.map(|ns| {
                Timestamp::new(ns, w.publish_timestamp_source.unwrap_or_else(default_source))
            }),
            quality: w.quality,
        }
    }
}

/// A batch of values from one source, with a sequence number for gap detection.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TelemetryMessage {
    /// Publishing source.
    pub source: SourceId,
    /// Values in the batch.
    pub values: Vec<TelemetryValue>,
    /// Monotonically increasing sequence number.
    pub sequence: u64,
}

impl TelemetryMessage {
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
    fn minimal_value_uses_defaults() {
        let v: TelemetryValue = serde_json::from_value(
            json!({"channel": "t", "value": 25, "unit": "C", "source_timestamp": 7}),
        )
        .unwrap();
        assert_eq!(v.value, 25.0);
        assert_eq!(v.source_timestamp, Timestamp::local(7));
        assert_eq!(v.publish_timestamp, None);
        assert_eq!(v.quality, ValueQuality::Good);
    }

    #[test]
    fn nan_is_written_as_null_and_read_back() {
        let v = TelemetryValue {
            channel: "tc0".into(),
            value: f64::NAN,
            unit: "degC".into(),
            source_timestamp: Timestamp::local(1),
            publish_timestamp: None,
            quality: ValueQuality::Bad,
        };
        let bytes = serde_json::to_vec(&v).unwrap();
        assert!(String::from_utf8_lossy(&bytes).contains(r#""value":null"#));
        let back: TelemetryValue = serde_json::from_slice(&bytes).unwrap();
        assert!(back.value.is_nan());
    }

    #[test]
    fn message_round_trip() {
        let m = TelemetryMessage {
            source: "psu".into(),
            values: vec![TelemetryValue {
                channel: "v".into(),
                value: 3.3,
                unit: "V".into(),
                source_timestamp: Timestamp::new(1, "ptp"),
                publish_timestamp: Some(Timestamp::local(2)),
                quality: ValueQuality::Stale,
            }],
            sequence: 9,
        };
        assert_eq!(TelemetryMessage::from_json_bytes(&m.to_json_bytes().unwrap()).unwrap(), m);
    }
}
