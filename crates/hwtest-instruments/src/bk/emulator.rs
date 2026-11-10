//! In-process BK Precision 9100-series PSU emulator (implements
//! [`ScpiTransport`]). Accepts long/short SCPI forms and optional segments
//! (`SOURce:VOLTage:LEVel:IMMediate:AMPLitude` == `VOLT`).

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use crate::error::{InstrumentError, Result};
use crate::scpi::ScpiTransport;

const LONG_TO_SHORT: &[(&str, &str)] = &[
    ("VOLTAGE", "VOLT"),
    ("CURRENT", "CURR"),
    ("OUTPUT", "OUTP"),
    ("MEASURE", "MEAS"),
    ("INSTRUMENT", "INST"),
    ("SOURCE", "SOUR"),
    ("LEVEL", "LEV"),
    ("IMMEDIATE", "IMM"),
    ("AMPLITUDE", "AMPL"),
    ("SCALAR", "SCAL"),
    ("STATE", "STAT"),
    ("SYSTEM", "SYST"),
    ("ERROR", "ERR"),
    ("PROTECTION", "PROT"),
    ("APPLY", "APPL"),
    ("SELECT", "NSEL"),
    ("POWER", "POW"),
];
const OPTIONAL: &[&str] = &["SOUR", "LEV", "IMM", "AMPL", "SCAL", "DC", "STAT"];

/// Canonical short form of a SCPI header.
pub fn normalize_header(header: &str) -> String {
    let upper = header.trim_start_matches(':').to_ascii_uppercase();
    upper
        .split(':')
        .map(|seg| LONG_TO_SHORT.iter().find(|(l, _)| *l == seg).map_or(seg, |(_, s)| s))
        .filter(|seg| !OPTIONAL.contains(seg))
        .collect::<Vec<_>>()
        .join(":")
}

/// Emulated PSU model.
#[derive(Debug, Clone)]
pub struct EmulatorConfig {
    /// `*IDN?` response.
    pub identity: String,
    /// Number of output channels.
    pub num_channels: usize,
    /// Maximum voltage (default OVP level).
    pub max_voltage: f64,
    /// Maximum current.
    pub max_current: f64,
}

#[derive(Debug, Clone, Default)]
struct Channel {
    voltage: f64,
    current_limit: f64,
    output: bool,
    ovp: f64,
    measured_voltage: Option<f64>,
    measured_current: Option<f64>,
}

#[derive(Debug)]
struct State {
    config: EmulatorConfig,
    channels: Vec<Channel>,
    selected: usize,
    response: VecDeque<String>,
    errors: VecDeque<(i32, &'static str)>,
}

/// Handle to the emulated PSU. Clones share state, so a test can keep one
/// handle to inject measurements while a driver uses another.
#[derive(Debug, Clone)]
pub struct BkPsuEmulator {
    state: Arc<Mutex<State>>,
}

impl BkPsuEmulator {
    /// Creates an emulator.
    ///
    /// # Panics
    /// If `num_channels` is zero.
    pub fn new(config: EmulatorConfig) -> Self {
        assert!(config.num_channels >= 1, "num_channels must be >= 1");
        let channels =
            vec![Channel { ovp: config.max_voltage, ..Channel::default() }; config.num_channels];
        Self {
            state: Arc::new(Mutex::new(State {
                config,
                channels,
                selected: 1,
                response: VecDeque::new(),
                errors: VecDeque::new(),
            })),
        }
    }

    /// Single-channel 9115 (60 V / 5 A).
    pub fn model_9115(serial: &str) -> Self {
        Self::new(EmulatorConfig {
            identity: format!("B&K Precision,9115,{serial},V1.00-V1.00"),
            num_channels: 1,
            max_voltage: 60.0,
            max_current: 5.0,
        })
    }

    /// Three-channel 9130B (30 V / 3 A).
    pub fn model_9130b(serial: &str) -> Self {
        Self::new(EmulatorConfig {
            identity: format!("B&K Precision,9130B,{serial},V1.00-V1.00"),
            num_channels: 3,
            max_voltage: 30.0,
            max_current: 3.0,
        })
    }

    /// `bk9115` / `bk9130b` (as used in `emulator:` addresses).
    ///
    /// # Errors
    /// [`InstrumentError::Config`] for unknown models.
    pub fn by_name(model: &str) -> Result<Self> {
        match model.to_ascii_lowercase().as_str() {
            "bk9115" | "9115" => Ok(Self::model_9115("EMU0001")),
            "bk9130b" | "9130b" => Ok(Self::model_9130b("EMU0001")),
            other => Err(InstrumentError::Config(format!(
                "unknown emulator model {other:?} (bk9115, bk9130b)"
            ))),
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Overrides the measured voltage of a channel (1-based).
    pub fn set_measured_voltage(&self, channel: usize, value: Option<f64>) {
        if let Some(c) = self.lock().channels.get_mut(channel.wrapping_sub(1)) {
            c.measured_voltage = value;
        }
    }

    /// Overrides the measured current of a channel (1-based).
    pub fn set_measured_current(&self, channel: usize, value: Option<f64>) {
        if let Some(c) = self.lock().channels.get_mut(channel.wrapping_sub(1)) {
            c.measured_current = value;
        }
    }

    /// `(voltage setpoint, current limit, output on)` of a channel (1-based).
    pub fn channel_state(&self, channel: usize) -> Option<(f64, f64, bool)> {
        self.lock()
            .channels
            .get(channel.wrapping_sub(1))
            .map(|c| (c.voltage, c.current_limit, c.output))
    }

    /// Processes one SCPI line (exposed for the TCP server).
    pub fn handle_line(&self, line: &str) {
        self.lock().handle(line.trim());
    }

    /// Takes the pending response, if any.
    pub fn take_response(&self) -> Option<String> {
        self.lock().response.pop_front()
    }
}

impl State {
    fn ch(&mut self) -> &mut Channel {
        let i = self.selected - 1;
        &mut self.channels[i]
    }

    fn param_error(&mut self) {
        self.errors.push_back((-220, "Parameter error"));
    }

    fn reply(&mut self, s: String) {
        self.response.push_back(s);
    }

    #[allow(clippy::too_many_lines)] // one match arm per SCPI command
    fn handle(&mut self, line: &str) {
        if line.is_empty() {
            return;
        }
        let (is_query, header, args) = match line.find('?') {
            Some(q) => (true, &line[..=q], line[q + 1..].trim()),
            None => match line.split_once(char::is_whitespace) {
                Some((h, a)) => (false, h, a.trim()),
                None => (false, line, ""),
            },
        };
        match header.to_ascii_uppercase().as_str() {
            "*IDN?" => return self.reply(self.config.identity.clone()),
            "*OPC?" => return self.reply("1".into()),
            "*RST" => {
                let ovp = self.config.max_voltage;
                for c in &mut self.channels {
                    *c = Channel { ovp, ..Channel::default() };
                }
                self.selected = 1;
                return;
            }
            "*CLS" => {
                self.errors.clear();
                return;
            }
            _ => {}
        }
        let key = normalize_header(header.trim_end_matches('?'));
        if is_query {
            let out = match key.as_str() {
                "SYST:ERR" => {
                    Some(self.errors.pop_front().map_or_else(
                        || "0,\"No error\"".to_string(),
                        |(c, m)| format!("{c},\"{m}\""),
                    ))
                }
                "VOLT" => Some(format!("{:.4}", self.ch().voltage)),
                "CURR" => Some(format!("{:.4}", self.ch().current_limit)),
                "VOLT:PROT" => Some(format!("{:.4}", self.ch().ovp)),
                "OUTP" => Some(if self.ch().output { "1" } else { "0" }.to_string()),
                "INST:NSEL" => Some(self.selected.to_string()),
                "MEAS:VOLT" => Some(format!("{:.4}", self.measured_v())),
                "MEAS:CURR" => Some(format!("{:.4}", self.ch().measured_current.unwrap_or(0.0))),
                "MEAS:POW" => {
                    let p = self.measured_v() * self.ch().measured_current.unwrap_or(0.0);
                    Some(format!("{p:.4}"))
                }
                "APPL" => {
                    let c = self.ch().clone();
                    Some(format!("{:.4},{:.4}", c.voltage, c.current_limit))
                }
                _ => None,
            };
            match out {
                Some(r) => self.reply(r),
                None => self.errors.push_back((-100, "Command error")),
            }
            return;
        }
        let num = args.parse::<f64>();
        match key.as_str() {
            "VOLT" => match num {
                Ok(v) => self.ch().voltage = v,
                Err(_) => self.param_error(),
            },
            "CURR" => match num {
                Ok(v) => self.ch().current_limit = v,
                Err(_) => self.param_error(),
            },
            "VOLT:PROT" => match num {
                Ok(v) => self.ch().ovp = v,
                Err(_) => self.param_error(),
            },
            "OUTP" => match args.to_ascii_uppercase().as_str() {
                "ON" | "1" => self.ch().output = true,
                "OFF" | "0" => self.ch().output = false,
                _ => self.param_error(),
            },
            "INST:NSEL" => match args.parse::<usize>() {
                Ok(n) if (1..=self.channels.len()).contains(&n) => self.selected = n,
                _ => self.param_error(),
            },
            "APPL" => {
                let parts: Vec<&str> = args.split(',').map(str::trim).collect();
                let parsed = (|| {
                    if parts.len() != 3 {
                        return None;
                    }
                    let n: usize =
                        parts[0].to_ascii_uppercase().strip_prefix("CH")?.parse().ok()?;
                    Some((n, parts[1].parse::<f64>().ok()?, parts[2].parse::<f64>().ok()?))
                })();
                match parsed {
                    Some((n, v, i)) if (1..=self.channels.len()).contains(&n) => {
                        self.channels[n - 1].voltage = v;
                        self.channels[n - 1].current_limit = i;
                    }
                    _ => self.param_error(),
                }
            }
            _ => self.errors.push_back((-100, "Command error")),
        }
    }

    fn measured_v(&mut self) -> f64 {
        let c = self.ch();
        c.measured_voltage.unwrap_or(if c.output { c.voltage } else { 0.0 })
    }
}

impl ScpiTransport for BkPsuEmulator {
    fn write(&mut self, message: &str) -> Result<()> {
        self.handle_line(message);
        Ok(())
    }

    fn read(&mut self) -> Result<String> {
        Ok(self.take_response().unwrap_or_default())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn q(e: &mut BkPsuEmulator, cmd: &str) -> String {
        e.write(cmd).unwrap();
        e.read().unwrap()
    }

    #[test]
    fn normalization() {
        assert_eq!(normalize_header(":SOURce:VOLTage:LEVel:IMMediate:AMPLitude"), "VOLT");
        assert_eq!(normalize_header("INSTrument:SELect"), "INST:NSEL");
        assert_eq!(normalize_header("meas:volt"), "MEAS:VOLT");
    }

    #[test]
    fn behaves_like_the_python_emulator() {
        let mut e = BkPsuEmulator::model_9130b("SN1");
        assert_eq!(q(&mut e, "*IDN?"), "B&K Precision,9130B,SN1,V1.00-V1.00");
        e.write("SOURce:VOLTage 12.5").unwrap();
        assert_eq!(q(&mut e, "VOLT?"), "12.5000");
        assert_eq!(q(&mut e, "MEAS:VOLT?"), "0.0000"); // output off
        e.write("OUTPut ON").unwrap();
        assert_eq!(q(&mut e, "MEAS:VOLT?"), "12.5000");
        e.set_measured_current(1, Some(0.5));
        assert_eq!(q(&mut e, "MEAS:POW?"), "6.2500");
        e.write("APPL CH2,5.0,1.0").unwrap();
        e.write("INST:NSEL 2").unwrap();
        assert_eq!(q(&mut e, "APPL?"), "5.0000,1.0000");
        e.write("INST:NSEL 9").unwrap();
        e.write("BOGUS 1").unwrap();
        assert_eq!(q(&mut e, "SYST:ERR?"), "-220,\"Parameter error\"");
        assert_eq!(q(&mut e, "SYSTem:ERRor?"), "-100,\"Command error\"");
        assert_eq!(q(&mut e, "SYST:ERR?"), "0,\"No error\"");
        e.write("*RST").unwrap();
        assert_eq!(e.channel_state(1), Some((0.0, 0.0, false)));
    }
}
