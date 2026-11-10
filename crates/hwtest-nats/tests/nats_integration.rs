//! End-to-end tests against a real NATS server.
//!
//! Run with a JetStream-enabled server, e.g.:
//! ```text
//! docker run -d --name nats -p 14222:4222 nats:2.10-alpine -js
//! HWTEST_NATS_URL=nats://localhost:14222 cargo test -p hwtest-nats
//! ```
//! Without `HWTEST_NATS_URL` these tests are skipped.

use std::sync::atomic::{AtomicU32, Ordering};
use std::time::Duration;

use hwtest_core::bounds::BoundCheck;
use hwtest_core::monitor::StateBounds;
use hwtest_core::{
    DataType, EnvironmentalState, MonitorVerdict, StreamData, StreamField, StreamSchema,
};
use hwtest_nats::{
    MonitorSpec, NatsConfig, NatsConnection, StatePublisher, StateSubscriber, StreamPublisher,
    StreamSubscriber, TelemetryMonitor,
};
use indexmap::IndexMap;
use serde_json::json;

static COUNTER: AtomicU32 = AtomicU32::new(0);

/// A connection with an isolated stream and subject prefix, or `None` to skip.
async fn connect() -> Option<NatsConnection> {
    let url = std::env::var("HWTEST_NATS_URL").ok()?;
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let tag = format!("{}_{n}", std::process::id());
    let config = NatsConfig {
        stream_name: format!("TEST_{tag}"),
        subject_prefix: format!("t{tag}"),
        schema_publish_interval: 0.2,
        ..NatsConfig::from_url(url)
    };
    Some(NatsConnection::connect(config).await.expect("connect"))
}

async fn cleanup(conn: &NatsConnection) {
    let _ = conn.jetstream().delete_stream(&conn.config().stream_name).await;
}

fn schema() -> StreamSchema {
    StreamSchema::new(
        "daq",
        vec![StreamField::new("v", DataType::F64, "V"), StreamField::new("n", DataType::U16, "")],
    )
}

fn batch(schema: &StreamSchema, t0: u64, values: &[(f64, i64)]) -> StreamData {
    StreamData {
        schema_id: schema.schema_id(),
        timestamp_ns: t0,
        period_ns: 1_000_000,
        samples: values.iter().map(|(v, n)| vec![(*v).into(), (*n).into()]).collect(),
    }
}

#[tokio::test]
async fn publish_subscribe_round_trip() {
    let Some(conn) = connect().await else { return };
    let s = schema();
    let publisher = StreamPublisher::start(conn.clone(), s.clone()).await.unwrap();
    let mut sub = StreamSubscriber::subscribe(&conn, "daq").await.unwrap();
    // Late subscriber with deliver=new: schema arrives via re-broadcast.
    let got = sub.schema(Duration::from_secs(3)).await.unwrap();
    assert_eq!(*got, s);
    let sent = batch(&s, 1_000, &[(3.3, 1), (3.4, 2)]);
    publisher.publish(&sent).await.unwrap();
    let received = sub.next_timeout(Duration::from_secs(3)).await.expect("data");
    assert_eq!(received, sent);
    assert_eq!(sub.dropped(), 0);
    // Data that does not match the schema is rejected before sending.
    let wrong = StreamData { schema_id: 1, ..sent };
    assert!(publisher.publish(&wrong).await.is_err());
    publisher.stop();
    cleanup(&conn).await;
}

#[tokio::test]
async fn late_state_subscriber_sees_current_state() {
    let Some(conn) = connect().await else { return };
    let mut publisher = StatePublisher::new(conn.clone()).await.unwrap();
    publisher.set_state(EnvironmentalState::new("ambient", "Ambient", ""), "start").await.unwrap();
    let t = publisher.set_state(EnvironmentalState::new("hot", "Hot", ""), "soak").await.unwrap();
    assert_eq!(t.from_state.as_deref(), Some("ambient"));
    let mut sub = StateSubscriber::subscribe(&conn).await.unwrap();
    let first = tokio::time::timeout(Duration::from_secs(3), sub.next()).await.unwrap().unwrap();
    assert_eq!((first.to_state.as_str(), first.reason.as_str()), ("hot", "soak"));
    publisher.set_state(EnvironmentalState::new("cold", "Cold", ""), "next").await.unwrap();
    let next = tokio::time::timeout(Duration::from_secs(3), sub.next()).await.unwrap().unwrap();
    assert_eq!(next.to_state, "cold");
    cleanup(&conn).await;
}

#[tokio::test]
async fn monitor_evaluates_live_stream_per_state() {
    let Some(conn) = connect().await else { return };
    let s = schema();
    let mut bounds = IndexMap::new();
    bounds.insert(
        "low".to_string(),
        IndexMap::from([(
            "v".to_string(),
            BoundCheck::from_json(&json!({"within_range": [1.0, 0.1]})).unwrap(),
        )]),
    );
    let states = IndexMap::from([("low".to_string(), EnvironmentalState::new("low", "Low", ""))]);
    let (tx, mut rx) = tokio::sync::mpsc::channel(16);
    let spec = MonitorSpec {
        monitor_id: "mon".into(),
        source_id: "daq".into(),
        bounds: StateBounds::new(bounds),
        states,
        max_violations: 5,
    };
    let publisher = StreamPublisher::start(conn.clone(), s.clone()).await.unwrap();
    let mut state_pub = StatePublisher::new(conn.clone()).await.unwrap();
    let mut monitor = TelemetryMonitor::start(conn.clone(), spec, Some(tx)).await.unwrap();
    state_pub.set_state(EnvironmentalState::new("low", "Low", ""), "go").await.unwrap();
    assert!(monitor.wait_for_state("low", Duration::from_secs(3)).await);
    // Allow the monitor's subscriber to receive the re-broadcast schema.
    tokio::time::sleep(Duration::from_millis(500)).await;

    publisher.publish(&batch(&s, 0, &[(1.05, 0), (1.5, 1)])).await.unwrap();
    let r = tokio::time::timeout(Duration::from_secs(3), rx.recv()).await.unwrap().unwrap();
    assert_eq!(r.verdict, MonitorVerdict::Fail);
    assert_eq!(r.violations.len(), 1);
    assert_eq!(r.state_id, "low");

    publisher.publish(&batch(&s, 10, &[(1.0, 0)])).await.unwrap();
    let r = tokio::time::timeout(Duration::from_secs(3), rx.recv()).await.unwrap().unwrap();
    assert_eq!(r.verdict, MonitorVerdict::Pass);

    // Unknown (e.g. transition) state pauses evaluation.
    state_pub
        .set_state(EnvironmentalState::new("transition_low_to_x", "T", ""), "ramp")
        .await
        .unwrap();
    assert!(monitor.wait_for_state("transition_low_to_x", Duration::from_secs(3)).await);
    publisher.publish(&batch(&s, 20, &[(9.0, 0)])).await.unwrap();
    assert!(tokio::time::timeout(Duration::from_millis(500), rx.recv()).await.is_err());

    monitor.stop();
    publisher.stop();
    cleanup(&conn).await;
}

#[tokio::test]
async fn connect_failure_is_reported() {
    if std::env::var("HWTEST_NATS_URL").is_err() {
        return;
    }
    let config = NatsConfig { connect_timeout: 0.5, ..NatsConfig::from_url("nats://127.0.0.1:1") };
    let err = NatsConnection::connect(config).await.unwrap_err().to_string();
    assert!(err.starts_with("failed to connect to NATS nats://127.0.0.1:1"), "{err}");
}
