//! Stream publisher: data frames plus periodic schema re-broadcast.

use std::sync::Arc;

use hwtest_core::{StreamData, StreamSchema};
use tokio::task::JoinHandle;

use crate::config::NatsConfig;
use crate::connection::NatsConnection;
use crate::error::{OpExt, Result};

/// Publishes one source's stream. The schema is published immediately and
/// then every `schema_publish_interval` until [`StreamPublisher::stop`] (or drop).
#[derive(Debug)]
pub struct StreamPublisher {
    conn: NatsConnection,
    schema: Arc<StreamSchema>,
    data_subject: String,
    broadcaster: JoinHandle<()>,
}

impl StreamPublisher {
    /// Ensures the stream exists and starts the schema broadcaster.
    ///
    /// # Errors
    /// [`crate::NatsError`] if the stream cannot be created or the first
    /// schema cannot be published.
    pub async fn start(conn: NatsConnection, schema: StreamSchema) -> Result<Self> {
        conn.ensure_stream().await?;
        let schema = Arc::new(schema);
        let schema_subject = conn.config().schema_subject(schema.source_id());
        let frame = bytes::Bytes::from(schema.to_bytes()?);
        conn.jetstream()
            .publish(schema_subject.clone(), frame.clone())
            .await
            .op("publish schema")?
            .await
            .op("publish schema")?;
        let interval = NatsConfig::secs(conn.config().schema_publish_interval);
        let client = conn.client().clone();
        let broadcaster = tokio::spawn(async move {
            let mut tick = tokio::time::interval(interval);
            tick.tick().await; // first tick is immediate; already published
            loop {
                tick.tick().await;
                if let Err(e) = client.publish(schema_subject.clone(), frame.clone()).await {
                    tracing::warn!("failed to publish schema on {schema_subject}: {e}");
                }
            }
        });
        let data_subject = conn.config().data_subject(schema.source_id());
        Ok(Self { conn, schema, data_subject, broadcaster })
    }

    /// The published schema.
    pub fn schema(&self) -> &StreamSchema {
        &self.schema
    }

    /// Publishes a batch and waits for the `JetStream` acknowledgement.
    ///
    /// # Errors
    /// [`crate::NatsError::Protocol`] if `data` does not match the schema;
    /// [`crate::NatsError::Operation`] if publishing fails.
    pub async fn publish(&self, data: &StreamData) -> Result<()> {
        let payload = data.to_bytes(&self.schema)?;
        self.conn
            .jetstream()
            .publish(self.data_subject.clone(), payload.into())
            .await
            .op("publish data")?
            .await
            .op("publish data")?;
        Ok(())
    }

    /// Stops the schema broadcaster.
    pub fn stop(self) {
        self.broadcaster.abort();
    }
}

impl Drop for StreamPublisher {
    fn drop(&mut self) {
        self.broadcaster.abort();
    }
}
