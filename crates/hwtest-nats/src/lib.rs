//! NATS `JetStream` transport for hwtest telemetry.
//!
//! Wire layout (compatible with the original Python `hwtest_nats`):
//!
//! | Subject | Payload |
//! |---------|---------|
//! | `{prefix}.{source}.schema` | binary schema frame, re-published every `schema_publish_interval` |
//! | `{prefix}.{source}.data` | binary data frames |
//! | `{prefix}.state` | JSON `StateTransition` |
//! | `{prefix}.monitor.results` | JSON `MonitorResult` |
//!
//! All subjects are captured by one `JetStream` stream (`TELEMETRY` by default,
//! 24 h retention). Subscribers use ordered (ephemeral, self-healing) consumers.

pub mod config;
pub mod connection;
pub mod error;
pub mod monitor;
pub mod publisher;
pub mod state;
pub mod subscriber;

pub use config::{DeliverPolicy, NatsConfig};
pub use connection::NatsConnection;
pub use error::{NatsError, Result};
pub use monitor::{MonitorSpec, TelemetryMonitor};
pub use publisher::StreamPublisher;
pub use state::{StatePublisher, StateSubscriber};
pub use subscriber::StreamSubscriber;
