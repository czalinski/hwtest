//! Stream subscriber: decodes schema and data frames for one source.

use std::sync::Arc;
use std::time::Duration;

use async_nats::jetstream::consumer::{self, pull};
use futures::StreamExt;
use hwtest_core::streaming::{peek_header, MSG_TYPE_DATA, MSG_TYPE_SCHEMA};
use hwtest_core::{StreamData, StreamSchema};
use tokio::sync::{mpsc, watch};
use tokio::task::JoinHandle;

use crate::config::DeliverPolicy;
use crate::connection::NatsConnection;
use crate::error::{NatsError, OpExt, Result};

/// Queue depth for decoded batches; when full, the oldest are dropped (and
/// counted) rather than stalling the NATS connection.
const QUEUE_DEPTH: usize = 1024;

/// Receives one source's stream.
#[derive(Debug)]
pub struct StreamSubscriber {
    source_id: String,
    schema: watch::Receiver<Option<Arc<StreamSchema>>>,
    data: mpsc::Receiver<StreamData>,
    dropped: Arc<std::sync::atomic::AtomicU64>,
    task: JoinHandle<()>,
}

pub(crate) fn deliver_policy(p: DeliverPolicy) -> consumer::DeliverPolicy {
    match p {
        DeliverPolicy::All => consumer::DeliverPolicy::All,
        DeliverPolicy::Last => consumer::DeliverPolicy::Last,
        DeliverPolicy::New => consumer::DeliverPolicy::New,
    }
}

impl StreamSubscriber {
    /// Subscribes to `source_id` (schema and data subjects).
    ///
    /// # Errors
    /// [`NatsError::Operation`] if the consumer cannot be created.
    pub async fn subscribe(conn: &NatsConnection, source_id: &str) -> Result<Self> {
        let stream = conn.ensure_stream().await?;
        let consumer = stream
            .create_consumer(pull::OrderedConfig {
                filter_subject: conn.config().source_filter(source_id),
                deliver_policy: deliver_policy(conn.config().consumer_deliver_policy),
                ..Default::default()
            })
            .await
            .op("create consumer")?;
        let mut messages = consumer.messages().await.op("subscribe")?;
        let (schema_tx, schema_rx) = watch::channel(None::<Arc<StreamSchema>>);
        let (data_tx, data_rx) = mpsc::channel(QUEUE_DEPTH);
        let dropped = Arc::new(std::sync::atomic::AtomicU64::new(0));
        let dropped_task = dropped.clone();
        let source = source_id.to_string();
        let task = tokio::spawn(async move {
            let mut current: Option<Arc<StreamSchema>> = None;
            while let Some(msg) = messages.next().await {
                let msg = match msg {
                    Ok(m) => m,
                    Err(e) => {
                        tracing::warn!("{source}: stream error: {e}");
                        continue;
                    }
                };
                let Ok((kind, schema_id)) = peek_header(&msg.payload) else { continue };
                match kind {
                    MSG_TYPE_SCHEMA => match StreamSchema::from_bytes(&msg.payload) {
                        Ok(s) if current.as_ref().is_none_or(|c| **c != s) => {
                            let s = Arc::new(s);
                            current = Some(s.clone());
                            let _ = schema_tx.send(Some(s));
                        }
                        Ok(_) => {}
                        Err(e) => tracing::warn!("{source}: bad schema frame: {e}"),
                    },
                    MSG_TYPE_DATA => {
                        let Some(schema) = current.as_ref().filter(|s| s.schema_id() == schema_id)
                        else {
                            continue; // no (matching) schema yet
                        };
                        match StreamData::from_bytes(&msg.payload, schema) {
                            Ok(d) => {
                                if let Err(mpsc::error::TrySendError::Full(_)) = data_tx.try_send(d)
                                {
                                    dropped_task.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                                }
                            }
                            Err(e) => tracing::warn!("{source}: bad data frame: {e}"),
                        }
                    }
                    other => tracing::warn!("{source}: unknown message type {other}"),
                }
                if data_tx.is_closed() {
                    break;
                }
            }
        });
        Ok(Self {
            source_id: source_id.to_string(),
            schema: schema_rx,
            data: data_rx,
            dropped,
            task,
        })
    }

    /// The source being received.
    pub fn source_id(&self) -> &str {
        &self.source_id
    }

    /// The current schema, waiting up to `timeout` for the first one.
    ///
    /// # Errors
    /// [`NatsError::Timeout`] if no schema arrives in time.
    pub async fn schema(&mut self, timeout: Duration) -> Result<Arc<StreamSchema>> {
        let wait = self.schema.wait_for(Option::is_some);
        match tokio::time::timeout(timeout, wait).await {
            Ok(Ok(s)) if s.is_some() => Ok(s.clone().unwrap_or_else(|| unreachable!())),
            _ => Err(NatsError::Timeout(format!("schema from source {:?}", self.source_id))),
        }
    }

    /// The schema if one has been received.
    pub fn current_schema(&self) -> Option<Arc<StreamSchema>> {
        self.schema.borrow().clone()
    }

    /// The next batch; `None` when the subscription ends.
    pub async fn next(&mut self) -> Option<StreamData> {
        self.data.recv().await
    }

    /// The next batch, or `None` after `timeout`.
    pub async fn next_timeout(&mut self, timeout: Duration) -> Option<StreamData> {
        tokio::time::timeout(timeout, self.data.recv()).await.ok().flatten()
    }

    /// Batches dropped because the consumer fell behind.
    pub fn dropped(&self) -> u64 {
        self.dropped.load(std::sync::atomic::Ordering::Relaxed)
    }
}

impl Drop for StreamSubscriber {
    fn drop(&mut self) {
        self.task.abort();
    }
}
