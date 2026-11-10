//! Station YAML for the test runner: which rack, which UUT, which test cases.
//!
//! ```yaml
//! station: {id: pi5-bench-a, description: "Pi 5 integration bench A"}
//! rack: {config: pi5_mcc_intg_a_rack, serial: "001"}
//! uut: {url: "http://192.168.68.4:8080"}
//! test_cases:
//!   - {id: voltage_echo_monitor, name: Voltage Echo Monitor, modes: [functional, hass, halt],
//!      script: tests/voltage_echo_monitor.py}     # default: <id>.py next to this file or in tests/
//! environment: {INFLUXDB_URL: "http://influx:8086"}  # optional, passed to test scripts
//! ```

use std::path::{Path, PathBuf};

use serde::Deserialize;
use serde_json::Value;
use serde_saphyr::Spanned;

use crate::error::{unknown, Issues, Result};
use crate::yaml::{loc, parse, read_file};

/// Execution modes a test case can run in.
pub const MODES: [&str; 3] = ["functional", "hass", "halt"];

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawStation {
    station: Spanned<RawStationInfo>,
    rack: Spanned<RawRackRef>,
    uut: Spanned<RawUut>,
    #[serde(default)]
    test_cases: Option<Vec<Spanned<RawTestCase>>>,
    #[serde(default)]
    environment: Option<indexmap::IndexMap<String, Value>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawStationInfo {
    id: Option<String>,
    #[serde(default)]
    description: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawRackRef {
    config: Option<String>,
    #[serde(default)]
    serial: Option<Value>,
    #[serde(default)]
    url: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawUut {
    url: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawTestCase {
    id: Option<String>,
    name: Option<String>,
    definition: Option<String>,
    #[serde(default)]
    modes: Option<Vec<Spanned<String>>>,
    #[serde(default)]
    script: Option<String>,
}

/// Rack used by a station.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RackReference {
    /// Rack class YAML name.
    pub config: String,
    /// Rack instance serial (selects calibration).
    pub serial: Option<String>,
    /// `hwtest-rack` server URL (for live rack status), if any.
    pub url: Option<String>,
}

/// A test case offered by the station.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TestCaseEntry {
    /// Test case id.
    pub id: String,
    /// Display name (defaults to the id).
    pub name: String,
    /// Test definition YAML name (defaults to the id).
    pub definition: String,
    /// Allowed modes (default `[functional]`).
    pub modes: Vec<String>,
    /// Test script path as written (relative to the station file).
    pub script: Option<String>,
}

/// A test station.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StationConfig {
    /// Station id.
    pub id: String,
    /// Description.
    pub description: String,
    /// Rack in use.
    pub rack: RackReference,
    /// UUT control URL.
    pub uut_url: String,
    /// Offered test cases.
    pub test_cases: Vec<TestCaseEntry>,
    /// Environment variables passed to test scripts.
    pub environment: indexmap::IndexMap<String, String>,
    /// Source file.
    pub source_path: Option<PathBuf>,
}

impl StationConfig {
    /// Loads and validates a station file.
    ///
    /// # Errors
    /// [`crate::SpecError`] if missing, unreadable or invalid.
    pub fn from_file(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let mut s = Self::from_yaml_str(&read_file(path)?, Some(path.to_path_buf()))?;
        s.source_path = Some(path.to_path_buf());
        Ok(s)
    }

    /// Parses and validates YAML text.
    ///
    /// # Errors
    /// [`crate::SpecError::Invalid`] listing every problem.
    pub fn from_yaml_str(text: &str, file: Option<PathBuf>) -> Result<Self> {
        let raw: RawStation = parse(text, file.as_ref())?;
        let mut issues = Issues::default();
        let mut required = |v: Option<String>, l, path: &str| {
            v.filter(|s| !s.trim().is_empty()).unwrap_or_else(|| {
                issues.push(l, path, "is required");
                String::new()
            })
        };
        let id = required(raw.station.value.id.clone(), loc(&raw.station), "station.id");
        let config = required(raw.rack.value.config.clone(), loc(&raw.rack), "rack.config");
        let uut_url = required(raw.uut.value.url.clone(), loc(&raw.uut), "uut.url");

        let mut test_cases = Vec::new();
        for (i, tc) in raw.test_cases.unwrap_or_default().into_iter().enumerate() {
            let tloc = loc(&tc);
            let path = format!("test_cases[{i}]");
            let Some(tc_id) = tc.value.id.filter(|s| !s.trim().is_empty()) else {
                issues.push(tloc, format!("{path}.id"), "is required");
                continue;
            };
            let modes = match tc.value.modes {
                None => vec!["functional".to_string()],
                Some(modes) => {
                    for m in &modes {
                        if !MODES.contains(&m.value.as_str()) {
                            issues.push(
                                loc(m),
                                format!("{path}.modes"),
                                unknown("mode", &m.value, &MODES),
                            );
                        }
                    }
                    modes.into_iter().map(|m| m.value).collect()
                }
            };
            test_cases.push(TestCaseEntry {
                name: tc.value.name.unwrap_or_else(|| tc_id.clone()),
                definition: tc.value.definition.unwrap_or_else(|| tc_id.clone()),
                id: tc_id,
                modes,
                script: tc.value.script,
            });
        }

        let text = |v: Value| match v {
            Value::String(s) => s,
            other => other.to_string(),
        };
        let serial = raw.rack.value.serial.map(text);
        let environment =
            raw.environment.unwrap_or_default().into_iter().map(|(k, v)| (k, text(v))).collect();
        issues.finish(
            file.clone(),
            StationConfig {
                id,
                description: raw.station.value.description.unwrap_or_default(),
                rack: RackReference { config, serial, url: raw.rack.value.url },
                uut_url,
                test_cases,
                environment,
                source_path: file,
            },
        )
    }

    /// Looks up a test case by id.
    pub fn test_case(&self, id: &str) -> Option<&TestCaseEntry> {
        self.test_cases.iter().find(|t| t.id == id)
    }

    /// The script for a test case: its `script:` (relative to the station
    /// file), else the first existing `<id>.py` in the station directory,
    /// `tests/`, `test_cases/` or `../tests/`.
    pub fn script_path(&self, entry: &TestCaseEntry) -> Option<PathBuf> {
        let dir = self
            .source_path
            .as_deref()
            .and_then(Path::parent)
            .map_or_else(|| PathBuf::from("."), Path::to_path_buf);
        if let Some(s) = &entry.script {
            return Some(dir.join(s));
        }
        let name = format!("{}.py", entry.id);
        [dir.clone(), dir.join("tests"), dir.join("test_cases"), dir.join("../tests")]
            .into_iter()
            .map(|d| d.join(&name))
            .find(|p| p.is_file())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repository_station_is_valid() {
        let p = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../hwtest-runner/configs/pi5_bench_a_station.yaml");
        let s = StationConfig::from_file(p).unwrap_or_else(|e| panic!("{e}"));
        assert_eq!(s.id, "pi5-bench-a");
        assert_eq!(s.rack.serial.as_deref(), Some("001"));
        let tc = s.test_case("voltage_echo_monitor").unwrap();
        assert_eq!(tc.modes, ["functional", "hass", "halt"]);
    }

    #[test]
    fn validation() {
        let yaml = "station: {}\nrack: {config: r, serial: 7}\nuut: {}\ntest_cases:\n  - {name: x}\n  - {id: a, modes: [functional, hast]}\n";
        let e = StationConfig::from_yaml_str(yaml, None).unwrap_err();
        let msgs: Vec<String> =
            e.issues().iter().map(|i| format!("{}: {}", i.path, i.message)).collect();
        assert!(msgs.contains(&"station.id: is required".to_string()), "{msgs:?}");
        assert!(msgs.contains(&"uut.url: is required".to_string()), "{msgs:?}");
        assert!(msgs.contains(&"test_cases[0].id: is required".to_string()), "{msgs:?}");
        assert!(
            msgs.iter().any(|m| m.starts_with("test_cases[1].modes: unknown mode \"hast\"")
                && m.contains("did you mean \"hass\"")),
            "{msgs:?}"
        );
    }

    #[test]
    fn scripts_and_environment() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir(dir.path().join("tests")).unwrap();
        std::fs::write(dir.path().join("tests/auto.py"), "").unwrap();
        let path = dir.path().join("station.yaml");
        std::fs::write(&path, "station: {id: s}\nrack: {config: r, url: 'http://rack:8000'}\nuut: {url: u}\nenvironment: {A: x, N: 3}\ntest_cases: [{id: auto}, {id: given, script: sub/g.py}, {id: missing}]\n").unwrap();
        let s = StationConfig::from_file(&path).unwrap();
        assert_eq!(s.script_path(&s.test_cases[0]), Some(dir.path().join("tests/auto.py")));
        assert_eq!(s.script_path(&s.test_cases[1]), Some(dir.path().join("sub/g.py")));
        assert_eq!(s.script_path(&s.test_cases[2]), None);
        assert_eq!(s.environment["N"], "3");
        assert_eq!(s.rack.url.as_deref(), Some("http://rack:8000"));
    }

    #[test]
    fn defaults() {
        let s = StationConfig::from_yaml_str("station: {id: s}\nrack: {config: r, serial: 7}\nuut: {url: u}\ntest_cases: [{id: t}]\n", None).unwrap();
        assert_eq!(s.rack.serial.as_deref(), Some("7"));
        assert_eq!(s.test_cases[0].modes, ["functional"]);
        assert_eq!(s.test_cases[0].definition, "t");
    }
}
