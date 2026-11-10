//! BK Precision 9100-series DC power supplies (9115, 9130B, ...).
//!
//! Rack `kwargs`:
//! ```yaml
//! driver: bk_psu          # legacy: hwtest_bkprecision.psu:create_instrument
//! kwargs:
//!   visa_address: "TCPIP::192.168.68.20::5025::SOCKET"   # or address:, emulator:bk9130b
//!   channels:                                            # optional; default one channel "output"
//!     - {id: 1, logical_name: dut_power, max_voltage: 13.0, max_current: 2.0}
//! ```
//! `max_voltage` / `max_current` are software limits checked before any
//! command is sent.

pub mod emulator;
pub mod server;

use std::time::Duration;

use hwtest_core::InstrumentIdentity;
use serde::Deserialize;

use crate::error::{InstrumentError, Result};
use crate::scpi::{open_transport, ScpiConnection};
use crate::traits::{resolve, ChannelInfo, ChannelKind, ChannelRef, Instrument, PowerSupply};

/// One configured output channel.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PsuChannelConfig {
    /// Channel number (1-based).
    pub id: u32,
    /// Logical name (`logical_name` or `name`).
    #[serde(alias = "name")]
    pub logical_name: String,
    /// Software voltage limit.
    #[serde(default)]
    pub max_voltage: Option<f64>,
    /// Software current limit.
    #[serde(default)]
    pub max_current: Option<f64>,
}

/// Driver configuration.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BkPsuConfig {
    /// Instrument address (see [`crate::scpi::transport`]).
    #[serde(alias = "address")]
    pub visa_address: String,
    /// Output channels.
    #[serde(default)]
    pub channels: Vec<PsuChannelConfig>,
    /// I/O timeout in seconds.
    #[serde(default = "default_timeout")]
    pub timeout: f64,
}

fn default_timeout() -> f64 {
    5.0
}

/// A BK Precision PSU.
pub struct BkPsu {
    conn: ScpiConnection,
    channels: Vec<PsuChannelConfig>,
    model: String,
}

impl BkPsu {
    /// Connects and checks communication with `*IDN?`.
    ///
    /// # Errors
    /// Invalid config or communication failure.
    pub fn open(mut config: BkPsuConfig) -> Result<Self> {
        if config.channels.is_empty() {
            config.channels.push(PsuChannelConfig {
                id: 1,
                logical_name: "output".into(),
                max_voltage: None,
                max_current: None,
            });
        }
        let transport =
            open_transport(&config.visa_address, Duration::from_secs_f64(config.timeout))?;
        Self::with_connection(ScpiConnection::new(transport, true), config.channels)
    }

    /// Uses an existing connection (e.g. to an emulator).
    ///
    /// # Errors
    /// Communication failure.
    pub fn with_connection(
        mut conn: ScpiConnection,
        channels: Vec<PsuChannelConfig>,
    ) -> Result<Self> {
        let model = conn.identity()?.model;
        Ok(Self { conn, channels, model: format!("BK Precision {model}") })
    }

    fn cfg(&self, ch: &ChannelRef) -> Result<PsuChannelConfig> {
        let id = resolve(&self.channels(), ch, &[ChannelKind::Psu], &self.model)?;
        Ok(self.channels.iter().find(|c| c.id == id).cloned().expect("resolved id exists"))
    }

    fn select(&mut self, id: u32) -> Result<()> {
        // Single-channel supplies (9115) may reject channel selection.
        if self.channels.len() > 1 || id != 1 {
            self.conn.command(&format!("INST:NSEL {id}"))?;
        }
        Ok(())
    }

    fn check_limits(c: &PsuChannelConfig, volts: Option<f64>, amps: Option<f64>) -> Result<()> {
        if let (Some(v), Some(max)) = (volts, c.max_voltage) {
            if v > max {
                return Err(InstrumentError::OutOfRange(format!(
                    "voltage {v} V exceeds limit {max} V for channel {:?}",
                    c.logical_name
                )));
            }
        }
        if let (Some(a), Some(max)) = (amps, c.max_current) {
            if a > max {
                return Err(InstrumentError::OutOfRange(format!(
                    "current {a} A exceeds limit {max} A for channel {:?}",
                    c.logical_name
                )));
            }
        }
        if volts.is_some_and(|v| v < 0.0 || !v.is_finite())
            || amps.is_some_and(|a| a < 0.0 || !a.is_finite())
        {
            return Err(InstrumentError::OutOfRange(format!(
                "negative or non-finite setpoint for channel {:?}",
                c.logical_name
            )));
        }
        Ok(())
    }

    fn query_num(&mut self, ch: &ChannelRef, cmd: &str) -> Result<f64> {
        let c = self.cfg(ch)?;
        self.select(c.id)?;
        self.conn.query_number(cmd)
    }
}

impl Instrument for BkPsu {
    fn model(&self) -> &str {
        &self.model
    }

    fn identity(&mut self) -> Result<InstrumentIdentity> {
        self.conn.identity()
    }

    fn channels(&self) -> Vec<ChannelInfo> {
        self.channels
            .iter()
            .map(|c| ChannelInfo {
                name: c.logical_name.clone(),
                id: c.id,
                kind: ChannelKind::Psu,
                unit: "V",
            })
            .collect()
    }

    fn close(&mut self) -> Result<()> {
        self.conn.close()
    }

    fn power_supply(&mut self) -> Option<&mut dyn PowerSupply> {
        Some(self)
    }
}

impl PowerSupply for BkPsu {
    fn set_voltage(&mut self, ch: &ChannelRef, volts: f64) -> Result<()> {
        let c = self.cfg(ch)?;
        Self::check_limits(&c, Some(volts), None)?;
        self.select(c.id)?;
        self.conn.command(&format!("VOLT {volts}"))
    }

    fn set_current_limit(&mut self, ch: &ChannelRef, amps: f64) -> Result<()> {
        let c = self.cfg(ch)?;
        Self::check_limits(&c, None, Some(amps))?;
        self.select(c.id)?;
        self.conn.command(&format!("CURR {amps}"))
    }

    fn set_output(&mut self, ch: &ChannelRef, on: bool) -> Result<()> {
        let c = self.cfg(ch)?;
        self.select(c.id)?;
        self.conn.command(if on { "OUTP ON" } else { "OUTP OFF" })
    }

    fn apply(&mut self, ch: &ChannelRef, volts: f64, amps: f64) -> Result<()> {
        let c = self.cfg(ch)?;
        Self::check_limits(&c, Some(volts), Some(amps))?;
        self.conn.command(&format!("APPL CH{},{volts},{amps}", c.id))
    }

    fn voltage_setpoint(&mut self, ch: &ChannelRef) -> Result<f64> {
        self.query_num(ch, "VOLT?")
    }

    fn current_limit(&mut self, ch: &ChannelRef) -> Result<f64> {
        self.query_num(ch, "CURR?")
    }

    fn output_enabled(&mut self, ch: &ChannelRef) -> Result<bool> {
        let c = self.cfg(ch)?;
        self.select(c.id)?;
        self.conn.query_bool("OUTP?")
    }

    fn measure_voltage(&mut self, ch: &ChannelRef) -> Result<f64> {
        self.query_num(ch, "MEAS:VOLT?")
    }

    fn measure_current(&mut self, ch: &ChannelRef) -> Result<f64> {
        self.query_num(ch, "MEAS:CURR?")
    }

    fn measure_power(&mut self, ch: &ChannelRef) -> Result<f64> {
        self.query_num(ch, "MEAS:POW?")
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use emulator::BkPsuEmulator;

    fn psu() -> (BkPsu, BkPsuEmulator) {
        let emu = BkPsuEmulator::model_9130b("SN7");
        let conn = ScpiConnection::new(Box::new(emu.clone()), true);
        let channels = vec![
            PsuChannelConfig {
                id: 1,
                logical_name: "dut_5v".into(),
                max_voltage: Some(5.5),
                max_current: Some(1.0),
            },
            PsuChannelConfig {
                id: 2,
                logical_name: "dut_12v".into(),
                max_voltage: None,
                max_current: None,
            },
        ];
        (BkPsu::with_connection(conn, channels).unwrap(), emu)
    }

    #[test]
    fn drives_named_channels() {
        let (mut p, emu) = psu();
        assert_eq!(p.model(), "BK Precision 9130B");
        p.apply(&"dut_5v".into(), 5.0, 0.5).unwrap();
        p.set_output(&"dut_5v".into(), true).unwrap();
        p.set_voltage(&ChannelRef::Id(2), 12.0).unwrap();
        assert_eq!(emu.channel_state(1), Some((5.0, 0.5, true)));
        assert_eq!(emu.channel_state(2), Some((12.0, 0.0, false)));
        assert_eq!(p.measure_voltage(&"dut_5v".into()).unwrap(), 5.0);
        emu.set_measured_current(1, Some(0.2));
        assert!((p.measure_power(&"dut_5v".into()).unwrap() - 1.0).abs() < 1e-9);
        assert!(p.output_enabled(&"dut_5v".into()).unwrap());
        assert_eq!(p.voltage_setpoint(&"dut_12v".into()).unwrap(), 12.0);
        assert_eq!(p.identity().unwrap().serial, "SN7");
    }

    #[test]
    fn software_limits_block_commands() {
        let (mut p, emu) = psu();
        let e = p.set_voltage(&"dut_5v".into(), 6.0).unwrap_err().to_string();
        assert!(e.contains("exceeds limit 5.5 V for channel \"dut_5v\""), "{e}");
        assert!(p.apply(&"dut_5v".into(), 5.0, 2.0).is_err());
        assert!(p.set_voltage(&"dut_12v".into(), -1.0).is_err());
        assert_eq!(emu.channel_state(1), Some((0.0, 0.0, false)), "nothing was sent");
        assert!(p
            .set_voltage(&"nope".into(), 1.0)
            .unwrap_err()
            .to_string()
            .contains("configured: dut_5v, dut_12v"));
    }

    #[test]
    fn instrument_errors_surface() {
        let emu = BkPsuEmulator::model_9115("A");
        let conn = ScpiConnection::new(Box::new(emu), true);
        let mut p = BkPsu::with_connection(
            conn,
            vec![PsuChannelConfig {
                id: 1,
                logical_name: "out".into(),
                max_voltage: None,
                max_current: None,
            }],
        )
        .unwrap();
        // Channel 1 on a single-channel supply sends no INST:NSEL.
        p.set_voltage(&"out".into(), 3.0).unwrap();
        // An unsupported query surfaces the instrument's error queue.
        let e = p.conn.query("BOGUS?").unwrap_err().to_string();
        assert_eq!(e, "instrument error(s): -100,\"Command error\"");
    }

    #[test]
    fn open_by_address() {
        let cfg: BkPsuConfig =
            serde_json::from_value(serde_json::json!({"visa_address": "emulator:bk9115"})).unwrap();
        let mut p = BkPsu::open(cfg).unwrap();
        assert_eq!(p.channels()[0].name, "output");
        p.set_voltage(&ChannelRef::Id(1), 1.5).unwrap();
        assert_eq!(p.voltage_setpoint(&ChannelRef::Id(1)).unwrap(), 1.5);
    }
}
