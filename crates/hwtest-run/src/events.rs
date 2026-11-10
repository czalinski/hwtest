//! Progress events (for the runner and live displays).

use std::io::Write;
use std::sync::{Arc, Mutex};

use serde::Serialize;
use serde_json::Value;

/// A progress event. Serialized as `{"event": "<kind>", ...}`.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "event", rename_all = "snake_case")]
#[allow(missing_docs)]
pub enum Event {
    RunStarted {
        run_id: String,
        test_case: String,
        mode: String,
        states: Vec<String>,
        rack: Option<String>,
    },
    State {
        state_id: String,
        name: String,
        cycle: u64,
        transition: bool,
    },
    Measurement {
        state_id: String,
        topic: String,
        values: Value,
    },
    Monitor {
        monitor_id: String,
        verdict: String,
        message: String,
    },
    CycleCompleted {
        cycle: u64,
    },
    Log {
        level: String,
        message: String,
    },
    RunFinished {
        outcome: String,
        message: String,
        cycles_completed: u64,
        passes: u64,
        failures: u64,
    },
}

/// Where events go.
#[derive(Clone, Default)]
pub enum EventSink {
    /// Discard.
    #[default]
    None,
    /// One `@@hwtest {json}` line per event on stdout (read by the runner).
    Stdout,
    /// Collect in memory (tests).
    Memory(Arc<Mutex<Vec<Event>>>),
}

/// Prefix of event lines on stdout.
pub const EVENT_PREFIX: &str = "@@hwtest ";

impl EventSink {
    pub(crate) fn emit(&self, event: Event) {
        match self {
            EventSink::None => {}
            EventSink::Stdout => {
                if let Ok(json) = serde_json::to_string(&event) {
                    let mut out = std::io::stdout().lock();
                    let _ = writeln!(out, "{EVENT_PREFIX}{json}");
                    let _ = out.flush();
                }
            }
            EventSink::Memory(v) => {
                v.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(event)
            }
        }
    }
}

/// Parses an event line written by [`EventSink::Stdout`].
pub fn parse_event_line(line: &str) -> Option<Value> {
    serde_json::from_str(line.strip_prefix(EVENT_PREFIX)?).ok()
}
