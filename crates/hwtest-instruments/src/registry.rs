//! Creating instruments from rack YAML (`driver:` + `kwargs:`).
//!
//! Built-in drivers can be named directly or by their legacy Python factory
//! path (existing rack files keep working):
//!
//! | `driver:` | legacy name |
//! |-----------|-------------|
//! | `mcc118` | `hwtest_mcc.mcc118:create_instrument` |
//! | `mcc134` | `hwtest_mcc.mcc134:create_instrument` |
//! | `mcc152` | `hwtest_mcc.mcc152:create_instrument` |
//! | `bk_psu` | `hwtest_bkprecision.psu:create_instrument`, `...psu_channel:create_multichannel_instrument` |
//! | `waveshare_adda` | `hwtest_waveshare.high_precision_ad_da:create_instrument` |
//!
//! Other `module:function` drivers are Python escape-hatch drivers, which
//! the Python SDK loads itself.
//!
//! In emulation mode every built-in driver runs against its emulator, so a
//! complete rack can be exercised on a development machine.

use std::collections::HashMap;
use std::sync::Arc;

use hwtest_spec::rack::DriverRef;
use serde::de::DeserializeOwned;
use serde_json::{Map, Value};

use crate::bk::emulator::BkPsuEmulator;
use crate::bk::{BkPsu, BkPsuConfig};
use crate::daqhats::emulator::EmulatedDaqHats;
use crate::daqhats::{ids, mcc118, mcc134, mcc152, system_backend, Backend};
use crate::error::{InstrumentError, Result};
use crate::scpi::ScpiConnection;
use crate::traits::Instrument;
use crate::waveshare::adda::{self, AdDa};
use crate::waveshare::emulator::{EmulatedAdDa, Pins};

/// Built-in driver names.
pub const BUILTIN: [&str; 5] = ["mcc118", "mcc134", "mcc152", "bk_psu", "waveshare_adda"];

/// The built-in driver name for a driver reference, if any.
pub fn builtin_name(driver: &DriverRef) -> Option<&'static str> {
    let name = match driver {
        DriverRef::Builtin(n) => n.as_str(),
        DriverRef::Python { module, .. } => match module.as_str() {
            "hwtest_mcc.mcc118" => "mcc118",
            "hwtest_mcc.mcc134" => "mcc134",
            "hwtest_mcc.mcc152" => "mcc152",
            "hwtest_bkprecision.psu" | "hwtest_bkprecision.psu_channel" | "hwtest_bkprecision" => {
                "bk_psu"
            }
            "hwtest_waveshare.high_precision_ad_da" | "hwtest_waveshare" => "waveshare_adda",
            _ => return None,
        },
    };
    BUILTIN.iter().copied().find(|b| *b == name)
}

/// Shared resources for opening instruments (one per rack).
pub struct DriverEnv {
    emulate: bool,
    daqhats: Option<Backend>,
    emulated_hats: Option<Arc<EmulatedDaqHats>>,
    /// Emulated AD/DA boards by instrument name.
    pub emulated_adda: HashMap<String, EmulatedAdDa>,
    /// Emulated PSUs by instrument name.
    pub emulated_psu: HashMap<String, BkPsuEmulator>,
}

impl DriverEnv {
    /// Real hardware.
    pub fn hardware() -> Self {
        Self {
            emulate: false,
            daqhats: None,
            emulated_hats: None,
            emulated_adda: HashMap::new(),
            emulated_psu: HashMap::new(),
        }
    }

    /// Everything emulated.
    pub fn emulated() -> Self {
        let hats = Arc::new(EmulatedDaqHats::new());
        Self {
            emulate: true,
            daqhats: Some(hats.clone()),
            emulated_hats: Some(hats),
            ..Self::hardware()
        }
    }

    /// Hardware unless `HWTEST_EMULATE` is set to `1`/`true`.
    pub fn from_env() -> Self {
        match std::env::var("HWTEST_EMULATE").map(|v| v.to_ascii_lowercase()) {
            Ok(v) if v == "1" || v == "true" || v == "yes" => Self::emulated(),
            _ => Self::hardware(),
        }
    }

    /// True in emulation mode.
    pub fn is_emulated(&self) -> bool {
        self.emulate
    }

    /// The emulated HAT stack (emulation mode only).
    pub fn emulated_hats(&self) -> Option<&Arc<EmulatedDaqHats>> {
        self.emulated_hats.as_ref()
    }

    fn hats(&mut self, address: i64, id: u16) -> Result<Backend> {
        if let (Some(e), Ok(a)) = (&self.emulated_hats, u8::try_from(address)) {
            if !e.list()?.iter().any(|h| h.address == a) {
                e.add_board(a, id);
            }
        }
        if self.daqhats.is_none() {
            self.daqhats = Some(system_backend()?);
        }
        Ok(self.daqhats.clone().expect("set above"))
    }
}

use crate::daqhats::DaqHats as _;

fn kwargs<T: DeserializeOwned>(name: &str, kwargs: &Map<String, Value>) -> Result<T> {
    serde_json::from_value(Value::Object(kwargs.clone()))
        .map_err(|e| InstrumentError::Config(format!("instruments.{name}.kwargs: {e}")))
}

/// Opens instrument `name`.
///
/// # Errors
/// [`InstrumentError::Config`] for unknown drivers or invalid kwargs, or any
/// error from opening the device.
pub fn open_instrument(
    name: &str,
    driver: &DriverRef,
    args: &Map<String, Value>,
    env: &mut DriverEnv,
) -> Result<Box<dyn Instrument>> {
    let Some(builtin) = builtin_name(driver) else {
        return match driver {
            DriverRef::Python { module, function } => Err(InstrumentError::Unsupported {
                instrument: format!("{name} ({module}:{function})"),
                capability: "loading from Rust (Python driver; load it from the Python SDK)",
            }),
            DriverRef::Builtin(n) => Err(InstrumentError::Config(format!(
                "instruments.{name}.driver: unknown driver {n:?} (built-in: {})",
                BUILTIN.join(", ")
            ))),
        };
    };
    match builtin {
        "mcc118" => {
            let cfg: mcc118::Config = kwargs(name, args)?;
            let hats = env.hats(cfg.address, ids::MCC_118)?;
            Ok(Box::new(mcc118::Mcc118::open(hats, cfg, name)?))
        }
        "mcc134" => {
            let cfg: mcc134::Config = kwargs(name, args)?;
            let hats = env.hats(cfg.address, ids::MCC_134)?;
            Ok(Box::new(mcc134::Mcc134::open(hats, cfg, name)?))
        }
        "mcc152" => {
            let cfg: mcc152::Config = kwargs(name, args)?;
            let hats = env.hats(cfg.address, ids::MCC_152)?;
            Ok(Box::new(mcc152::Mcc152::open(hats, cfg)?))
        }
        "bk_psu" => {
            let cfg: BkPsuConfig = kwargs(name, args)?;
            if env.emulate || cfg.visa_address.starts_with("emulator:") {
                let emu = if cfg.channels.len() > 1 || cfg.visa_address.contains("9130") {
                    BkPsuEmulator::model_9130b("EMU0001")
                } else {
                    BkPsuEmulator::model_9115("EMU0001")
                };
                env.emulated_psu.insert(name.to_string(), emu.clone());
                let mut channels = cfg.channels;
                if channels.is_empty() {
                    channels.push(crate::bk::PsuChannelConfig {
                        id: 1,
                        logical_name: "output".into(),
                        max_voltage: None,
                        max_current: None,
                    });
                }
                return Ok(Box::new(BkPsu::with_connection(
                    ScpiConnection::new(Box::new(emu), true),
                    channels,
                )?));
            }
            Ok(Box::new(BkPsu::open(cfg)?))
        }
        "waveshare_adda" => {
            let cfg: adda::Config = kwargs(name, args)?;
            if env.emulate {
                let pins = Pins {
                    adc_cs: cfg.adc_cs_pin,
                    drdy: cfg.adc_drdy_pin,
                    dac_cs: cfg.dac_cs_pin,
                    adc_vref: cfg.adc_vref,
                    dac_vref: cfg.dac_vref,
                };
                let (emu, bus) = EmulatedAdDa::new(pins);
                env.emulated_adda.insert(name.to_string(), emu);
                return Ok(Box::new(AdDa::with_bus(bus, cfg, name)?));
            }
            Ok(Box::new(AdDa::open(cfg, name)?))
        }
        _ => unreachable!("BUILTIN covers all names"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::traits::ChannelRef;
    use hwtest_spec::RackConfig;

    #[test]
    fn legacy_names_map_to_builtins() {
        assert_eq!(
            builtin_name(&DriverRef::parse("hwtest_mcc.mcc118:create_instrument").unwrap()),
            Some("mcc118")
        );
        assert_eq!(
            builtin_name(
                &DriverRef::parse("hwtest_bkprecision.psu_channel:create_multichannel_instrument")
                    .unwrap()
            ),
            Some("bk_psu")
        );
        assert_eq!(builtin_name(&DriverRef::parse("mcc152").unwrap()), Some("mcc152"));
        assert_eq!(builtin_name(&DriverRef::parse("lab.custom:make").unwrap()), None);
        assert_eq!(builtin_name(&DriverRef::parse("mcc999").unwrap()), None);
    }

    #[test]
    fn whole_repository_rack_opens_emulated() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let rack = RackConfig::from_file(root.join("configs/pi5_mcc_intg_a_rack.yaml")).unwrap();
        let mut env = DriverEnv::emulated();
        for (name, inst) in &rack.instruments {
            let mut i = open_instrument(name, &inst.driver, &inst.kwargs, &mut env)
                .unwrap_or_else(|e| panic!("{name}: {e}"));
            let id = i.identity().unwrap();
            assert_eq!(
                (id.manufacturer.as_str(), id.model.as_str()),
                (inst.identity.manufacturer.as_str(), inst.identity.model.as_str())
            );
        }
        assert_eq!(env.emulated_hats().unwrap().list().unwrap().len(), 3);
    }

    #[test]
    fn errors_name_the_instrument() {
        let mut env = DriverEnv::emulated();
        let args = serde_json::from_value(serde_json::json!({"adress": 0})).unwrap();
        let e = open_instrument("dio", &DriverRef::parse("mcc152").unwrap(), &args, &mut env)
            .err()
            .unwrap()
            .to_string();
        assert!(e.contains("instruments.dio.kwargs: unknown field `adress`"), "{e}");
        let e = open_instrument(
            "x",
            &DriverRef::parse("lab.thing:make").unwrap(),
            &Map::new(),
            &mut env,
        )
        .err()
        .unwrap()
        .to_string();
        assert!(e.contains("Python driver"), "{e}");
        let e = open_instrument("x", &DriverRef::parse("mcc999").unwrap(), &Map::new(), &mut env)
            .err()
            .unwrap()
            .to_string();
        assert!(e.contains("unknown driver \"mcc999\""), "{e}");
    }

    #[test]
    fn emulated_psu_is_reachable() {
        let mut env = DriverEnv::emulated();
        let args = serde_json::from_value(serde_json::json!({"visa_address": "TCPIP::10.0.0.9::5025::SOCKET", "channels": [{"id": 1, "logical_name": "a"}, {"id": 2, "logical_name": "b"}]})).unwrap();
        let mut psu =
            open_instrument("psu", &DriverRef::parse("bk_psu").unwrap(), &args, &mut env).unwrap();
        psu.power_supply().unwrap().set_voltage(&ChannelRef::Name("b".into()), 7.5).unwrap();
        assert_eq!(env.emulated_psu["psu"].channel_state(2).unwrap().0, 7.5);
    }
}
