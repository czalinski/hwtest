//! Launching and supervising test scripts.

use std::collections::{BTreeMap, VecDeque};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use hwtest_run::parse_event_line;
use hwtest_spec::StationConfig;
use serde::Serialize;
use serde_json::Value;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;

/// Lines of script output kept for the dashboard.
const LOG_LINES: usize = 2000;

/// Runner state.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum RunState {
    /// Nothing running.
    Idle,
    /// A test is running.
    Running,
    /// A stop was requested.
    Stopping,
}

/// Current run (same fields as the Python runner, plus outcome/run id).
#[derive(Debug, Clone, Serialize)]
pub struct RunStatus {
    /// State.
    pub state: RunState,
    /// Test case id of the current/last run.
    pub test_case_id: Option<String>,
    /// Mode.
    pub mode: Option<String>,
    /// Current environmental state.
    pub current_state: Option<String>,
    /// Completed cycles.
    pub cycle: u64,
    /// Counters (passes, failures, measurements).
    pub stats: BTreeMap<String, u64>,
    /// Start time (ISO-8601).
    pub started_at: Option<String>,
    /// Status / result message.
    pub message: String,
    /// Run id.
    pub run_id: Option<String>,
    /// Outcome of the last finished run.
    pub outcome: Option<String>,
    /// Number of log lines so far (for incremental `/run/log?since=`).
    pub log_lines: u64,
}

/// Runner settings.
#[derive(Debug, Clone)]
pub struct RunnerConfig {
    /// Station configuration.
    pub station: StationConfig,
    /// Python interpreter.
    pub python: String,
    /// NATS URL passed to scripts.
    pub nats_url: Option<String>,
    /// Results database passed to scripts.
    pub db_path: Option<PathBuf>,
    /// Run scripts against instrument emulators.
    pub emulate: bool,
    /// Grace period between SIGINT and kill.
    pub stop_timeout: Duration,
}

struct Inner {
    status: RunStatus,
    log: VecDeque<String>,
    total_lines: u64,
    pid: Option<u32>,
}

/// Runs one test at a time.
#[derive(Clone)]
pub struct Executor {
    config: Arc<RunnerConfig>,
    inner: Arc<Mutex<Inner>>,
}

/// Why a run could not start.
#[derive(Debug, thiserror::Error)]
pub enum StartError {
    /// A test is already running.
    #[error("a test is already running ({0})")]
    Busy(String),
    /// Unknown test case.
    #[error("unknown test case {0:?}")]
    UnknownTestCase(String),
    /// Mode not allowed for this test case.
    #[error("mode {mode:?} is not enabled for {test_case:?} (allowed: {allowed})")]
    Mode {
        /// Requested mode.
        mode: String,
        /// Test case.
        test_case: String,
        /// Allowed modes.
        allowed: String,
    },
    /// No script found.
    #[error("no script for test case {0:?}: set script: in the station file or add {0}.py next to it or in tests/")]
    NoScript(String),
    /// The process could not be started.
    #[error("cannot start {0}")]
    Spawn(String),
}

fn lock(m: &Mutex<Inner>) -> MutexGuard<'_, Inner> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

impl Executor {
    /// Creates an idle executor.
    pub fn new(config: RunnerConfig) -> Self {
        let status = RunStatus {
            state: RunState::Idle,
            test_case_id: None,
            mode: None,
            current_state: None,
            cycle: 0,
            stats: BTreeMap::new(),
            started_at: None,
            message: "Idle".into(),
            run_id: None,
            outcome: None,
            log_lines: 0,
        };
        Self {
            config: Arc::new(config),
            inner: Arc::new(Mutex::new(Inner {
                status,
                log: VecDeque::new(),
                total_lines: 0,
                pid: None,
            })),
        }
    }

    /// Settings.
    pub fn config(&self) -> &RunnerConfig {
        &self.config
    }

    /// Current status.
    pub fn status(&self) -> RunStatus {
        lock(&self.inner).status.clone()
    }

    /// Log lines from index `since` (and the next index).
    pub fn log_since(&self, since: u64) -> (Vec<String>, u64) {
        let i = lock(&self.inner);
        let first = i.total_lines - i.log.len() as u64;
        let skip = usize::try_from(since.saturating_sub(first)).unwrap_or(usize::MAX);
        (i.log.iter().skip(skip).cloned().collect(), i.total_lines)
    }

    fn push_log(inner: &Mutex<Inner>, line: String) {
        let mut i = lock(inner);
        if i.log.len() == LOG_LINES {
            i.log.pop_front();
        }
        i.log.push_back(line);
        i.total_lines += 1;
        i.status.log_lines = i.total_lines;
    }

    fn apply_event(inner: &Mutex<Inner>, ev: &Value) {
        let mut i = lock(inner);
        let s = &mut i.status;
        let text = |k: &str| ev.get(k).and_then(Value::as_str).map(String::from);
        let num = |k: &str| ev.get(k).and_then(Value::as_u64).unwrap_or(0);
        match ev.get("event").and_then(Value::as_str).unwrap_or("") {
            "run_started" => {
                s.run_id = text("run_id");
                s.message = "Running".into();
            }
            "state" => {
                s.current_state = text("name");
                s.message = format!("Testing {}", text("name").unwrap_or_default());
            }
            "measurement" => *s.stats.entry("measurements".into()).or_default() += 1,
            "monitor" => match text("verdict").as_deref() {
                Some("pass") => *s.stats.entry("passes".into()).or_default() += 1,
                Some("fail") => {
                    *s.stats.entry("failures".into()).or_default() += 1;
                    s.message = text("message").unwrap_or_default();
                }
                _ => *s.stats.entry("skips".into()).or_default() += 1,
            },
            "cycle_completed" => s.cycle = num("cycle"),
            "run_finished" => {
                s.outcome = text("outcome");
                s.cycle = num("cycles_completed");
                s.message = format!(
                    "{}: {}",
                    text("outcome").unwrap_or_default().to_uppercase(),
                    text("message").unwrap_or_default()
                );
            }
            _ => {}
        }
    }

    /// Starts a test case.
    ///
    /// # Errors
    /// [`StartError`] if busy, the test case/mode is invalid, or the script
    /// cannot be started.
    pub fn start(&self, test_case_id: &str, mode: &str) -> Result<RunStatus, StartError> {
        let station = &self.config.station;
        let entry = station
            .test_case(test_case_id)
            .ok_or_else(|| StartError::UnknownTestCase(test_case_id.into()))?;
        if !entry.modes.iter().any(|m| m == mode) {
            return Err(StartError::Mode {
                mode: mode.into(),
                test_case: test_case_id.into(),
                allowed: entry.modes.join(", "),
            });
        }
        let script =
            station.script_path(entry).ok_or_else(|| StartError::NoScript(test_case_id.into()))?;
        let mut i = lock(&self.inner);
        if i.status.state != RunState::Idle {
            return Err(StartError::Busy(i.status.test_case_id.clone().unwrap_or_default()));
        }
        let run_id = format!("{test_case_id}-{}", chrono::Utc::now().format("%Y%m%dT%H%M%SZ"));
        let mut cmd = Command::new(&self.config.python);
        cmd.arg(&script)
            .current_dir(script.parent().unwrap_or_else(|| std::path::Path::new(".")))
            .env("HWTEST_EVENTS", "1")
            .env("PYTHONUNBUFFERED", "1")
            .env("HWTEST_MODE", mode)
            .env("HWTEST_RUN_ID", &run_id)
            .env("HWTEST_RACK", &station.rack.config)
            .env("UUT_URL", &station.uut_url)
            .env("HWTEST_STATION", &station.id)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        if let Some(s) = &station.rack.serial {
            cmd.env("HWTEST_RACK_SERIAL", s);
        }
        if let Some(n) = &self.config.nats_url {
            cmd.env("HWTEST_NATS_URL", n);
        }
        if let Some(d) = &self.config.db_path {
            cmd.env("HWTEST_DB", d);
        }
        if self.config.emulate {
            cmd.env("HWTEST_EMULATE", "1");
        }
        for (k, v) in &station.environment {
            cmd.env(k, v);
        }
        let mut child = cmd.spawn().map_err(|e| {
            StartError::Spawn(format!("{} {}: {e}", self.config.python, script.display()))
        })?;
        i.pid = child.id();
        i.log.clear();
        i.total_lines = 0;
        i.status = RunStatus {
            state: RunState::Running,
            test_case_id: Some(test_case_id.into()),
            mode: Some(mode.into()),
            current_state: None,
            cycle: 0,
            stats: BTreeMap::new(),
            started_at: Some(chrono::Utc::now().to_rfc3339()),
            message: format!("Starting {}", script.display()),
            run_id: Some(run_id),
            outcome: None,
            log_lines: 0,
        };
        let status = i.status.clone();
        drop(i);

        let stdout = child.stdout.take();
        let stderr = child.stderr.take();
        let inner = self.inner.clone();
        tokio::spawn(async move {
            let out_inner = inner.clone();
            let out = tokio::spawn(async move {
                if let Some(o) = stdout {
                    let mut lines = BufReader::new(o).lines();
                    while let Ok(Some(line)) = lines.next_line().await {
                        match parse_event_line(&line) {
                            Some(ev) => {
                                Self::apply_event(&out_inner, &ev);
                                if ev.get("event").and_then(Value::as_str) == Some("log") {
                                    Self::push_log(
                                        &out_inner,
                                        ev.get("message")
                                            .and_then(Value::as_str)
                                            .unwrap_or_default()
                                            .to_string(),
                                    );
                                }
                            }
                            None => Self::push_log(&out_inner, line),
                        }
                    }
                }
            });
            let err_inner = inner.clone();
            let err = tokio::spawn(async move {
                if let Some(e) = stderr {
                    let mut lines = BufReader::new(e).lines();
                    while let Ok(Some(line)) = lines.next_line().await {
                        Self::push_log(&err_inner, line);
                    }
                }
            });
            let exit = child.wait().await;
            let _ = out.await;
            let _ = err.await;
            let mut i = lock(&inner);
            i.pid = None;
            let s = &mut i.status;
            s.state = RunState::Idle;
            s.current_state = None;
            if s.outcome.is_none() {
                s.outcome = Some("error".into());
                s.message = match exit {
                    Ok(code) => {
                        format!("ERROR: test script exited ({code}) without finishing; see the log")
                    }
                    Err(e) => format!("ERROR: {e}"),
                };
            }
        });
        Ok(status)
    }

    /// Requests a stop (SIGINT; killed after the grace period).
    pub fn stop(&self) -> RunStatus {
        let mut i = lock(&self.inner);
        if i.status.state == RunState::Running {
            if let Some(pid) = i.pid {
                let pid = nix::unistd::Pid::from_raw(i32::try_from(pid).unwrap_or(0));
                let _ = nix::sys::signal::kill(pid, nix::sys::signal::Signal::SIGINT);
                i.status.state = RunState::Stopping;
                i.status.message = "Stopping...".into();
                let inner = self.inner.clone();
                let timeout = self.config.stop_timeout;
                tokio::spawn(async move {
                    tokio::time::sleep(timeout).await;
                    let still = lock(&inner).status.state == RunState::Stopping;
                    if still {
                        tracing::warn!("test did not stop within {timeout:?}; killing it");
                        let _ = nix::sys::signal::kill(pid, nix::sys::signal::Signal::SIGKILL);
                    }
                });
            }
        }
        i.status.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn station(dir: &std::path::Path, script: &str) -> StationConfig {
        std::fs::write(dir.join("t.py"), script).unwrap();
        let p = dir.join("station.yaml");
        std::fs::write(&p, "station: {id: s}\nrack: {config: r, serial: '9'}\nuut: {url: 'http://uut'}\nenvironment: {EXTRA: yes-please}\ntest_cases: [{id: t, modes: [functional, hass]}]\n").unwrap();
        StationConfig::from_file(p).unwrap()
    }

    fn executor(dir: &std::path::Path, script: &str) -> Executor {
        Executor::new(RunnerConfig {
            station: station(dir, script),
            python: "python3".into(),
            nats_url: None,
            db_path: None,
            emulate: true,
            stop_timeout: Duration::from_secs(5),
        })
    }

    async fn wait_idle(e: &Executor) -> RunStatus {
        for _ in 0..200 {
            let s = e.status();
            if s.state == RunState::Idle {
                return s;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        panic!("did not finish");
    }

    const EVENTS: &str = r#"
import json, os, sys
def ev(**k): print("@@hwtest " + json.dumps(k), flush=True)
ev(event="run_started", run_id=os.environ["HWTEST_RUN_ID"], test_case="t", mode=os.environ["HWTEST_MODE"], states=["a"], rack=None)
print("serial", os.environ["HWTEST_RACK_SERIAL"], os.environ["UUT_URL"], os.environ["EXTRA"], os.environ["HWTEST_EMULATE"])
ev(event="state", state_id="a", name="Alpha", cycle=1, transition=False)
ev(event="monitor", monitor_id="m", verdict="pass", message="ok")
ev(event="cycle_completed", cycle=1)
print("oops", file=sys.stderr)
ev(event="run_finished", outcome="passed", message="1 cycle(s) completed", cycles_completed=1, passes=1, failures=0)
"#;

    #[tokio::test]
    async fn runs_script_and_tracks_events() {
        let dir = tempfile::tempdir().unwrap();
        let e = executor(dir.path(), EVENTS);
        let s = e.start("t", "hass").unwrap();
        assert_eq!(s.state, RunState::Running);
        assert!(matches!(e.start("t", "hass"), Err(StartError::Busy(_))));
        let s = wait_idle(&e).await;
        assert_eq!(s.outcome.as_deref(), Some("passed"));
        assert_eq!(s.message, "PASSED: 1 cycle(s) completed");
        assert_eq!((s.cycle, s.stats["passes"]), (1, 1));
        assert!(s.run_id.unwrap().starts_with("t-"));
        let (lines, next) = e.log_since(0);
        assert!(lines.contains(&"serial 9 http://uut yes-please 1".to_string()), "{lines:?}");
        assert!(lines.contains(&"oops".to_string()));
        assert_eq!(next, 2);
        assert_eq!(e.log_since(1).0.len(), 1);
    }

    #[tokio::test]
    async fn validation_errors() {
        let dir = tempfile::tempdir().unwrap();
        let e = executor(dir.path(), "");
        assert!(matches!(e.start("nope", "functional"), Err(StartError::UnknownTestCase(_))));
        let err = e.start("t", "halt").unwrap_err().to_string();
        assert_eq!(err, "mode \"halt\" is not enabled for \"t\" (allowed: functional, hass)");
    }

    #[tokio::test]
    async fn crash_without_finish_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let e = executor(dir.path(), "raise SystemExit(3)\n");
        e.start("t", "functional").unwrap();
        let s = wait_idle(&e).await;
        assert_eq!(s.outcome.as_deref(), Some("error"));
        assert!(s.message.contains("exited (exit status: 3) without finishing"), "{}", s.message);
    }

    #[tokio::test]
    async fn stop_interrupts_the_script() {
        let dir = tempfile::tempdir().unwrap();
        let script = "import time\ntry:\n    time.sleep(30)\nexcept KeyboardInterrupt:\n    print('@@hwtest {\"event\": \"run_finished\", \"outcome\": \"stopped\", \"message\": \"stopped by operator\", \"cycles_completed\": 0, \"passes\": 0, \"failures\": 0}', flush=True)\n";
        let e = executor(dir.path(), script);
        e.start("t", "functional").unwrap();
        tokio::time::sleep(Duration::from_millis(300)).await;
        assert_eq!(e.stop().state, RunState::Stopping);
        let s = wait_idle(&e).await;
        assert_eq!(s.outcome.as_deref(), Some("stopped"));
    }
}
