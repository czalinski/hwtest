//! Live telemetry monitor: evaluates a source's stream against YAML bounds in
//! the current environmental state and publishes one result per batch.

use std::time::Duration;

use hwtest_core::monitor::{BoundsMonitor, StateBounds};
use hwtest_core::{EnvironmentalState, MonitorResult};
use indexmap::IndexMap;
use tokio::sync::{mpsc, watch};
use tokio::task::JoinHandle;

use crate::connection::NatsConnection;
use crate::error::Result;
use crate::state::StateSubscriber;
use crate::subscriber::StreamSubscriber;

/// What to monitor.
#[derive(Debug, Clone)]
pub struct MonitorSpec {
    /// Monitor id used in results.
    pub monitor_id: String,
    /// Stream source to watch.
    pub source_id: String,
    /// Bounds per state and field (from the test definition).
    pub bounds: StateBounds,
    /// States in which to evaluate. Transitions to any other state id
    /// (including transition states) pause evaluation.
    pub states: IndexMap<String, EnvironmentalState>,
    /// Maximum violations kept per result.
    pub max_violations: usize,
}

/// A running monitor.
#[derive(Debug)]
pub struct TelemetryMonitor {
    task: JoinHandle<()>,
    state: watch::Receiver<Option<String>>,
}

impl TelemetryMonitor {
    /// Starts monitoring. Results are published on `{prefix}.monitor.results`
    /// and, if `results` is given, also sent there.
    ///
    /// # Errors
    /// [`crate::NatsError`] if the subscriptions cannot be created.
    pub async fn start(
        conn: NatsConnection,
        spec: MonitorSpec,
        results: Option<mpsc::Sender<MonitorResult>>,
    ) -> Result<Self> {
        let mut data = StreamSubscriber::subscribe(&conn, &spec.source_id).await?;
        let mut states = StateSubscriber::subscribe(&conn).await?;
        let (state_tx, state_rx) = watch::channel(None::<String>);
        let subject = conn.config().monitor_results_subject();
        let task = tokio::spawn(async move {
            let mut engine = BoundsMonitor::new(spec.monitor_id.clone(), spec.bounds.clone());
            let mut current: Option<EnvironmentalState> = None;
            loop {
                tokio::select! {
                    t = states.next() => {
                        let Some(t) = t else { break };
                        current = spec.states.get(&t.to_state).cloned();
                        let _ = state_tx.send(Some(t.to_state));
                    }
                    batch = data.next() => {
                        let Some(batch) = batch else { break };
                        let (Some(state), Some(schema)) = (current.as_ref(), data.current_schema()) else { continue };
                        let result = engine.evaluate_batch(&schema, &batch, state, spec.max_violations);
                        if let Err(e) = conn.publish_json(subject.clone(), &result).await {
                            tracing::warn!("monitor {}: cannot publish result: {e}", spec.monitor_id);
                        }
                        if let Some(tx) = &results {
                            if tx.send(result).await.is_err() {
                                break;
                            }
                        }
                    }
                }
            }
        });
        Ok(Self { task, state: state_rx })
    }

    /// The last state id received (whether or not it is monitored).
    pub fn current_state(&self) -> Option<String> {
        self.state.borrow().clone()
    }

    /// Waits until the monitor has seen `state_id`.
    pub async fn wait_for_state(&mut self, state_id: &str, timeout: Duration) -> bool {
        tokio::time::timeout(timeout, self.state.wait_for(|s| s.as_deref() == Some(state_id)))
            .await
            .is_ok_and(|r| r.is_ok())
    }

    /// Stops the monitor.
    pub fn stop(self) {
        self.task.abort();
    }
}

impl Drop for TelemetryMonitor {
    fn drop(&mut self) {
        self.task.abort();
    }
}
