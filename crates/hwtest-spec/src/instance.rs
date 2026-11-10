//! Rack instance YAML: per-serial-number calibration for a rack class.
//!
//! Stored as `<rack_class>_<serial>.yaml` in `$HWTEST_RACK_INSTANCE_PATH`,
//! `~/.config/hwtest/racks` or `/etc/hwtest/racks`:
//!
//! ```yaml
//! rack_instance: {serial_number: "001", rack_class: pi5_mcc_intg_a_rack, description: ""}
//! calibration: {uut_adc_scale_factor: 1.0012, mcc118_scale_factor: 0.9987}
//! calibration_metadata: {calibrated_at: "2026-01-02T03:04:05Z", calibrated_by: jdoe,
//!                        reference_instrument: "Fluke 87V", notes: ""}
//! ```

use std::path::{Path, PathBuf};

use indexmap::IndexMap;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use serde_saphyr::Spanned;

use crate::error::{Issues, Result, SpecError};
use crate::search::{rack_instance_search_paths, yaml_names};
use crate::yaml::{loc, parse, read_file};

/// Identification of a physical rack.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RackInstanceInfo {
    /// Serial number.
    #[serde(deserialize_with = "string_or_number")]
    pub serial_number: String,
    /// Rack class (rack YAML name).
    pub rack_class: String,
    /// Description.
    #[serde(default)]
    pub description: String,
}

/// Who calibrated the rack and how.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CalibrationMetadata {
    /// ISO-8601 time of calibration.
    #[serde(default)]
    pub calibrated_at: String,
    /// Person or process.
    #[serde(default)]
    pub calibrated_by: String,
    /// Reference instrument used.
    #[serde(default)]
    pub reference_instrument: String,
    /// Free-form notes.
    #[serde(default)]
    pub notes: String,
}

fn string_or_number<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> std::result::Result<String, D::Error> {
    Ok(match Value::deserialize(d)? {
        Value::String(s) => s,
        other => other.to_string(),
    })
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawInstance {
    rack_instance: RackInstanceInfo,
    #[serde(default)]
    calibration: Option<IndexMap<String, Spanned<Value>>>,
    #[serde(default)]
    calibration_metadata: Option<CalibrationMetadata>,
}

#[derive(Serialize)]
struct OutInstance<'a> {
    rack_instance: &'a RackInstanceInfo,
    calibration: &'a IndexMap<String, f64>,
    calibration_metadata: &'a CalibrationMetadata,
}

/// Calibration data for one physical rack.
#[derive(Debug, Clone, PartialEq)]
pub struct RackInstance {
    /// Identification.
    pub instance: RackInstanceInfo,
    /// Calibration factors by name.
    pub calibration: IndexMap<String, f64>,
    /// Calibration provenance.
    pub metadata: CalibrationMetadata,
    /// File it was loaded from / saved to.
    pub source_path: Option<PathBuf>,
}

impl RackInstance {
    /// A new, uncalibrated instance (all factors 1.0).
    pub fn new(
        serial_number: &str,
        rack_class: &str,
        description: &str,
        calibrated_at: &str,
    ) -> Self {
        Self {
            instance: RackInstanceInfo {
                serial_number: serial_number.into(),
                rack_class: rack_class.into(),
                description: description.into(),
            },
            calibration: IndexMap::from([
                ("uut_adc_scale_factor".to_string(), 1.0),
                ("mcc118_scale_factor".to_string(), 1.0),
            ]),
            metadata: CalibrationMetadata {
                calibrated_at: calibrated_at.into(),
                calibrated_by: "manual".into(),
                reference_instrument: String::new(),
                notes: "Default calibration - not yet calibrated".into(),
            },
            source_path: None,
        }
    }

    /// A calibration factor, or `default` if not set.
    pub fn factor(&self, name: &str, default: f64) -> f64 {
        self.calibration.get(name).copied().unwrap_or(default)
    }

    /// Loads an instance file.
    ///
    /// # Errors
    /// [`SpecError`] if missing, unreadable or invalid.
    pub fn from_file(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let file = Some(path.to_path_buf());
        let raw: RawInstance = parse(&read_file(path)?, file.as_ref())?;
        let mut issues = Issues::default();
        let mut calibration = IndexMap::new();
        for (k, v) in raw.calibration.unwrap_or_default() {
            match v.value.as_f64() {
                Some(f) => {
                    calibration.insert(k, f);
                }
                None => issues.push(
                    loc(&v),
                    format!("calibration.{k}"),
                    format!("must be a number, got {}", v.value),
                ),
            }
        }
        issues.finish(
            file.clone(),
            Self {
                instance: raw.rack_instance,
                calibration,
                metadata: raw.calibration_metadata.unwrap_or_default(),
                source_path: file,
            },
        )
    }

    /// Serializes to YAML.
    ///
    /// # Panics
    /// Never: the document contains only strings and finite numbers.
    pub fn to_yaml(&self) -> String {
        serde_saphyr::to_string(&OutInstance {
            rack_instance: &self.instance,
            calibration: &self.calibration,
            calibration_metadata: &self.metadata,
        })
        .expect("instance documents always serialize")
    }

    /// Writes the instance to `path`, or to its source path, or to
    /// `~/.config/hwtest/racks/<class>_<serial>.yaml`. Returns the path written.
    ///
    /// # Errors
    /// [`SpecError::Io`] if the file cannot be written.
    pub fn save(&mut self, path: Option<&Path>) -> Result<PathBuf> {
        let target = match (path, &self.source_path) {
            (Some(p), _) => p.to_path_buf(),
            (None, Some(p)) => p.clone(),
            (None, None) => {
                let home =
                    std::env::var_os("HOME").map_or_else(|| PathBuf::from("."), PathBuf::from);
                home.join(".config/hwtest/racks").join(format!(
                    "{}_{}.yaml",
                    self.instance.rack_class, self.instance.serial_number
                ))
            }
        };
        let io = |e: std::io::Error| SpecError::Io { path: target.clone(), message: e.to_string() };
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent).map_err(io)?;
        }
        std::fs::write(&target, self.to_yaml()).map_err(io)?;
        self.source_path = Some(target.clone());
        Ok(target)
    }
}

fn is_yaml(p: &Path) -> bool {
    p.extension().is_some_and(|e| e.eq_ignore_ascii_case("yaml") || e.eq_ignore_ascii_case("yml"))
}

/// Finds `<rack_class>_<serial>.yaml`, or the first `<rack_class>_*.yaml`
/// (sorted) when `serial` is `None`.
pub fn find_instance_file(
    rack_class: &str,
    serial: Option<&str>,
    search_paths: Option<&[PathBuf]>,
) -> Option<PathBuf> {
    let default;
    let dirs = if let Some(d) = search_paths {
        d
    } else {
        default = rack_instance_search_paths();
        &default
    };
    let prefix = format!("{rack_class}_");
    for dir in dirs.iter().filter(|d| d.is_dir()) {
        if let Some(serial) = serial {
            if let Some(p) = yaml_names(&format!("{prefix}{serial}"), &[""])
                .iter()
                .map(|n| dir.join(n))
                .find(|p| p.is_file())
            {
                return Some(p);
            }
        } else if let Ok(entries) = std::fs::read_dir(dir) {
            let mut matches: Vec<PathBuf> = entries
                .filter_map(|e| e.ok().map(|e| e.path()))
                .filter(|p| {
                    p.is_file()
                        && p.file_name()
                            .and_then(|n| n.to_str())
                            .is_some_and(|n| n.starts_with(&prefix) && is_yaml(p))
                })
                .collect();
            matches.sort();
            if let Some(first) = matches.into_iter().next() {
                return Some(first);
            }
        }
    }
    None
}

/// Loads the instance for `rack_class` / `serial` (or from `path`).
///
/// # Errors
/// [`SpecError::NotFound`] if no file matches; otherwise as
/// [`RackInstance::from_file`].
pub fn load_instance(
    rack_class: &str,
    serial: Option<&str>,
    path: Option<&Path>,
    search_paths: Option<&[PathBuf]>,
) -> Result<RackInstance> {
    if let Some(p) = path {
        return RackInstance::from_file(p);
    }
    let found = find_instance_file(rack_class, serial, search_paths).ok_or_else(|| {
        SpecError::NotFound {
            what: format!(
                "Rack instance config for class '{rack_class}'{}",
                serial.map(|s| format!(" serial '{s}'")).unwrap_or_default()
            ),
            searched: search_paths.map_or_else(rack_instance_search_paths, <[PathBuf]>::to_vec),
            hint: "Set HWTEST_RACK_INSTANCE_PATH or create one in ~/.config/hwtest/racks/".into(),
        }
    })?;
    RackInstance::from_file(found)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn save_load_round_trip() {
        let dir = tempfile::tempdir().unwrap();
        let mut inst = RackInstance::new("001", "pi5_rack", "bench A", "2026-11-09T00:00:00Z");
        inst.calibration.insert("uut_adc_scale_factor".into(), 1.0012);
        let path = inst.save(Some(&dir.path().join("pi5_rack_001.yaml"))).unwrap();
        let back = RackInstance::from_file(&path).unwrap();
        assert_eq!(back, inst);
        assert!((back.factor("uut_adc_scale_factor", 1.0) - 1.0012).abs() < 1e-12);
        assert!((back.factor("missing", 0.5) - 0.5).abs() < f64::EPSILON);
    }

    #[test]
    fn numeric_serial_and_find() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("rk_7.yaml"),
            "rack_instance: {serial_number: 7, rack_class: rk}\ncalibration: {a: 2}\n",
        )
        .unwrap();
        std::fs::write(
            dir.path().join("rk_3.yml"),
            "rack_instance: {serial_number: '3', rack_class: rk}\n",
        )
        .unwrap();
        let dirs = vec![dir.path().to_path_buf()];
        assert_eq!(find_instance_file("rk", None, Some(&dirs)), Some(dir.path().join("rk_3.yml")));
        let i = load_instance("rk", Some("7"), None, Some(&dirs)).unwrap();
        assert_eq!(i.instance.serial_number, "7");
        assert_eq!(i.calibration["a"], 2.0);
        let e = load_instance("rk", Some("9"), None, Some(&dirs)).unwrap_err();
        assert!(e.to_string().contains("serial '9' not found"), "{e}");
    }

    #[test]
    fn bad_factor_is_reported() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("x.yaml");
        std::fs::write(
            &p,
            "rack_instance: {serial_number: a, rack_class: b}\ncalibration:\n  gain: fast\n",
        )
        .unwrap();
        let e = RackInstance::from_file(&p).unwrap_err();
        assert!(
            e.to_string().ends_with(":3:9: calibration.gain: must be a number, got \"fast\""),
            "{e}"
        );
    }
}
