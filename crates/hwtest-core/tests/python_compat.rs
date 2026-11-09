//! Wire-compatibility tests against golden fixtures produced by the original
//! pure-Python `hwtest_core` implementation (`fixtures/python_golden.json`).
//!
//! These guarantee that Rust and Python publishers/subscribers interoperate
//! on NATS and that previously stored data still decodes.

use hwtest_core::bounds::BoundCheck;
use hwtest_core::streaming::{SampleValue, StreamData, StreamField, StreamSchema};
use hwtest_core::{
    DataType, EnvironmentalState, MonitorResult, StateThresholds, StateTransition,
    TelemetryMessage, TelemetryValue, Threshold,
};
use serde::de::DeserializeOwned;
use serde::Serialize;
use serde_json::Value;

fn fixtures() -> Value {
    serde_json::from_str(include_str!("fixtures/python_golden.json")).unwrap()
}

fn hex(s: &Value) -> Vec<u8> {
    let s = s.as_str().unwrap();
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
}

fn schema_id(v: &Value) -> u32 {
    u32::try_from(v["schema_id"].as_u64().unwrap()).unwrap()
}

fn adc_schema() -> StreamSchema {
    StreamSchema::new(
        "adc",
        vec![
            StreamField::new("ch0", DataType::F32, "V"),
            StreamField::new("ch1", DataType::F32, "V"),
            StreamField::new("ch2", DataType::F64, "°C"),
        ],
    )
}

#[test]
fn schema_frame_matches_python() {
    let fx = &fixtures()["schema_f32x2_f64"];
    let schema = adc_schema();
    assert_eq!(schema.schema_id(), schema_id(fx));
    assert_eq!(schema.to_bytes().unwrap(), hex(&fx["bytes"]));
    assert_eq!(StreamSchema::from_bytes(&hex(&fx["bytes"])).unwrap(), schema);
}

#[test]
fn data_frame_matches_python() {
    let fx = &fixtures()["data_f32x2_f64"];
    let schema = adc_schema();
    let data = StreamData {
        schema_id: schema.schema_id(),
        timestamp_ns: 1_704_067_200_000_000_000,
        period_ns: 1_000_000,
        samples: vec![
            vec![3.3.into(), 5.0.into(), 12.0.into()],
            vec![3.29.into(), 4.99.into(), 11.9.into()],
        ],
    };
    assert_eq!(data.to_bytes(&schema).unwrap(), hex(&fx["bytes"]));

    // Decoding must reproduce exactly what Python decoded (f32 widened to f64).
    let decoded = StreamData::from_bytes(&hex(&fx["bytes"]), &schema).unwrap();
    let expected: Vec<Vec<f64>> = serde_json::from_value(fx["decoded"].clone()).unwrap();
    let got: Vec<Vec<f64>> =
        decoded.samples.iter().map(|s| s.iter().map(|v| v.as_f64()).collect()).collect();
    assert_eq!(got, expected);
}

#[test]
fn all_data_types_match_python() {
    let fx = fixtures();
    let fields = DataType::ALL
        .iter()
        .map(|t| StreamField::new(format!("{t:?}").to_lowercase(), *t, "u"))
        .collect();
    let schema = StreamSchema::new("all_types", fields);
    assert_eq!(schema.schema_id(), schema_id(&fx["schema_all_types"]));
    assert_eq!(schema.to_bytes().unwrap(), hex(&fx["schema_all_types"]["bytes"]));

    let sample: Vec<SampleValue> = vec![
        SampleValue::Int(-128),
        SampleValue::Int(-32768),
        SampleValue::Int(-2_147_483_648),
        SampleValue::Int(i64::MIN),
        SampleValue::Int(255),
        SampleValue::Int(65535),
        SampleValue::Int(4_294_967_295),
        SampleValue::UInt(u64::MAX),
        SampleValue::Float(1.5),
        SampleValue::Float(-2.25),
    ];
    let data = StreamData {
        schema_id: schema.schema_id(),
        timestamp_ns: 1,
        period_ns: 2,
        samples: vec![sample],
    };
    let bytes = hex(&fx["data_all_types"]["bytes"]);
    assert_eq!(data.to_bytes(&schema).unwrap(), bytes);
    assert_eq!(StreamData::from_bytes(&bytes, &schema).unwrap(), data);
}

#[test]
fn empty_schema_matches_python() {
    let fx = &fixtures()["schema_empty"];
    let schema = StreamSchema::new("", vec![]);
    assert_eq!(schema.schema_id(), schema_id(fx));
    assert_eq!(schema.to_bytes().unwrap(), hex(&fx["bytes"]));
}

/// Parses a Python `to_dict()` fixture and checks re-serialization is identical.
fn json_round_trip<T: Serialize + DeserializeOwned>(name: &str) {
    let fx = fixtures()[name].clone();
    let parsed: T = serde_json::from_value(fx.clone()).unwrap_or_else(|e| panic!("{name}: {e}"));
    assert_eq!(serde_json::to_value(&parsed).unwrap(), fx, "{name}");
}

#[test]
fn json_messages_match_python() {
    json_round_trip::<TelemetryValue>("telemetry_value");
    json_round_trip::<TelemetryMessage>("telemetry_message");
    json_round_trip::<EnvironmentalState>("environmental_state");
    json_round_trip::<StateTransition>("state_transition");
    json_round_trip::<StateTransition>("state_transition_initial");
    json_round_trip::<Threshold>("threshold");
    json_round_trip::<StateThresholds>("state_thresholds");
    json_round_trip::<MonitorResult>("monitor_result");
}

#[test]
fn bound_specs_match_python() {
    for spec in fixtures()["bounds"].as_array().unwrap() {
        let b = BoundCheck::from_json(spec).unwrap();
        assert_eq!(&b.to_json(), spec);
    }
}
