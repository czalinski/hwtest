//! Test definition YAML: test case info, parameters, states, monitors, loggers.
//!
//! ```yaml
//! test_case: {id: voltage_echo, name: Voltage Echo, version: "1.1.0"}
//! rack: pi5_mcc_intg_a_rack
//! case_parameters: {settling_time_seconds: 0.025}
//! monitor_states:
//!   minimum: {name: Minimum Voltage, target_voltage: 1.0}   # extra keys = state parameters
//! state_sequence: [minimum]
//! monitors:
//!   echo_monitor:
//!     kind: bounds              # default; legacy: module: hwtest_testcase / class: Monitor
//!     kwargs.0: {channel: echo} # kwargs = system monitor, kwargs.N = UUT monitor for slot N
//!     configuration:
//!       default: {echo_voltage: {special: any}}
//!       minimum: {echo_voltage: {within_range: [1.0, 0.3]}}
//! loggers:
//!   csv: {kind: csv, kwargs: {output_dir: "${CSV_LOG_DIR:./logs}"}, topics: [voltage_echo]}
//! ```
//!
//! Validation reports every problem at once with line numbers: unknown keys,
//! states referenced but not defined, malformed bounds, bad slot numbers, ...

use std::path::{Path, PathBuf};

use hwtest_core::bounds::BoundCheck;
use hwtest_core::monitor::{BoundsMonitor, StateBounds};
use hwtest_core::EnvironmentalState;
use indexmap::IndexMap;
use serde::de::{MapAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{Map, Value};
use serde_saphyr::Spanned;

use crate::env::substitute_env;
use crate::error::{unknown, Issue, Issues, Result, SpecError};
use crate::search::{definition_search_paths, find_first, require_first, yaml_names};
use crate::yaml::{json_as_yaml, loc, parse, read_file, without_locations};

type SValue = Spanned<Value>;

fn null_as_default<'de, D, T>(d: D) -> std::result::Result<T, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de> + Default,
{
    Ok(Option::<T>::deserialize(d)?.unwrap_or_default())
}

// ---------------------------------------------------------------------------
// Raw YAML shapes (with source locations)
// ---------------------------------------------------------------------------

#[derive(Deserialize, Default)]
#[serde(deny_unknown_fields)]
struct RawTestCase {
    id: Option<Spanned<String>>,
    name: Option<String>,
    version: Option<Value>,
    description: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawTestDef {
    #[serde(default)]
    test_case: Option<Spanned<RawTestCase>>,
    #[serde(default)]
    rack: Option<String>,
    #[serde(default, deserialize_with = "null_as_default")]
    case_parameters: Map<String, Value>,
    #[serde(default, deserialize_with = "null_as_default")]
    monitor_states: IndexMap<String, Spanned<Option<Map<String, Value>>>>,
    #[serde(default)]
    state_sequence: Option<Vec<Spanned<String>>>,
    #[serde(default, deserialize_with = "null_as_default")]
    monitors: IndexMap<String, Spanned<RawMonitor>>,
    #[serde(default, deserialize_with = "null_as_default")]
    loggers: IndexMap<String, Spanned<RawLogger>>,
    #[serde(default, deserialize_with = "null_as_default")]
    functional_requirements: IndexMap<String, Option<RawStateBounds>>,
}

/// `state -> field -> bound spec`, with locations on each bound.
type RawStateBounds = IndexMap<String, Option<IndexMap<String, SValue>>>;

/// Monitor entries have pattern keys (`kwargs.N`), so they are read manually.
#[derive(Default)]
struct RawMonitor {
    module: Option<Spanned<String>>,
    class: Option<Spanned<String>>,
    kind: Option<Spanned<String>>,
    kwargs: Vec<(String, SValue)>,
    configuration: RawStateBounds,
    unknown: Vec<(String, SValue)>,
}

impl<'de> Deserialize<'de> for RawMonitor {
    fn deserialize<D: Deserializer<'de>>(d: D) -> std::result::Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = RawMonitor;
            fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                f.write_str("a monitor definition (mapping)")
            }
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut map: A,
            ) -> std::result::Result<RawMonitor, A::Error> {
                let mut m = RawMonitor::default();
                while let Some(key) = map.next_key::<String>()? {
                    match key.as_str() {
                        "module" => m.module = Some(map.next_value()?),
                        "class" => m.class = Some(map.next_value()?),
                        "kind" => m.kind = Some(map.next_value()?),
                        "configuration" => {
                            m.configuration =
                                map.next_value::<Option<RawStateBounds>>()?.unwrap_or_default();
                        }
                        k if k == "kwargs" || k.starts_with("kwargs.") => {
                            m.kwargs.push((key, map.next_value()?));
                        }
                        _ => m.unknown.push((key, map.next_value()?)),
                    }
                }
                Ok(m)
            }
        }
        d.deserialize_map(V)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawLogger {
    kind: Option<Spanned<String>>,
    module: Option<Spanned<String>>,
    class: Option<Spanned<String>>,
    #[serde(default, deserialize_with = "null_as_default")]
    kwargs: Map<String, Value>,
    #[serde(default, deserialize_with = "null_as_default")]
    topics: Vec<String>,
    #[serde(default = "yes")]
    enabled: bool,
    #[serde(default)]
    ignore_offsite: bool,
}

fn yes() -> bool {
    true
}

// ---------------------------------------------------------------------------
// Validated model
// ---------------------------------------------------------------------------

/// Identification of the test case.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TestCaseInfo {
    /// Unique test case id.
    pub id: String,
    /// Human-readable name (defaults to the id).
    pub name: String,
    /// Definition version (default `"1.0.0"`).
    pub version: String,
    /// Description.
    pub description: String,
}

/// One environmental state from `monitor_states`.
#[derive(Debug, Clone, PartialEq)]
pub struct MonitorState {
    /// State id (the YAML key).
    pub id: String,
    /// Human-readable name (defaults to the id).
    pub name: String,
    /// Description.
    pub description: String,
    /// All other keys: per-state parameters and overrides of `case_parameters`.
    pub parameters: Map<String, Value>,
}

impl MonitorState {
    /// The equivalent core [`EnvironmentalState`] (parameters become metadata).
    pub fn to_environmental_state(&self) -> EnvironmentalState {
        EnvironmentalState {
            state_id: self.id.clone(),
            name: self.name.clone(),
            description: self.description.clone(),
            is_transition: false,
            metadata: self.parameters.clone(),
        }
    }
}

/// What implements a monitor.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MonitorKind {
    /// Built-in state-dependent bounds monitor (Rust).
    Bounds,
    /// A Python class (escape hatch).
    Python {
        /// Module path.
        module: String,
        /// Class name.
        class: String,
    },
}

/// A monitor from the `monitors` section.
#[derive(Debug, Clone, PartialEq)]
pub struct MonitorDef {
    /// Monitor name (the YAML key).
    pub name: String,
    /// Implementation.
    pub kind: MonitorKind,
    /// Constructor arguments (`kwargs` / `kwargs.N`).
    pub kwargs: Map<String, Value>,
    /// UUT slot for `kwargs.N` monitors; `None` for system monitors.
    pub slot: Option<u32>,
    /// Bounds per state and field.
    pub bounds: StateBounds,
}

impl MonitorDef {
    /// True for UUT monitors (`kwargs.N`): failures indicate a bad UUT.
    pub fn is_uut_monitor(&self) -> bool {
        self.slot.is_some()
    }

    /// Creates a fresh bounds monitor (with unlocked baselines).
    pub fn create_monitor(&self) -> BoundsMonitor {
        self.create_monitor_with_id(&self.name)
    }

    /// Creates a fresh bounds monitor reporting under `monitor_id`.
    pub fn create_monitor_with_id(&self, monitor_id: &str) -> BoundsMonitor {
        BoundsMonitor::new(monitor_id, self.bounds.clone())
    }
}

/// What implements a logger.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum LoggerKind {
    /// CSV files.
    Csv,
    /// `InfluxDB`.
    InfluxDb,
    /// A Python class (escape hatch).
    Python {
        /// Module path.
        module: String,
        /// Class name.
        class: String,
    },
}

impl LoggerKind {
    /// Built-in kind names accepted for `kind:`.
    pub const BUILTIN: [&'static str; 2] = ["csv", "influxdb"];
}

/// A logger from the `loggers` section.
#[derive(Debug, Clone, PartialEq)]
pub struct LoggerDef {
    /// Logger name (the YAML key).
    pub name: String,
    /// Implementation.
    pub kind: LoggerKind,
    /// Constructor arguments, unresolved (may contain `${VAR}`).
    pub kwargs: Map<String, Value>,
    /// Topics to record.
    pub topics: Vec<String>,
    /// Whether the logger is active.
    pub enabled: bool,
    /// Skip the logger in offsite mode.
    pub ignore_offsite: bool,
}

impl LoggerDef {
    /// `kwargs` with `${VAR}` / `${VAR:default}` replaced from the environment.
    ///
    /// # Errors
    /// [`SpecError::Invalid`] naming unset variables that have no default.
    pub fn resolved_kwargs(&self) -> Result<Map<String, Value>> {
        match substitute_env(&Value::Object(self.kwargs.clone())) {
            Ok(Value::Object(m)) => Ok(m),
            Ok(_) => unreachable!("substitution preserves the value shape"),
            Err(missing) => Err(SpecError::invalid(
                None,
                Issue::new(
                    None,
                    format!("loggers.{}.kwargs", self.name),
                    format!("environment variable(s) not set: {}", missing.join(", ")),
                ),
            )),
        }
    }
}

/// A complete, validated test definition.
#[derive(Debug, Clone, PartialEq)]
pub struct TestDefinition {
    /// Test case identification.
    pub test_case: TestCaseInfo,
    /// Rack class this test is designed for.
    pub rack: Option<String>,
    /// Global parameters.
    pub case_parameters: Map<String, Value>,
    /// States by id, in definition order.
    pub monitor_states: IndexMap<String, MonitorState>,
    /// Execution order of states (defaults to definition order).
    pub state_sequence: Vec<String>,
    /// Monitors by name.
    pub monitors: IndexMap<String, MonitorDef>,
    /// Loggers by name.
    pub loggers: IndexMap<String, LoggerDef>,
    /// Point-in-time checks: requirement name -> bounds per state.
    pub functional_requirements: IndexMap<String, StateBounds>,
    /// File the definition was loaded from.
    pub source_path: Option<PathBuf>,
}

impl TestDefinition {
    /// Loads and validates a definition file.
    ///
    /// # Errors
    /// [`SpecError`] if the file is missing, unreadable or invalid.
    pub fn from_file(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let text = read_file(path)?;
        let mut def = Self::from_yaml_str(&text, Some(path.to_path_buf()))?;
        def.source_path = Some(path.to_path_buf());
        Ok(def)
    }

    /// Parses and validates YAML text. `file` is used in error messages.
    ///
    /// # Errors
    /// [`SpecError::Invalid`] listing every problem found.
    pub fn from_yaml_str(text: &str, file: Option<PathBuf>) -> Result<Self> {
        let raw: RawTestDef = parse(text, file.as_ref())?;
        validate(raw, file)
    }

    /// Validates an already-parsed value (e.g. a Python dict).
    ///
    /// # Errors
    /// [`SpecError::Invalid`] listing every problem found.
    pub fn from_json(value: &Value) -> Result<Self> {
        without_locations(Self::from_yaml_str(&json_as_yaml(value), None))
    }

    /// A parameter, looking first at `state`'s overrides, then `case_parameters`.
    pub fn parameter(&self, name: &str, state: Option<&str>) -> Option<&Value> {
        state
            .and_then(|s| self.monitor_states.get(s))
            .and_then(|s| s.parameters.get(name))
            .or_else(|| self.case_parameters.get(name))
    }

    /// States in execution order.
    pub fn states_in_sequence(&self) -> Vec<&MonitorState> {
        self.state_sequence.iter().filter_map(|id| self.monitor_states.get(id)).collect()
    }

    /// Enabled loggers; in offsite mode, also excludes `ignore_offsite` loggers.
    pub fn enabled_loggers(&self, offsite: bool) -> Vec<&LoggerDef> {
        self.loggers.values().filter(|l| l.enabled && !(offsite && l.ignore_offsite)).collect()
    }
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

const MONITOR_KINDS: [&str; 1] = ["bounds"];
const MONITOR_KEYS: [&str; 5] = ["kind", "module", "class", "kwargs", "configuration"];
const LEGACY_BOUNDS_MONITOR: (&str, &str) = ("hwtest_testcase", "Monitor");

fn legacy_logger(module: &str, class: &str) -> Option<LoggerKind> {
    match (module, class) {
        ("hwtest_logger", "CsvStreamLogger") => Some(LoggerKind::Csv),
        ("hwtest_logger", "InfluxDbStreamLogger") => Some(LoggerKind::InfluxDb),
        _ => None,
    }
}

fn value_to_string(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

#[allow(clippy::too_many_lines)]
fn validate(raw: RawTestDef, file: Option<PathBuf>) -> Result<TestDefinition> {
    let mut issues = Issues::default();

    // test_case
    let (tc, tc_loc) = match raw.test_case {
        Some(s) => {
            let l = loc(&s);
            (s.value, l)
        }
        None => (RawTestCase::default(), None),
    };
    let id = match &tc.id {
        Some(id) if !id.value.trim().is_empty() => id.value.clone(),
        Some(id) => {
            issues.push(loc(id), "test_case.id", "must not be empty");
            String::new()
        }
        None => {
            issues.push(tc_loc, "test_case.id", "is required");
            String::new()
        }
    };
    let test_case = TestCaseInfo {
        name: tc.name.unwrap_or_else(|| id.clone()),
        version: tc.version.as_ref().map_or_else(|| "1.0.0".to_string(), value_to_string),
        description: tc.description.unwrap_or_default(),
        id,
    };

    // monitor_states
    let mut monitor_states = IndexMap::new();
    for (state_id, spanned) in raw.monitor_states {
        let path = format!("monitor_states.{state_id}");
        if state_id == StateBounds::DEFAULT_STATE {
            issues.push(
                loc(&spanned),
                &path,
                "\"default\" is reserved for fallback bounds and cannot be a state",
            );
        }
        let sloc = loc(&spanned);
        let mut params = spanned.value.unwrap_or_default();
        let mut text = |key: &str, issues: &mut Issues| match params.shift_remove(key) {
            None => None,
            Some(Value::String(s)) => Some(s),
            Some(other) => {
                issues.push(sloc, format!("{path}.{key}"), format!("must be text, got {other}"));
                None
            }
        };
        let name = text("name", &mut issues).unwrap_or_else(|| state_id.clone());
        let description = text("description", &mut issues).unwrap_or_default();
        monitor_states.insert(
            state_id.clone(),
            MonitorState { id: state_id, name, description, parameters: params },
        );
    }
    let state_ids: Vec<&str> = monitor_states.keys().map(String::as_str).collect();

    // state_sequence
    let state_sequence = match raw.state_sequence {
        None => monitor_states.keys().cloned().collect(),
        Some(seq) => {
            for (i, s) in seq.iter().enumerate() {
                if !monitor_states.contains_key(&s.value) {
                    issues.push(
                        loc(s),
                        format!("state_sequence[{i}]"),
                        unknown("state", &s.value, &state_ids),
                    );
                }
            }
            seq.into_iter().map(|s| s.value).collect()
        }
    };

    // monitors
    let mut monitors = IndexMap::new();
    for (name, spanned) in raw.monitors {
        let mloc = loc(&spanned);
        let m = spanned.value;
        let path = format!("monitors.{name}");
        for (key, v) in &m.unknown {
            issues.push(
                loc(v),
                format!("{path}.{key}"),
                unknown("monitor key", key, &MONITOR_KEYS),
            );
        }
        let kind = match (&m.kind, &m.module, &m.class) {
            (Some(k), None, None) => {
                if !MONITOR_KINDS.contains(&k.value.as_str()) {
                    issues.push(
                        loc(k),
                        format!("{path}.kind"),
                        unknown("monitor kind", &k.value, &MONITOR_KINDS),
                    );
                }
                MonitorKind::Bounds
            }
            (None, None, None) => MonitorKind::Bounds,
            (None, Some(module), Some(class)) => {
                if (module.value.as_str(), class.value.as_str()) == LEGACY_BOUNDS_MONITOR {
                    MonitorKind::Bounds
                } else {
                    MonitorKind::Python { module: module.value.clone(), class: class.value.clone() }
                }
            }
            (None, Some(_), None) | (None, None, Some(_)) => {
                issues.push(mloc, &path, "module and class must be given together");
                MonitorKind::Bounds
            }
            (Some(k), _, _) => {
                issues.push(loc(k), &path, "use either kind or module/class, not both");
                MonitorKind::Bounds
            }
        };

        let mut kwargs = Map::new();
        let mut slot = None;
        match m.kwargs.as_slice() {
            [] => {}
            [(key, value)] => {
                if let Some(n) = key.strip_prefix("kwargs.") {
                    match n.parse::<u32>() {
                        Ok(n) => slot = Some(n),
                        Err(_) => issues.push(
                            loc(value),
                            format!("{path}.{key}"),
                            format!(
                                "slot must be a whole number (kwargs.0, kwargs.1, ...), got {n:?}"
                            ),
                        ),
                    }
                }
                match &value.value {
                    Value::Object(o) => kwargs.clone_from(o),
                    Value::Null => {}
                    other => issues.push(
                        loc(value),
                        format!("{path}.{key}"),
                        format!("must be a mapping, got {other}"),
                    ),
                }
            }
            [_, (key, value), ..] => issues.push(
                loc(value),
                format!("{path}.{key}"),
                "only one of kwargs / kwargs.N may be given per monitor",
            ),
        }

        let bounds = state_bounds(
            &m.configuration,
            &format!("{path}.configuration"),
            &state_ids,
            &mut issues,
        );
        monitors.insert(name.clone(), MonitorDef { name, kind, kwargs, slot, bounds });
    }

    // loggers
    let mut loggers = IndexMap::new();
    for (name, spanned) in raw.loggers {
        let lloc = loc(&spanned);
        let l = spanned.value;
        let path = format!("loggers.{name}");
        let kind = match (&l.kind, &l.module, &l.class) {
            (Some(k), None, None) => match k.value.as_str() {
                "csv" => LoggerKind::Csv,
                "influxdb" => LoggerKind::InfluxDb,
                other => {
                    issues.push(
                        loc(k),
                        format!("{path}.kind"),
                        unknown("logger kind", other, &LoggerKind::BUILTIN),
                    );
                    LoggerKind::Csv
                }
            },
            (None, Some(module), Some(class)) => legacy_logger(&module.value, &class.value)
                .unwrap_or_else(|| LoggerKind::Python {
                    module: module.value.clone(),
                    class: class.value.clone(),
                }),
            (None, None, None) => {
                issues.push(lloc, &path, "logger needs a kind (csv or influxdb)");
                LoggerKind::Csv
            }
            (None, _, _) => {
                issues.push(lloc, &path, "module and class must be given together");
                LoggerKind::Csv
            }
            (Some(k), _, _) => {
                issues.push(loc(k), &path, "use either kind or module/class, not both");
                LoggerKind::Csv
            }
        };
        loggers.insert(
            name.clone(),
            LoggerDef {
                name,
                kind,
                kwargs: l.kwargs,
                topics: l.topics,
                enabled: l.enabled,
                ignore_offsite: l.ignore_offsite,
            },
        );
    }

    // functional_requirements
    let mut functional_requirements = IndexMap::new();
    for (name, states) in raw.functional_requirements {
        let path = format!("functional_requirements.{name}");
        let b = state_bounds(&states.unwrap_or_default(), &path, &state_ids, &mut issues);
        functional_requirements.insert(name, b);
    }

    issues.finish(
        file.clone(),
        TestDefinition {
            test_case,
            rack: raw.rack,
            case_parameters: raw.case_parameters,
            monitor_states,
            state_sequence,
            monitors,
            loggers,
            functional_requirements,
            source_path: file,
        },
    )
}

fn state_bounds(
    raw: &RawStateBounds,
    path: &str,
    state_ids: &[&str],
    issues: &mut Issues,
) -> StateBounds {
    let mut allowed: Vec<&str> = state_ids.to_vec();
    allowed.push(StateBounds::DEFAULT_STATE);
    let mut states = IndexMap::new();
    for (state, fields) in raw {
        let fields = fields.clone().unwrap_or_default();
        if !allowed.contains(&state.as_str()) {
            let first = fields.values().next().and_then(loc);
            issues.push(first, format!("{path}.{state}"), unknown("state", state, &allowed));
        }
        let mut parsed = IndexMap::new();
        for (field, spec) in &fields {
            match BoundCheck::from_json(&spec.value) {
                Ok(b) => {
                    parsed.insert(field.clone(), b);
                }
                Err(e) => issues.push(
                    loc(spec),
                    format!("{path}.{state}.{field}"),
                    bound_message(&e.to_string()),
                ),
            }
        }
        states.insert(state.clone(), parsed);
    }
    StateBounds::new(states)
}

/// Adds a spelling suggestion to "Unknown bound check type" errors.
fn bound_message(msg: &str) -> String {
    if let Some(name) = msg.strip_prefix("Unknown bound check type: ") {
        let name = name.trim_matches('"');
        return unknown("bound type", name, &BoundCheck::KEYS);
    }
    msg.to_string()
}

// ---------------------------------------------------------------------------
// Finding definitions
// ---------------------------------------------------------------------------

/// Finds `<test_id>.yaml`, `.yml`, `_definition.yaml` or `_definition.yml` in
/// `search_paths` (default: `.`, `./configs`, `$TEST_DEFINITION_PATH`).
pub fn find_definition_file(test_id: &str, search_paths: Option<&[PathBuf]>) -> Option<PathBuf> {
    let default;
    let dirs = if let Some(d) = search_paths {
        d
    } else {
        default = definition_search_paths();
        &default
    };
    find_first(dirs, &yaml_names(test_id, &["", "_definition"]))
}

/// Loads a definition from `path`, or finds it by `test_id`.
///
/// # Errors
/// [`SpecError::NotFound`] if it cannot be found; otherwise as
/// [`TestDefinition::from_file`].
pub fn load_definition(
    test_id: &str,
    path: Option<&Path>,
    search_paths: Option<&[PathBuf]>,
) -> Result<TestDefinition> {
    if let Some(p) = path {
        return TestDefinition::from_file(p);
    }
    let default;
    let dirs = if let Some(d) = search_paths {
        d
    } else {
        default = definition_search_paths();
        &default
    };
    let found = require_first(
        &format!("Test definition '{test_id}'"),
        dirs,
        &yaml_names(test_id, &["", "_definition"]),
        "Set TEST_DEFINITION_PATH or provide an explicit path",
    )?;
    TestDefinition::from_file(found)
}

#[cfg(test)]
mod tests {
    use super::*;
    use hwtest_core::{MonitorVerdict, Timestamp};
    use serde_json::json;

    const VALID: &str = r#"
test_case:
  id: echo
  name: Echo Test
  version: 1.1
rack: pi5_rack
case_parameters:
  settling_time_seconds: 0.025
monitor_states:
  minimum:
    name: Minimum
    target_voltage: 1.0
  maximum:
    name: Maximum
    target_voltage: 4.0
    settling_time_seconds: 0.05
state_sequence: [minimum, maximum]
monitors:
  echo_monitor:
    module: hwtest_testcase
    class: Monitor
    kwargs.0:
      channel: echo_voltage
    configuration:
      default:
        echo_voltage: {special: any}
      minimum:
        echo_voltage: {within_range: [1.0, 0.3]}
  rack_monitor:
    kind: bounds
    kwargs: {}
    configuration:
      maximum:
        temp: {less_than: 85}
loggers:
  influx:
    module: hwtest_logger
    class: InfluxDbStreamLogger
    ignore_offsite: true
    kwargs: {url: "${INFLUXDB_URL:http://localhost:8086}"}
    topics: [echo]
  csv:
    kind: csv
    topics: [echo]
  off:
    kind: csv
    enabled: false
functional_requirements:
  initial_check:
    default:
      rack_dac_voltage: {good_interval: [0.0, 0.1]}
"#;

    fn errors(yaml: &str) -> Vec<String> {
        match TestDefinition::from_yaml_str(yaml, Some("t.yaml".into())) {
            Ok(_) => vec![],
            Err(e) => e
                .issues()
                .iter()
                .map(|i| format!("{:?} {}: {}", i.location.map(|l| l.0), i.path, i.message))
                .collect(),
        }
    }

    #[test]
    fn parses_valid_definition() {
        let d = TestDefinition::from_yaml_str(VALID, None).unwrap();
        assert_eq!(d.test_case.id, "echo");
        assert_eq!(d.test_case.version, "1.1");
        assert_eq!(d.rack.as_deref(), Some("pi5_rack"));
        assert_eq!(d.state_sequence, ["minimum", "maximum"]);
        assert_eq!(d.parameter("target_voltage", Some("maximum")), Some(&json!(4.0)));
        assert_eq!(d.parameter("settling_time_seconds", Some("maximum")), Some(&json!(0.05)));
        assert_eq!(d.parameter("settling_time_seconds", Some("minimum")), Some(&json!(0.025)));
        assert_eq!(d.parameter("settling_time_seconds", None), Some(&json!(0.025)));

        let echo = &d.monitors["echo_monitor"];
        assert_eq!(echo.kind, MonitorKind::Bounds);
        assert_eq!(echo.slot, Some(0));
        assert!(echo.is_uut_monitor());
        assert_eq!(echo.kwargs["channel"], json!("echo_voltage"));
        assert!(!d.monitors["rack_monitor"].is_uut_monitor());

        assert_eq!(d.loggers["influx"].kind, LoggerKind::InfluxDb);
        assert_eq!(d.loggers["csv"].kind, LoggerKind::Csv);
        let names =
            |offsite| d.enabled_loggers(offsite).iter().map(|l| l.name.clone()).collect::<Vec<_>>();
        assert_eq!(names(false), ["influx", "csv"]);
        assert_eq!(names(true), ["csv"]);
        assert!(d.functional_requirements["initial_check"]
            .get("minimum", "rack_dac_voltage")
            .is_some());
    }

    #[test]
    fn monitor_from_definition_evaluates() {
        let d = TestDefinition::from_yaml_str(VALID, None).unwrap();
        let mut m = d.monitors["echo_monitor"].create_monitor();
        let state = d.monitor_states["minimum"].to_environmental_state();
        let r = m.evaluate(|f| (f == "echo_voltage").then_some(1.5), &state, &Timestamp::local(0));
        assert_eq!(r.verdict, MonitorVerdict::Fail);
        let state = d.monitor_states["maximum"].to_environmental_state();
        let r = m.evaluate(|f| (f == "echo_voltage").then_some(9.9), &state, &Timestamp::local(0));
        assert_eq!(r.verdict, MonitorVerdict::Skip); // default: special any
    }

    #[test]
    fn reports_all_problems_with_locations() {
        let yaml = r"
test_case: {id: t}
monitor_states:
  minimum: {}
  middle: {}
state_sequence: [minimum, midle]
monitors:
  m:
    kwargs.x: {}
    confguration: {}
    configuration:
      maximum:
        v: {within_range: [1.0]}
      middle:
        v: {within_rnage: [1.0, 0.1]}
";
        let errs = errors(yaml);
        assert_eq!(errs.len(), 6, "{errs:#?}");
        assert!(
            errs[0].contains("Some(6) state_sequence[1]: unknown state \"midle\"")
                && errs[0].contains("did you mean \"middle\"?"),
            "{}",
            errs[0]
        );
        assert!(
            errs[1].contains("monitors.m.confguration: unknown monitor key")
                && errs[1].contains("\"configuration\""),
            "{}",
            errs[1]
        );
        assert!(
            errs[2].contains("monitors.m.kwargs.x: slot must be a whole number"),
            "{}",
            errs[2]
        );
        assert!(
            errs[3].contains("Some(13) monitors.m.configuration.maximum")
                && errs[3].contains("unknown state \"maximum\""),
            "{}",
            errs[3]
        );
        assert!(errs[4].contains("Some(13) monitors.m.configuration.maximum.v: within_range: expected [center, delta], got [1.0]"), "{}", errs[4]);
        assert!(
            errs[5].contains(
                "Some(15) monitors.m.configuration.middle.v: unknown bound type \"within_rnage\""
            ) && errs[5].contains("did you mean \"within_range\""),
            "{}",
            errs[5]
        );
    }

    #[test]
    fn bad_bound_arguments_located() {
        let yaml = "test_case: {id: t}\nmonitor_states: {s: {}}\nmonitors:\n  m:\n    configuration:\n      s:\n        v: {within_range: [1.0]}\n";
        let errs = errors(yaml);
        assert_eq!(errs, vec!["Some(7) monitors.m.configuration.s.v: within_range: expected [center, delta], got [1.0]"]);
    }

    #[test]
    fn unknown_top_level_key_located() {
        let errs = errors("test_case: {id: t}\nmonitor_sates: {}\n");
        assert_eq!(errs.len(), 1);
        assert!(errs[0].starts_with("Some(2) "), "{}", errs[0]);
        assert!(errs[0].contains("monitor_sates"), "{}", errs[0]);
    }

    #[test]
    fn required_and_reserved_names() {
        let errs = errors("monitor_states: {default: {}}\n");
        assert!(errs.iter().any(|e| e.contains("test_case.id: is required")), "{errs:?}");
        assert!(errs.iter().any(|e| e.contains("\"default\" is reserved")), "{errs:?}");
    }

    #[test]
    fn kind_and_module_conflicts() {
        let errs = errors("test_case: {id: t}\nmonitors:\n  m: {kind: bounds, module: x, class: Y}\nloggers:\n  l: {topics: [a]}\n  k: {kind: parquet}\n");
        assert!(
            errs.iter().any(|e| e.contains("monitors.m: use either kind or module/class")),
            "{errs:?}"
        );
        assert!(errs.iter().any(|e| e.contains("loggers.l: logger needs a kind")), "{errs:?}");
        assert!(
            errs.iter().any(|e| e.contains("loggers.k.kind: unknown logger kind \"parquet\"")),
            "{errs:?}"
        );
    }

    #[test]
    fn python_escape_hatch_kinds() {
        let d = TestDefinition::from_yaml_str(
            "test_case: {id: t}\nmonitors:\n  m: {module: lab.mon, class: Custom}\nloggers:\n  l: {module: lab.log, class: Sink}\n",
            None,
        )
        .unwrap();
        assert_eq!(
            d.monitors["m"].kind,
            MonitorKind::Python { module: "lab.mon".into(), class: "Custom".into() }
        );
        assert_eq!(
            d.loggers["l"].kind,
            LoggerKind::Python { module: "lab.log".into(), class: "Sink".into() }
        );
    }

    #[test]
    fn empty_sections_are_allowed() {
        let d = TestDefinition::from_yaml_str(
            "test_case: {id: t}\nloggers:\nmonitors:\ncase_parameters:\n",
            None,
        )
        .unwrap();
        assert!(d.loggers.is_empty() && d.monitors.is_empty() && d.state_sequence.is_empty());
    }

    #[test]
    fn from_json_matches_yaml() {
        let v = json!({"test_case": {"id": "t"}, "monitor_states": {"a": {"x": 1}}});
        let d = TestDefinition::from_json(&v).unwrap();
        assert_eq!(d.monitor_states["a"].parameters["x"], json!(1));
        let e =
            TestDefinition::from_json(&json!({"test_case": {"id": "t"}, "state_sequence": ["x"]}))
                .unwrap_err();
        assert_eq!(e.to_string(), "state_sequence[0]: unknown state \"x\"");
    }

    #[test]
    fn logger_env_resolution() {
        let d = TestDefinition::from_yaml_str(VALID, None).unwrap();
        let kw = d.loggers["influx"].resolved_kwargs().unwrap();
        assert!(kw["url"].as_str().unwrap().starts_with("http"));
        let mut l = d.loggers["csv"].clone();
        l.kwargs.insert("token".into(), json!("${HWTEST_SURELY_UNSET_VAR}"));
        let e = l.resolved_kwargs().unwrap_err().to_string();
        assert!(
            e.contains(
                "loggers.csv.kwargs: environment variable(s) not set: HWTEST_SURELY_UNSET_VAR"
            ),
            "{e}"
        );
    }

    #[test]
    fn find_and_load() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("echo_definition.yaml"), VALID).unwrap();
        let dirs = vec![dir.path().to_path_buf()];
        assert_eq!(
            find_definition_file("echo", Some(&dirs)),
            Some(dir.path().join("echo_definition.yaml"))
        );
        let d = load_definition("echo", None, Some(&dirs)).unwrap();
        assert_eq!(d.source_path, Some(dir.path().join("echo_definition.yaml")));
        let e = load_definition("nope", None, Some(&dirs)).unwrap_err();
        assert!(matches!(e, SpecError::NotFound { .. }));
        assert!(e.to_string().contains("Set TEST_DEFINITION_PATH"));
    }

    #[test]
    fn repository_definitions_are_valid() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let d =
            TestDefinition::from_file(root.join("hwtest-intg/configs/voltage_echo_monitor.yaml"))
                .unwrap();
        assert_eq!(d.test_case.id, "voltage_echo_monitor");
        assert_eq!(d.state_sequence, ["minimum", "middle", "maximum"]);
        assert_eq!(d.monitors["echo_voltage_monitor"].slot, Some(0));
        assert_eq!(d.loggers["csv_logger"].kind, LoggerKind::Csv);
    }
}
