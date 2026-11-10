//! The run session (see the crate docs).

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use hwtest_core::monitor::BoundsMonitor;
use hwtest_core::{
    DataType, EnvironmentalState, MonitorResult, MonitorVerdict, StreamData, StreamField,
    StreamSchema, Timestamp,
};
use hwtest_db::{Database, RequirementSource, RunType};
use hwtest_logger::{create_logger, StreamLogger, Tags};
use hwtest_nats::{NatsConfig, NatsConnection, StatePublisher, StreamPublisher};
use hwtest_rack::Rack;
use hwtest_spec::testdef::{MonitorKind, MonitorState};
use hwtest_spec::{MonitorDef, TestDefinition};
use indexmap::IndexMap;
use serde::Serialize;
use serde_json::json;
use tokio::sync::mpsc;

use crate::events::{Event, EventSink};

/// Run error (setup problems; failures during the run are outcomes).
#[derive(Debug, thiserror::Error)]
pub enum RunError {
    /// Invalid options.
    #[error("{0}")]
    Options(String),
    /// Logger setup failed.
    #[error("logger {name:?}: {message}")]
    Logger {
        /// Logger name.
        name: String,
        /// Cause.
        message: String,
    },
    /// NATS setup failed.
    #[error("NATS: {0}")]
    Nats(String),
    /// Results database setup failed.
    #[error("results database: {0}")]
    Db(String),
    /// Invalid measurement call.
    #[error("{0}")]
    Measurement(String),
}

/// Execution mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    /// One pass through the state sequence.
    Functional,
    /// Repeat until failure or stop (screening).
    Hass,
    /// Repeat until failure or stop (life test).
    Halt,
}

impl Mode {
    /// Parses `functional` / `hass` / `halt`.
    ///
    /// # Errors
    /// [`RunError::Options`] for other text.
    pub fn parse(s: &str) -> Result<Self, RunError> {
        match s.to_ascii_lowercase().as_str() {
            "functional" => Ok(Mode::Functional),
            "hass" => Ok(Mode::Hass),
            "halt" => Ok(Mode::Halt),
            other => {
                Err(RunError::Options(format!("unknown mode {other:?} (functional, hass, halt)")))
            }
        }
    }

    /// Lower-case name.
    pub fn as_str(self) -> &'static str {
        match self {
            Mode::Functional => "functional",
            Mode::Hass => "hass",
            Mode::Halt => "halt",
        }
    }

    fn run_type(self) -> RunType {
        match self {
            Mode::Functional => RunType::Functional,
            Mode::Hass => RunType::Hass,
            Mode::Halt => RunType::Halt,
        }
    }
}

/// Options for a run.
#[derive(Clone)]
pub struct RunOptions {
    /// Mode.
    pub mode: Mode,
    /// Stop after this many complete passes (HASS/HALT).
    pub max_cycles: Option<u64>,
    /// Run id (default: UTC timestamp).
    pub run_id: Option<String>,
    /// Publish states, measurements and results to this NATS server.
    pub nats_url: Option<String>,
    /// Record the run in this results database.
    pub db_path: Option<PathBuf>,
    /// Unit type name for the results database.
    pub unit_type: String,
    /// UUT serial numbers by test-definition slot (`kwargs.N`).
    pub units: IndexMap<u32, String>,
    /// Offsite mode (skips loggers with `ignore_offsite`).
    pub offsite: bool,
    /// Start the test definition's loggers.
    pub loggers: bool,
    /// Extra run tags for loggers.
    pub tags: Tags,
    /// Progress events.
    pub events: EventSink,
}

impl Default for RunOptions {
    fn default() -> Self {
        Self {
            mode: Mode::Functional,
            max_cycles: None,
            run_id: None,
            nats_url: None,
            db_path: None,
            unit_type: "default".into(),
            units: IndexMap::new(),
            offsite: false,
            loggers: true,
            tags: Tags::new(),
            events: EventSink::None,
        }
    }
}

/// How a run ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Outcome {
    /// Completed without monitor failures.
    Passed,
    /// A monitor failed.
    Failed,
    /// Stopped by the operator (Ctrl-C / runner stop).
    Stopped,
    /// The test script or the test system raised an error.
    Error,
}

impl Outcome {
    /// Lower-case name.
    pub fn as_str(self) -> &'static str {
        match self {
            Outcome::Passed => "passed",
            Outcome::Failed => "failed",
            Outcome::Stopped => "stopped",
            Outcome::Error => "error",
        }
    }
}

/// Counters.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct RunStats {
    /// Complete passes through the state sequence.
    pub cycles_completed: u64,
    /// `measure` calls.
    pub measurements: u64,
    /// Monitor passes.
    pub passes: u64,
    /// Monitor failures.
    pub failures: u64,
    /// Monitor skips.
    pub skips: u64,
}

/// Result of a run.
#[derive(Debug, Clone, Serialize)]
pub struct RunSummary {
    /// Run id.
    pub run_id: String,
    /// Test case id.
    pub test_case: String,
    /// Mode.
    pub mode: Mode,
    /// Outcome.
    pub outcome: Outcome,
    /// Explanation.
    pub message: String,
    /// Counters.
    pub stats: RunStats,
    /// First monitor failure.
    pub first_failure: Option<MonitorResult>,
}

/// What to do next.
#[derive(Debug, Clone)]
pub enum Step {
    /// Run the test steps for this state.
    Run(MonitorState),
    /// The run is over; see [`Session::summary`].
    Done,
}

/// How a step ended.
#[derive(Debug, Clone)]
pub enum StepOutcome {
    /// Steps completed (monitor results decide pass/fail).
    Ok,
    /// Operator stop.
    Stopped,
    /// The steps raised an error.
    Error(String),
}

enum NatsMsg {
    State(EnvironmentalState, String),
    Data(StreamSchema, StreamData),
    Result(MonitorResult),
    Flush(tokio::sync::oneshot::Sender<()>),
}

struct NatsBridge {
    rt: tokio::runtime::Runtime,
    tx: mpsc::UnboundedSender<NatsMsg>,
}

impl NatsBridge {
    fn connect(url: &str) -> Result<Self, RunError> {
        let rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .enable_all()
            .build()
            .map_err(|e| RunError::Nats(e.to_string()))?;
        let (conn, mut states) = rt
            .block_on(async {
                let conn = NatsConnection::connect(NatsConfig::from_url(url)).await?;
                let states = StatePublisher::new(conn.clone()).await?;
                Ok::<_, hwtest_nats::NatsError>((conn, states))
            })
            .map_err(|e| RunError::Nats(e.to_string()))?;
        let (tx, mut rx) = mpsc::unbounded_channel::<NatsMsg>();
        rt.spawn(async move {
            let mut publishers: IndexMap<u32, StreamPublisher> = IndexMap::new();
            let results = conn.config().monitor_results_subject();
            while let Some(msg) = rx.recv().await {
                let r = match msg {
                    NatsMsg::State(s, reason) => states.set_state(s, &reason).await.map(|_| ()),
                    NatsMsg::Data(schema, data) => {
                        if !publishers.contains_key(&schema.schema_id()) {
                            match StreamPublisher::start(conn.clone(), schema.clone()).await {
                                Ok(p) => {
                                    publishers.insert(schema.schema_id(), p);
                                }
                                Err(e) => {
                                    tracing::warn!("NATS: {e}");
                                    continue;
                                }
                            }
                        }
                        publishers[&schema.schema_id()].publish(&data).await
                    }
                    NatsMsg::Result(r) => conn.publish_json(results.clone(), &r).await,
                    NatsMsg::Flush(done) => {
                        let _ = conn.client().flush().await;
                        let _ = done.send(());
                        Ok(())
                    }
                };
                if let Err(e) = r {
                    tracing::warn!("NATS: {e}");
                }
            }
        });
        Ok(Self { rt, tx })
    }

    fn send(&self, msg: NatsMsg) {
        let _ = self.tx.send(msg);
    }

    fn flush(&self) {
        let (tx, rx) = tokio::sync::oneshot::channel();
        self.send(NatsMsg::Flush(tx));
        let _ = self.rt.block_on(async { tokio::time::timeout(Duration::from_secs(5), rx).await });
    }
}

struct DbRecorder {
    db: Database,
    run: i64,
    test_case: i64,
    states: IndexMap<String, i64>,
    requirements: IndexMap<String, i64>,
    /// Test-definition slot -> unit id.
    units: IndexMap<u32, i64>,
}

impl DbRecorder {
    fn open(
        path: &std::path::Path,
        def: &TestDefinition,
        options: &RunOptions,
    ) -> hwtest_db::Result<Self> {
        let db = Database::open(path)?;
        let test_case = db.ensure_test_case(
            &options.unit_type,
            &def.test_case.id,
            Some(&def.test_case.description),
        )?;
        let run = db.create_test_run(test_case, options.mode.run_type())?;
        let mut units = IndexMap::new();
        for (slot, serial) in &options.units {
            let unit = if let Some(u) = db.get_unit_by_serial(serial)? {
                u.id
            } else {
                let ut = db.get_unit_type_by_name(&options.unit_type)?.map_or(0, |u| u.id);
                let rev = match db.get_design_revision_by_name(ut, "unspecified")? {
                    Some(r) => r.id,
                    None => db.create_design_revision(ut, "unspecified")?,
                };
                db.create_unit(serial, rev)?
            };
            // The schema numbers slots from 1; test definitions number them from 0.
            db.add_unit_to_run(run, unit, i64::from(*slot) + 1)?;
            units.insert(*slot, unit);
        }
        let existing_states =
            db.list_environmental_states(test_case)?.into_iter().map(|s| (s.name, s.id)).collect();
        let existing_reqs =
            db.list_requirements(test_case)?.into_iter().map(|r| (r.name, r.id)).collect();
        Ok(Self { db, run, test_case, states: existing_states, requirements: existing_reqs, units })
    }

    fn state_id(&mut self, name: &str) -> hwtest_db::Result<i64> {
        if let Some(id) = self.states.get(name) {
            return Ok(*id);
        }
        let id = self.db.create_environmental_state(self.test_case, name)?;
        self.states.insert(name.to_string(), id);
        Ok(id)
    }

    fn requirement_id(&mut self, name: &str) -> hwtest_db::Result<i64> {
        if let Some(id) = self.requirements.get(name) {
            return Ok(*id);
        }
        let id = self.db.create_requirement(self.test_case, name, RequirementSource::Monitor)?;
        self.requirements.insert(name.to_string(), id);
        Ok(id)
    }
}

struct LoggerEntry {
    name: String,
    topics: Vec<String>,
    logger: Box<dyn StreamLogger>,
    failed: bool,
}

/// A rack shared between the session and the test code.
pub type SharedRack = Arc<Mutex<Rack>>;

/// A test run in progress.
pub struct Session {
    def: TestDefinition,
    rack: Option<SharedRack>,
    mode: Mode,
    max_cycles: Option<u64>,
    run_id: String,
    units: IndexMap<u32, String>,
    monitors: Vec<(MonitorDef, BoundsMonitor)>,
    loggers: Vec<LoggerEntry>,
    nats: Option<NatsBridge>,
    db: Option<DbRecorder>,
    schemas: IndexMap<String, StreamSchema>,
    events: EventSink,
    stats: RunStats,
    next_index: usize,
    current: Option<EnvironmentalState>,
    step_failed: bool,
    first_failure: Option<MonitorResult>,
    finished: Option<(Outcome, String)>,
    started: bool,
    cancel: Arc<AtomicBool>,
}

fn now_ns() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| u64::try_from(d.as_nanos()).unwrap_or(u64::MAX))
}

impl Session {
    /// Prepares a run: monitors, loggers, NATS and the results database.
    ///
    /// # Errors
    /// [`RunError`] if a logger, NATS or the database cannot be set up.
    pub fn open(
        def: TestDefinition,
        rack: Option<SharedRack>,
        options: RunOptions,
    ) -> Result<Self, RunError> {
        let run_id = options
            .run_id
            .clone()
            .unwrap_or_else(|| chrono::Utc::now().format("%Y%m%dT%H%M%SZ").to_string());
        let monitors = def
            .monitors
            .values()
            .filter(|m| m.kind == MonitorKind::Bounds)
            .map(|m| (m.clone(), m.create_monitor()))
            .collect();
        let mut tags = Tags::new();
        tags.insert("test_type".into(), options.mode.as_str().into());
        tags.insert("test_case_id".into(), def.test_case.id.clone());
        tags.insert("test_run_id".into(), run_id.clone());
        if let Some(r) = &rack {
            tags.insert(
                "rack_id".into(),
                r.lock().unwrap_or_else(std::sync::PoisonError::into_inner).rack_id().to_string(),
            );
        }
        tags.extend(options.tags.clone());
        let mut loggers = Vec::new();
        if options.loggers {
            for l in def.enabled_loggers(options.offsite) {
                if let hwtest_spec::testdef::LoggerKind::Python { .. } = l.kind {
                    continue; // loaded by the Python SDK
                }
                let mut logger = create_logger(l).map_err(|e| RunError::Logger {
                    name: l.name.clone(),
                    message: e.to_string(),
                })?;
                logger.start(&tags).map_err(|e| RunError::Logger {
                    name: l.name.clone(),
                    message: e.to_string(),
                })?;
                loggers.push(LoggerEntry {
                    name: l.name.clone(),
                    topics: l.topics.clone(),
                    logger,
                    failed: false,
                });
            }
        }
        let nats = options.nats_url.as_deref().map(NatsBridge::connect).transpose()?;
        let db = options
            .db_path
            .as_deref()
            .map(|p| DbRecorder::open(p, &def, &options))
            .transpose()
            .map_err(|e| RunError::Db(e.to_string()))?;
        Ok(Self {
            def,
            rack,
            mode: options.mode,
            max_cycles: options.max_cycles,
            run_id,
            units: options.units,
            monitors,
            loggers,
            nats,
            db,
            schemas: IndexMap::new(),
            events: options.events,
            stats: RunStats::default(),
            next_index: 0,
            current: None,
            step_failed: false,
            first_failure: None,
            finished: None,
            started: false,
            cancel: Arc::new(AtomicBool::new(false)),
        })
    }

    /// The test definition.
    pub fn definition(&self) -> &TestDefinition {
        &self.def
    }

    /// The rack, if one was opened.
    pub fn rack(&self) -> Option<&SharedRack> {
        self.rack.as_ref()
    }

    /// Run id.
    pub fn run_id(&self) -> &str {
        &self.run_id
    }

    /// Mode.
    pub fn mode(&self) -> Mode {
        self.mode
    }

    /// The state being executed.
    pub fn current_state(&self) -> Option<&EnvironmentalState> {
        self.current.as_ref()
    }

    /// Completed passes so far.
    pub fn cycle(&self) -> u64 {
        self.stats.cycles_completed
    }

    /// A parameter for the current state (state override, then `case_parameters`).
    pub fn param(&self, name: &str) -> Option<&serde_json::Value> {
        self.def.parameter(name, self.current.as_ref().map(|s| s.state_id.as_str()))
    }

    /// Flag that requests a stop at the next step boundary.
    pub fn cancel_handle(&self) -> Arc<AtomicBool> {
        self.cancel.clone()
    }

    /// Emits a log event (shown by the runner).
    pub fn log(&self, level: &str, message: &str) {
        self.events.emit(Event::Log { level: level.into(), message: message.into() });
    }

    fn publish_state(&self, state: &EnvironmentalState, reason: &str) {
        if let Some(n) = &self.nats {
            n.send(NatsMsg::State(state.clone(), reason.to_string()));
        }
        self.events.emit(Event::State {
            state_id: state.state_id.clone(),
            name: state.name.clone(),
            cycle: self.stats.cycles_completed + 1,
            transition: state.is_transition,
        });
    }

    /// Advances to the next state, or ends the run.
    pub fn next_step(&mut self) -> Step {
        if self.finished.is_some() {
            return Step::Done;
        }
        let seq = self.def.state_sequence.clone();
        if !self.started {
            self.started = true;
            self.events.emit(Event::RunStarted {
                run_id: self.run_id.clone(),
                test_case: self.def.test_case.id.clone(),
                mode: self.mode.as_str().into(),
                states: seq.clone(),
                rack: self.rack.as_ref().map(|r| {
                    r.lock()
                        .unwrap_or_else(std::sync::PoisonError::into_inner)
                        .rack_id()
                        .to_string()
                }),
            });
            if seq.is_empty() {
                self.finish(
                    Outcome::Error,
                    "the test definition has an empty state_sequence".into(),
                );
                return Step::Done;
            }
        }
        if self.cancel.load(Ordering::Relaxed) {
            self.finish(Outcome::Stopped, "stopped by operator".into());
            return Step::Done;
        }
        if self.next_index == seq.len() {
            self.stats.cycles_completed += 1;
            self.events.emit(Event::CycleCompleted { cycle: self.stats.cycles_completed });
            let limit_reached = self.max_cycles.is_some_and(|m| self.stats.cycles_completed >= m);
            if self.mode == Mode::Functional || limit_reached {
                let msg =
                    format!("{} cycle(s) completed without failures", self.stats.cycles_completed);
                self.finish(Outcome::Passed, msg);
                return Step::Done;
            }
            self.next_index = 0;
        }
        let state = self.def.monitor_states[&seq[self.next_index]].clone();
        self.next_index += 1;
        let env = state.to_environmental_state();
        self.publish_state(&env, "state sequence");
        self.current = Some(env);
        self.step_failed = false;
        Step::Run(state)
    }

    /// Marks the start of a ramp into the current state (e.g. waiting for a
    /// chamber): monitors skip until [`Session::end_transition`].
    pub fn begin_transition(&mut self, description: &str) {
        let Some(cur) = self.current.clone() else { return };
        let t = EnvironmentalState {
            state_id: format!("transition_to_{}", cur.state_id),
            name: format!("Transition to {}", cur.name),
            description: description.to_string(),
            is_transition: true,
            metadata: cur.metadata.clone(),
        };
        self.publish_state(&t, description);
        self.current = Some(EnvironmentalState { is_transition: true, ..cur });
    }

    /// Ends a ramp started with [`Session::begin_transition`].
    pub fn end_transition(&mut self) {
        let Some(cur) = self.current.clone().filter(|c| c.is_transition) else { return };
        let state = EnvironmentalState { is_transition: false, ..cur };
        self.publish_state(&state, "transition complete");
        self.current = Some(state);
    }

    /// Records measurements: logs them, publishes them, and evaluates every
    /// monitor with a bound for any of the fields. Returns monitor results.
    ///
    /// The first call for a `topic` (default: the test case id) fixes its
    /// fields; later calls must use the same names.
    ///
    /// # Errors
    /// [`RunError::Measurement`] outside a state or when the fields change.
    // One linear pass: validate, evaluate, record, log, publish; splitting it
    // would only move the borrow juggling of `self` into helpers.
    #[allow(clippy::too_many_lines)]
    pub fn measure(
        &mut self,
        values: &IndexMap<String, f64>,
        topic: Option<&str>,
    ) -> Result<Vec<MonitorResult>, RunError> {
        let state = self
            .current
            .clone()
            .ok_or_else(|| RunError::Measurement("measure() called outside a test state".into()))?;
        if values.is_empty() {
            return Err(RunError::Measurement("measure() needs at least one value".into()));
        }
        let topic = topic.unwrap_or(&self.def.test_case.id).to_string();
        let schema = if let Some(s) = self.schemas.get(&topic) {
            let names: Vec<&str> = s.fields().iter().map(|f| f.name.as_str()).collect();
            if names != values.keys().map(String::as_str).collect::<Vec<_>>() {
                return Err(RunError::Measurement(format!(
                    "measure() fields for topic {topic:?} must stay the same: expected {names:?}, got {:?}",
                    values.keys().collect::<Vec<_>>()
                )));
            }
            s.clone()
        } else {
            let s = StreamSchema::new(
                topic.clone(),
                values.keys().map(|k| StreamField::new(k.clone(), DataType::F64, "")).collect(),
            );
            for l in &mut self.loggers {
                l.logger.register_schema(&topic, s.clone());
            }
            self.schemas.insert(topic.clone(), s.clone());
            s
        };
        let ts = now_ns();
        let data = StreamData {
            schema_id: schema.schema_id(),
            timestamp_ns: ts,
            period_ns: 0,
            samples: vec![values.values().map(|v| (*v).into()).collect()],
        };
        self.stats.measurements += 1;
        for l in &mut self.loggers {
            if l.failed || !(l.topics.is_empty() || l.topics.contains(&topic)) {
                continue;
            }
            if let Err(e) = l.logger.log(&topic, &data) {
                tracing::warn!("logger {}: {e} (disabled for this run)", l.name);
                l.failed = true;
            }
        }
        if let Some(n) = &self.nats {
            n.send(NatsMsg::Data(schema, data));
        }
        self.events.emit(Event::Measurement {
            state_id: state.state_id.clone(),
            topic,
            values: json!(values),
        });

        let timestamp = Timestamp::new(i64::try_from(ts).unwrap_or(i64::MAX), "local");
        let mut results = Vec::new();
        for (def, engine) in &mut self.monitors {
            if !engine.bounds().fields().iter().any(|f| values.contains_key(*f)) {
                continue;
            }
            let r = engine.evaluate(|f| values.get(f).copied(), &state, &timestamp);
            match r.verdict {
                MonitorVerdict::Pass => self.stats.passes += 1,
                MonitorVerdict::Fail => self.stats.failures += 1,
                _ => self.stats.skips += 1,
            }
            if r.verdict == MonitorVerdict::Fail {
                self.step_failed = true;
                if self.first_failure.is_none() {
                    self.first_failure = Some(r.clone());
                }
                if let (Some(db), Some(slot)) = (&mut self.db, def.slot) {
                    if let Some(unit) = db.units.get(&slot).copied() {
                        for v in &r.violations {
                            let bound = engine
                                .bounds()
                                .get(&state.state_id, &v.channel)
                                .map(|b| b.to_json().to_string())
                                .unwrap_or_default();
                            let rec = db.requirement_id(&v.channel).and_then(|req| {
                                let st = db.state_id(&state.state_id)?;
                                db.db.record_unit_failure(
                                    db.run,
                                    unit,
                                    req,
                                    st,
                                    v.value,
                                    &bound,
                                    Some(&v.message),
                                )
                            });
                            if let Err(e) = rec {
                                tracing::warn!("results database: {e}");
                            }
                        }
                    }
                }
            }
            if let Some(n) = &self.nats {
                n.send(NatsMsg::Result(r.clone()));
            }
            self.events.emit(Event::Monitor {
                monitor_id: r.monitor_id.clone(),
                verdict: format!("{:?}", r.verdict).to_lowercase(),
                message: r.message.clone(),
            });
            results.push(r);
        }
        Ok(results)
    }

    /// Reports how the current step ended.
    pub fn complete_step(&mut self, outcome: StepOutcome) {
        if self.finished.is_some() {
            return;
        }
        match outcome {
            StepOutcome::Ok if self.step_failed => {
                let msg = self
                    .first_failure
                    .as_ref()
                    .map_or_else(|| "monitor failure".to_string(), |f| f.message.clone());
                self.finish(Outcome::Failed, msg);
            }
            StepOutcome::Ok => {}
            StepOutcome::Stopped => self.finish(Outcome::Stopped, "stopped by operator".into()),
            StepOutcome::Error(msg) => {
                if let Some(db) = &self.db {
                    if let Err(e) = db.db.record_system_failure(db.run, "SCRIPT_ERROR", &msg) {
                        tracing::warn!("results database: {e}");
                    }
                }
                self.finish(Outcome::Error, msg);
            }
        }
    }

    fn finish(&mut self, outcome: Outcome, message: String) {
        if self.finished.is_some() {
            return;
        }
        for l in &mut self.loggers {
            if let Err(e) = l.logger.stop() {
                tracing::warn!("logger {}: {e}", l.name);
            }
        }
        if let Some(n) = &self.nats {
            n.flush();
        }
        if let Some(db) = &self.db {
            let r = match outcome {
                Outcome::Passed | Outcome::Failed => db.db.complete_test_run(db.run),
                Outcome::Stopped | Outcome::Error => db.db.terminate_test_run(db.run),
            };
            if let Err(e) = r {
                tracing::warn!("results database: {e}");
            }
        }
        self.events.emit(Event::RunFinished {
            outcome: outcome.as_str().into(),
            message: message.clone(),
            cycles_completed: self.stats.cycles_completed,
            passes: self.stats.passes,
            failures: self.stats.failures,
        });
        self.finished = Some((outcome, message));
    }

    /// Ends the run with an error outside a step (e.g. setup failed).
    pub fn abort(&mut self, message: &str) {
        if !self.started {
            self.started = true;
        }
        self.complete_step(StepOutcome::Error(message.to_string()));
    }

    /// Ends the run now (as stopped) if it is still in progress.
    pub fn stop(&mut self) {
        self.finish(Outcome::Stopped, "stopped by operator".into());
    }

    /// The summary (stops the run first if it is still in progress).
    pub fn summary(&mut self) -> RunSummary {
        if self.finished.is_none() {
            self.stop();
        }
        let (outcome, message) = self.finished.clone().unwrap_or((Outcome::Stopped, String::new()));
        RunSummary {
            run_id: self.run_id.clone(),
            test_case: self.def.test_case.id.clone(),
            mode: self.mode,
            outcome,
            message,
            stats: self.stats.clone(),
            first_failure: self.first_failure.clone(),
        }
    }

    /// UUT serials by slot.
    pub fn units(&self) -> &IndexMap<u32, String> {
        &self.units
    }

    /// The results database run id, when recording.
    pub fn db_run_id(&self) -> Option<i64> {
        self.db.as_ref().map(|d| d.run)
    }

    /// Runs the whole test with a Rust closure as the step body.
    pub fn run(
        &mut self,
        mut step: impl FnMut(&mut Session, &MonitorState) -> StepOutcome,
    ) -> RunSummary {
        while let Step::Run(state) = self.next_step() {
            let outcome = step(self, &state);
            self.complete_step(outcome);
        }
        self.summary()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    const DEF: &str = r#"
test_case: {id: echo}
case_parameters: {settle: 0.01}
monitor_states:
  low: {name: Low, target: 1.0}
  high: {name: High, target: 4.0, settle: 0.02}
monitors:
  echo_mon:
    kwargs.0: {}
    configuration:
      low: {v: {within_range: [1.0, 0.1]}}
      high: {v: {within_range: [4.0, 0.1]}}
loggers:
  csv: {kind: csv, kwargs: {output_dir: "OUT"}, topics: [echo]}
"#;

    fn session(
        mode: Mode,
        out: &std::path::Path,
        extra: impl FnOnce(&mut RunOptions),
    ) -> (Session, Arc<Mutex<Vec<Event>>>) {
        let def =
            TestDefinition::from_yaml_str(&DEF.replace("OUT", &out.display().to_string()), None)
                .unwrap();
        let events = Arc::new(Mutex::new(Vec::new()));
        let mut opts = RunOptions {
            mode,
            run_id: Some("r1".into()),
            events: EventSink::Memory(events.clone()),
            ..RunOptions::default()
        };
        extra(&mut opts);
        (Session::open(def, None, opts).unwrap(), events)
    }

    fn echo(gain: f64) -> impl FnMut(&mut Session, &MonitorState) -> StepOutcome {
        move |s, state| {
            let target = state.parameters["target"].as_f64().unwrap();
            assert!(s.param("settle").is_some());
            s.measure(
                &IndexMap::from([("v".to_string(), target * gain), ("target".to_string(), target)]),
                None,
            )
            .unwrap();
            StepOutcome::Ok
        }
    }

    #[test]
    fn functional_pass_logs_and_reports() {
        let out = tempfile::tempdir().unwrap();
        let (mut s, events) = session(Mode::Functional, out.path(), |_| {});
        let summary = s.run(echo(1.0));
        assert_eq!(summary.outcome, Outcome::Passed, "{}", summary.message);
        assert_eq!(
            summary.stats,
            RunStats { cycles_completed: 1, measurements: 2, passes: 2, failures: 0, skips: 0 }
        );
        let csv = std::fs::read_to_string(out.path().join("functional/echo/r1/echo.csv")).unwrap();
        assert_eq!(csv.lines().count(), 3, "header + 2 rows: {csv}");
        assert!(csv.starts_with("timestamp_ns,v,target\n"));
        let ev = events.lock().unwrap();
        assert!(matches!(&ev[0], Event::RunStarted { states, .. } if states == &["low", "high"]));
        assert!(
            matches!(ev.last().unwrap(), Event::RunFinished { outcome, .. } if outcome == "passed")
        );
        assert_eq!(ev.iter().filter(|e| matches!(e, Event::State { .. })).count(), 2);
    }

    #[test]
    fn hass_stops_at_first_failure() {
        let out = tempfile::tempdir().unwrap();
        let (mut s, _) = session(Mode::Hass, out.path(), |_| {});
        let mut calls = 0;
        let summary = s.run(|s, state| {
            calls += 1;
            let target = state.parameters["target"].as_f64().unwrap();
            let v = if calls == 5 { target + 1.0 } else { target }; // fails in cycle 3, state low
            s.measure(&IndexMap::from([("v".to_string(), v)]), None).unwrap();
            StepOutcome::Ok
        });
        assert_eq!(summary.outcome, Outcome::Failed);
        assert_eq!(summary.stats.cycles_completed, 2);
        assert!(
            summary.message.starts_with("Failed: v=2.0000 outside [0.9000, 1.1000]"),
            "{}",
            summary.message
        );
        assert_eq!(summary.first_failure.unwrap().state_id, "low");
    }

    #[test]
    fn hass_max_cycles_and_cancel() {
        let out = tempfile::tempdir().unwrap();
        let (mut s, _) = session(Mode::Halt, out.path(), |o| o.max_cycles = Some(3));
        assert_eq!(s.run(echo(1.0)).stats.cycles_completed, 3);

        let (mut s, _) = session(Mode::Hass, out.path(), |o| o.run_id = Some("r2".into()));
        let cancel = s.cancel_handle();
        let mut n = 0;
        let summary = s.run(|s, st| {
            n += 1;
            if n == 3 {
                cancel.store(true, Ordering::Relaxed);
            }
            echo(1.0)(s, st)
        });
        assert_eq!((summary.outcome, summary.stats.cycles_completed), (Outcome::Stopped, 1));
    }

    #[test]
    fn errors_and_transitions() {
        let out = tempfile::tempdir().unwrap();
        let (mut s, events) = session(Mode::Functional, out.path(), |_| {});
        let summary = s.run(|s, _| {
            s.begin_transition("ramping");
            // Monitors skip during transitions.
            let r = s.measure(&IndexMap::from([("v".to_string(), 99.0)]), None).unwrap();
            assert_eq!(r[0].verdict, MonitorVerdict::Skip);
            s.end_transition();
            StepOutcome::Error("UUT did not respond".into())
        });
        assert_eq!(
            (summary.outcome, summary.message.as_str()),
            (Outcome::Error, "UUT did not respond")
        );
        let ev = events.lock().unwrap();
        assert!(ev.iter().any(|e| matches!(e, Event::State { transition: true, state_id, .. } if state_id == "transition_to_low")));
    }

    #[test]
    fn measurement_misuse_is_explained() {
        let out = tempfile::tempdir().unwrap();
        let (mut s, _) = session(Mode::Functional, out.path(), |_| {});
        let e = s.measure(&IndexMap::from([("v".to_string(), 1.0)]), None).unwrap_err().to_string();
        assert!(e.contains("outside a test state"), "{e}");
        let Step::Run(_) = s.next_step() else { panic!() };
        s.measure(&IndexMap::from([("v".to_string(), 1.0)]), None).unwrap();
        let e = s.measure(&IndexMap::from([("w".to_string(), 1.0)]), None).unwrap_err().to_string();
        assert!(e.contains("must stay the same: expected [\"v\"]"), "{e}");
        s.measure(&IndexMap::from([("w".to_string(), 1.0)]), Some("other")).unwrap();
    }

    #[test]
    fn records_results_database() {
        let out = tempfile::tempdir().unwrap();
        let db_path = out.path().join("results.db");
        let (mut s, _) = session(Mode::Functional, out.path(), |o| {
            o.db_path = Some(db_path.clone());
            o.units = IndexMap::from([(0, "SN42".to_string())]);
        });
        let summary = s.run(echo(1.5));
        assert_eq!(summary.outcome, Outcome::Failed);
        let run = s.db_run_id().unwrap();
        let db = Database::open(&db_path).unwrap();
        let outcomes = db.get_unit_outcomes(run).unwrap();
        assert_eq!(
            (outcomes[0].slot_number, outcomes[0].outcome),
            (1, hwtest_db::TestOutcome::Fail)
        );
        let failures = db.list_unit_failures(run, None).unwrap();
        assert_eq!(failures[0].bound_description, r#"{"within_range":[1.0,0.1]}"#);
        assert_eq!(db.get_test_run(run).unwrap().unwrap().status, hwtest_db::RunStatus::Completed);
    }

    #[test]
    fn modes_parse() {
        assert_eq!(Mode::parse("HASS").unwrap(), Mode::Hass);
        assert!(Mode::parse("burn-in").unwrap_err().to_string().contains("functional, hass, halt"));
    }
}
