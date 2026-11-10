//! Rack class YAML: instruments, expected identities, logical channels.
//!
//! ```yaml
//! rack: {id: pi5-mcc-intg-a, description: "..."}
//! instruments:
//!   voltage_daq:
//!     driver: "hwtest_mcc.mcc118:create_instrument"   # or a built-in driver name
//!     identity: {manufacturer: Measurement Computing, model: MCC 118}
//!     kwargs:
//!       address: 4
//!       channels: [{id: 0, name: dut_voltage}]          # logical channel names
//! calibration: {mcc118_scale_factor: 1.0}               # optional class defaults
//! ```
//!
//! Logical channel names come from `kwargs.channels`, `kwargs.dio_channels` and
//! `kwargs.analog_channels` entries (`name` or `logical_name`) and must be
//! unique across the rack.

use std::path::{Path, PathBuf};

use indexmap::IndexMap;
use serde::Deserialize;
use serde_json::{Map, Value};
use serde_saphyr::Spanned;

use crate::error::{Issues, Result};
use crate::yaml::{json_as_yaml, loc, parse, read_file, without_locations};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawRack {
    rack: Spanned<RawRackInfo>,
    #[serde(default)]
    instruments: Option<IndexMap<String, Spanned<RawInstrument>>>,
    #[serde(default)]
    calibration: Option<IndexMap<String, Spanned<Value>>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawRackInfo {
    id: Option<String>,
    #[serde(default)]
    description: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawInstrument {
    driver: Option<Spanned<String>>,
    identity: Option<Spanned<RawIdentity>>,
    #[serde(default)]
    kwargs: Option<Map<String, Value>>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawIdentity {
    manufacturer: Option<String>,
    model: Option<String>,
}

/// How an instrument driver is loaded.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DriverRef {
    /// A driver built into the Rust core, by name (e.g. `mcc118`).
    Builtin(String),
    /// A Python factory `module:function` (escape hatch / legacy).
    Python {
        /// Module path.
        module: String,
        /// Factory function.
        function: String,
    },
}

impl DriverRef {
    /// Parses `module:function` (Python) or a bare built-in name.
    pub fn parse(s: &str) -> Option<Self> {
        match s.rsplit_once(':') {
            Some((m, f)) if !m.is_empty() && !f.is_empty() => {
                Some(DriverRef::Python { module: m.to_string(), function: f.to_string() })
            }
            None if !s.trim().is_empty() => Some(DriverRef::Builtin(s.to_string())),
            _ => None,
        }
    }
}

/// Category of a logical channel.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ChannelType {
    /// DC power supply output.
    Psu,
    /// Electronic load input.
    Load,
    /// Analog input/output.
    DaqAnalog,
    /// Digital input/output.
    DaqDigital,
}

impl ChannelType {
    /// Name used in YAML (`type:`) and in Python.
    pub fn as_str(self) -> &'static str {
        match self {
            ChannelType::Psu => "psu",
            ChannelType::Load => "load",
            ChannelType::DaqAnalog => "daq_analog",
            ChannelType::DaqDigital => "daq_digital",
        }
    }

    fn parse(s: &str) -> Option<Self> {
        match s.to_ascii_lowercase().as_str() {
            "psu" => Some(ChannelType::Psu),
            "load" => Some(ChannelType::Load),
            "daq_analog" => Some(ChannelType::DaqAnalog),
            "daq_digital" => Some(ChannelType::DaqDigital),
            _ => None,
        }
    }

    /// Infers the type from the driver name when the channel has no `type:`.
    fn infer(driver: &str, channel: &Map<String, Value>) -> Self {
        let d = driver.to_ascii_lowercase();
        if d.contains("psu") || d.contains("power") {
            ChannelType::Psu
        } else if d.contains("load") {
            ChannelType::Load
        } else if d.contains("mcc152") && channel.contains_key("direction") {
            ChannelType::DaqDigital
        } else {
            ChannelType::DaqAnalog
        }
    }
}

/// A logical channel: a test-facing name mapped to an instrument channel.
#[derive(Debug, Clone, PartialEq)]
pub struct LogicalChannel {
    /// Name used by tests (e.g. `dut_voltage`).
    pub logical_name: String,
    /// Instrument that owns the channel.
    pub instrument: String,
    /// Channel number on the instrument.
    pub channel_id: i64,
    /// Category.
    pub channel_type: ChannelType,
    /// Remaining channel keys (direction, `tc_type`, ...).
    pub metadata: Map<String, Value>,
}

/// Expected instrument identity, verified at rack start-up.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExpectedIdentity {
    /// Manufacturer.
    pub manufacturer: String,
    /// Model.
    pub model: String,
}

/// One instrument in the rack.
#[derive(Debug, Clone, PartialEq)]
pub struct InstrumentConfig {
    /// Instrument name (the YAML key).
    pub name: String,
    /// Driver reference.
    pub driver: DriverRef,
    /// Driver string as written.
    pub driver_spec: String,
    /// Expected identity.
    pub identity: ExpectedIdentity,
    /// Driver arguments.
    pub kwargs: Map<String, Value>,
    /// Logical channels defined in `kwargs`.
    pub channels: Vec<LogicalChannel>,
}

/// A rack class definition.
#[derive(Debug, Clone, PartialEq)]
pub struct RackConfig {
    /// Rack class id.
    pub rack_id: String,
    /// Description.
    pub description: String,
    /// Instruments by name, in definition order.
    pub instruments: IndexMap<String, InstrumentConfig>,
    /// Default calibration factors for the class.
    pub calibration: IndexMap<String, f64>,
    /// Source file.
    pub source_path: Option<PathBuf>,
}

impl RackConfig {
    /// Loads and validates a rack file.
    ///
    /// # Errors
    /// [`crate::SpecError`] if missing, unreadable or invalid.
    pub fn from_file(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        let mut cfg = Self::from_yaml_str(&read_file(path)?, Some(path.to_path_buf()))?;
        cfg.source_path = Some(path.to_path_buf());
        Ok(cfg)
    }

    /// Parses and validates YAML text.
    ///
    /// # Errors
    /// [`crate::SpecError::Invalid`] listing every problem.
    pub fn from_yaml_str(text: &str, file: Option<PathBuf>) -> Result<Self> {
        let raw: RawRack = parse(text, file.as_ref())?;
        validate(raw, file)
    }

    /// Validates an already-parsed value.
    ///
    /// # Errors
    /// [`crate::SpecError::Invalid`] listing every problem.
    pub fn from_json(value: &Value) -> Result<Self> {
        without_locations(Self::from_yaml_str(&json_as_yaml(value), None))
    }

    /// All logical channels across instruments.
    pub fn channels(&self) -> impl Iterator<Item = &LogicalChannel> {
        self.instruments.values().flat_map(|i| i.channels.iter())
    }

    /// Looks up a logical channel by name.
    pub fn channel(&self, logical_name: &str) -> Option<&LogicalChannel> {
        self.channels().find(|c| c.logical_name == logical_name)
    }
}

fn validate(raw: RawRack, file: Option<PathBuf>) -> Result<RackConfig> {
    let mut issues = Issues::default();
    let rack_loc = loc(&raw.rack);
    let rack_id = match raw.rack.value.id {
        Some(id) if !id.trim().is_empty() => id,
        _ => {
            issues.push(rack_loc, "rack.id", "is required");
            String::new()
        }
    };

    let mut instruments = IndexMap::new();
    let mut seen: IndexMap<String, String> = IndexMap::new();
    for (name, spanned) in raw.instruments.unwrap_or_default() {
        let iloc = loc(&spanned);
        let inst = spanned.value;
        let path = format!("instruments.{name}");
        let (driver, driver_spec) = if let Some(d) = &inst.driver {
            if let Some(r) = DriverRef::parse(&d.value) {
                (r, d.value.clone())
            } else {
                issues.push(
                    loc(d),
                    format!("{path}.driver"),
                    "must be a built-in driver name or \"module:function\"",
                );
                (DriverRef::Builtin(String::new()), d.value.clone())
            }
        } else {
            issues.push(iloc, format!("{path}.driver"), "is required");
            (DriverRef::Builtin(String::new()), String::new())
        };
        let identity = if let Some(id) = inst.identity {
            let identity_loc = loc(&id);
            let mut field = |v: Option<String>, key: &str| {
                v.filter(|s| !s.trim().is_empty()).unwrap_or_else(|| {
                    issues.push(identity_loc, format!("{path}.identity.{key}"), "is required");
                    String::new()
                })
            };
            let manufacturer = field(id.value.manufacturer, "manufacturer");
            let model = field(id.value.model, "model");
            ExpectedIdentity { manufacturer, model }
        } else {
            issues.push(iloc, format!("{path}.identity"), "is required (manufacturer and model)");
            ExpectedIdentity { manufacturer: String::new(), model: String::new() }
        };
        let kwargs = inst.kwargs.unwrap_or_default();
        let channels = parse_channels(&name, &driver_spec, &kwargs, &path, iloc, &mut issues);
        for ch in &channels {
            if let Some(owner) = seen.insert(ch.logical_name.clone(), name.clone()) {
                issues.push(
                    iloc,
                    format!("{path}.kwargs"),
                    format!(
                        "logical channel name {:?} is already used by instrument {owner:?}",
                        ch.logical_name
                    ),
                );
            }
        }
        instruments.insert(
            name.clone(),
            InstrumentConfig { name, driver, driver_spec, identity, kwargs, channels },
        );
    }

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
        RackConfig {
            rack_id,
            description: raw.rack.value.description.unwrap_or_default(),
            instruments,
            calibration,
            source_path: file,
        },
    )
}

fn parse_channels(
    instrument: &str,
    driver: &str,
    kwargs: &Map<String, Value>,
    path: &str,
    iloc: Option<(u64, u64)>,
    issues: &mut Issues,
) -> Vec<LogicalChannel> {
    let mut out = Vec::new();
    for (list_key, fixed_type) in [
        ("channels", None),
        ("dio_channels", Some(ChannelType::DaqDigital)),
        ("analog_channels", Some(ChannelType::DaqAnalog)),
    ] {
        let Some(list) = kwargs.get(list_key) else { continue };
        let Some(items) = list.as_array() else {
            issues.push(iloc, format!("{path}.kwargs.{list_key}"), "must be a list");
            continue;
        };
        for (i, item) in items.iter().enumerate() {
            let ipath = format!("{path}.kwargs.{list_key}[{i}]");
            let Some(ch) = item.as_object() else {
                issues.push(iloc, ipath, "must be a mapping with id and name");
                continue;
            };
            let name = ch.get("logical_name").or_else(|| ch.get("name")).and_then(Value::as_str);
            let id = ch.get("id").and_then(Value::as_i64);
            let (Some(name), Some(id)) = (name, id) else {
                issues.push(iloc, ipath, "needs an integer id and a name");
                continue;
            };
            let channel_type = match (fixed_type, ch.get("type").and_then(Value::as_str)) {
                (Some(t), _) => t,
                (None, Some(t)) => ChannelType::parse(t).unwrap_or_else(|| {
                    issues.push(
                        iloc,
                        format!("{ipath}.type"),
                        format!("unknown channel type {t:?} (psu, load, daq_analog, daq_digital)"),
                    );
                    ChannelType::DaqAnalog
                }),
                (None, None) => ChannelType::infer(driver, ch),
            };
            let metadata = ch
                .iter()
                .filter(|(k, _)| !matches!(k.as_str(), "id" | "name" | "logical_name" | "type"))
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
            out.push(LogicalChannel {
                logical_name: name.to_string(),
                instrument: instrument.to_string(),
                channel_id: id,
                channel_type,
                metadata,
            });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn repo(p: &str) -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").join(p)
    }

    #[test]
    fn repository_racks_are_valid() {
        for f in [
            "configs/pi5_mcc_intg_a_rack.yaml",
            "configs/orange_pi_5_rack.yaml",
            "hwtest-intg/src/hwtest_intg/configs/pi5_mcc_intg_a_rack.yaml",
        ] {
            let r = RackConfig::from_file(repo(f)).unwrap_or_else(|e| panic!("{e}"));
            assert!(!r.instruments.is_empty());
        }
        let r = RackConfig::from_file(repo("configs/pi5_mcc_intg_a_rack.yaml")).unwrap();
        assert_eq!(r.rack_id, "pi5-mcc-intg-a");
        let daq = &r.instruments["voltage_daq"];
        assert_eq!(
            daq.driver,
            DriverRef::Python {
                module: "hwtest_mcc.mcc118".into(),
                function: "create_instrument".into()
            }
        );
        assert_eq!(daq.identity.model, "MCC 118");
        let relay = r.channel("relay_dut_power").unwrap();
        assert_eq!((relay.instrument.as_str(), relay.channel_id), ("dio_controller", 0));
        assert_eq!(relay.channel_type, ChannelType::DaqDigital);
        assert_eq!(r.channel("control_voltage").unwrap().channel_type, ChannelType::DaqAnalog);
        assert_eq!(r.channel("tc_0").unwrap().metadata["tc_type"], "TYPE_K");
    }

    #[test]
    fn reports_missing_and_duplicate() {
        let yaml = "rack: {description: x}\ninstruments:\n  a:\n    driver: \"bad:\"\n    identity: {manufacturer: M}\n    kwargs: {channels: [{id: 0, name: v}]}\n  b:\n    kwargs: {channels: [{id: 1, name: v}, {name: w}]}\ncalibration: {k: high}\n";
        let e = RackConfig::from_yaml_str(yaml, None).unwrap_err();
        let msgs: Vec<String> =
            e.issues().iter().map(|i| format!("{}: {}", i.path, i.message)).collect();
        for expected in [
            "rack.id: is required",
            "instruments.a.driver: must be a built-in driver name",
            "instruments.a.identity.model: is required",
            "instruments.b.driver: is required",
            "instruments.b.identity: is required",
            "instruments.b.kwargs.channels[1]: needs an integer id and a name",
            "instruments.b.kwargs: logical channel name \"v\" is already used by instrument \"a\"",
            "calibration.k: must be a number",
        ] {
            assert!(
                msgs.iter().any(|m| m.starts_with(expected)),
                "missing {expected:?} in {msgs:#?}"
            );
        }
    }

    #[test]
    fn driver_refs() {
        assert_eq!(DriverRef::parse("mcc118"), Some(DriverRef::Builtin("mcc118".into())));
        assert!(matches!(DriverRef::parse("a.b:c"), Some(DriverRef::Python { .. })));
        assert_eq!(DriverRef::parse(":x"), None);
        assert_eq!(DriverRef::parse(" "), None);
    }
}
