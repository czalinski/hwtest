//! Environmental state publishing and subscription.

use async_nats::jetstream::consumer::{self, pull};
use futures::StreamExt;
use hwtest_core::{EnvironmentalState, StateTransition, Timestamp};
use tokio::sync::mpsc;
use tokio::task::JoinHandle;

use crate::connection::NatsConnection;
use crate::error::{OpExt, Result};

/// Publishes state transitions on `{prefix}.state`.
#[derive(Debug)]
pub struct StatePublisher {
    conn: NatsConnection,
    subject: String,
    current: Option<EnvironmentalState>,
}

impl StatePublisher {
    /// Creates a publisher (ensures the stream exists).
    ///
    /// # Errors
    /// [`crate::NatsError`] if the stream cannot be created.
    pub async fn new(conn: NatsConnection) -> Result<Self> {
        conn.ensure_stream().await?;
        let subject = conn.config().state_subject();
        Ok(Self { conn, subject, current: None })
    }

    /// The last state set.
    pub fn current(&self) -> Option<&EnvironmentalState> {
        self.current.as_ref()
    }

    /// Publishes a transition to `state` and waits for the acknowledgement.
    ///
    /// # Errors
    /// [`crate::NatsError::Operation`] if publishing fails.
    pub async fn set_state(
        &mut self,
        state: EnvironmentalState,
        reason: &str,
    ) -> Result<StateTransition> {
        let transition = StateTransition {
            from_state: self.current.as_ref().map(|s| s.state_id.clone()),
            to_state: state.state_id.clone(),
            timestamp: Timestamp::now("local"),
            reason: reason.to_string(),
        };
        let payload = transition.to_json_bytes()?;
        self.conn
            .jetstream()
            .publish(self.subject.clone(), payload.into())
            .await
            .op("publish state")?
            .await
            .op("publish state")?;
        tracing::info!("state {:?} -> {} ({reason})", transition.from_state, transition.to_state);
        self.current = Some(state);
        Ok(transition)
    }
}

/// Receives state transitions. Starts with the most recent transition, so a
/// late subscriber learns the current state immediately.
#[derive(Debug)]
pub struct StateSubscriber {
    rx: mpsc::Receiver<StateTransition>,
    task: JoinHandle<()>,
}

impl StateSubscriber {
    /// Subscribes to `{prefix}.state`.
    ///
    /// # Errors
    /// [`crate::NatsError::Operation`] if the consumer cannot be created.
    pub async fn subscribe(conn: &NatsConnection) -> Result<Self> {
        let stream = conn.ensure_stream().await?;
        let consumer = stream
            .create_consumer(pull::OrderedConfig {
                filter_subject: conn.config().state_subject(),
                deliver_policy: consumer::DeliverPolicy::LastPerSubject,
                ..Default::default()
            })
            .await
            .op("create state consumer")?;
        let mut messages = consumer.messages().await.op("subscribe state")?;
        let (tx, rx) = mpsc::channel(256);
        let task = tokio::spawn(async move {
            while let Some(Ok(msg)) = messages.next().await {
                match StateTransition::from_json_bytes(&msg.payload) {
                    Ok(t) => {
                        if tx.send(t).await.is_err() {
                            break;
                        }
                    }
                    Err(e) => tracing::warn!("bad state message: {e}"),
                }
            }
        });
        Ok(Self { rx, task })
    }

    /// The next transition; `None` when the subscription ends.
    pub async fn next(&mut self) -> Option<StateTransition> {
        self.rx.recv().await
    }
}

impl Drop for StateSubscriber {
    fn drop(&mut self) {
        self.task.abort();
    }
}
