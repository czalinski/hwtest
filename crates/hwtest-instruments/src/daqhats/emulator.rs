//! Software model of MCC DAQ HATs for development without hardware.
//!
//! Boards are declared by address and product id. Inputs can be set from a
//! test (`set_analog_input`, `set_temperature`, `set_digital_input`) or wired
//! to emulated outputs (`connect_analog`), e.g. MCC 152 AO0 -> MCC 118 CH0 as
//! on the integration bench.

use std::collections::HashMap;
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use super::{ids, status, DaqHats, HatInfo, ScanRead, DIO_DIRECTION};
use crate::error::{InstrumentError, Result};

#[derive(Debug, Clone)]
struct Scan {
    channels: Vec<u8>,
    rate: f64,
    started: Instant,
    consumed: u64,
    running: bool,
}

#[derive(Debug, Clone)]
enum Board {
    Mcc118 { open: bool, inputs: [f64; 8], scan: Option<Scan> },
    Mcc134 { open: bool, temps: [f64; 4], types: [u8; 4] },
    Mcc152 { open: bool, ao: [f64; 2], is_input: [bool; 8], out: [bool; 8], ext_in: [bool; 8] },
}

impl Board {
    fn id(&self) -> u16 {
        match self {
            Board::Mcc118 { .. } => ids::MCC_118,
            Board::Mcc134 { .. } => ids::MCC_134,
            Board::Mcc152 { .. } => ids::MCC_152,
        }
    }
}

/// An analog source that can drive an emulated input.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct AnalogOut {
    /// MCC 152 address.
    pub address: u8,
    /// Output channel (0-1).
    pub channel: u8,
}

#[derive(Default)]
struct State {
    boards: HashMap<u8, Board>,
    /// (MCC 118 address, channel) -> (MCC 152 output driving it, gain).
    links: HashMap<(u8, u8), (AnalogOut, f64)>,
}

/// Emulated DAQ HAT stack.
#[derive(Default)]
pub struct EmulatedDaqHats {
    state: Mutex<State>,
}

fn invalid(address: u8, want: &str) -> InstrumentError {
    InstrumentError::Comms(format!(
        "no {want} at address {address}: invalid device (wrong HAT type at this address?) (-5)"
    ))
}

impl EmulatedDaqHats {
    /// An empty stack; add boards with [`EmulatedDaqHats::add_board`].
    pub fn new() -> Self {
        Self::default()
    }

    /// A stack with the given `(address, product id)` boards.
    pub fn with_boards(boards: &[(u8, u16)]) -> Self {
        let e = Self::new();
        for (a, id) in boards {
            e.add_board(*a, *id);
        }
        e
    }

    /// Adds a board (MCC 118, 134 or 152); other ids are ignored.
    pub fn add_board(&self, address: u8, id: u16) {
        let board = match id {
            ids::MCC_118 => Board::Mcc118 { open: false, inputs: [0.0; 8], scan: None },
            ids::MCC_134 => Board::Mcc134 { open: false, temps: [22.0; 4], types: [255; 4] },
            ids::MCC_152 => Board::Mcc152 {
                open: false,
                ao: [0.0; 2],
                is_input: [true; 8],
                out: [false; 8],
                ext_in: [false; 8],
            },
            _ => return,
        };
        self.lock().boards.insert(address, board);
    }

    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Sets an MCC 118 input voltage.
    pub fn set_analog_input(&self, address: u8, channel: u8, volts: f64) {
        if let Some(Board::Mcc118 { inputs, .. }) = self.lock().boards.get_mut(&address) {
            if let Some(v) = inputs.get_mut(usize::from(channel)) {
                *v = volts;
            }
        }
    }

    /// Sets an MCC 134 temperature (°C, or one of [`super::tc_values`]).
    pub fn set_temperature(&self, address: u8, channel: u8, celsius: f64) {
        if let Some(Board::Mcc134 { temps, .. }) = self.lock().boards.get_mut(&address) {
            if let Some(v) = temps.get_mut(usize::from(channel)) {
                *v = celsius;
            }
        }
    }

    /// Sets the level an external circuit drives on an MCC 152 input.
    pub fn set_digital_input(&self, address: u8, channel: u8, level: bool) {
        if let Some(Board::Mcc152 { ext_in, .. }) = self.lock().boards.get_mut(&address) {
            if let Some(v) = ext_in.get_mut(usize::from(channel)) {
                *v = level;
            }
        }
    }

    /// Wires an MCC 152 analog output to an MCC 118 input.
    pub fn connect_analog(&self, from: AnalogOut, to_address: u8, to_channel: u8) {
        self.connect_analog_scaled(from, to_address, to_channel, 1.0);
    }

    /// Wires an output to an input through a gain (e.g. to emulate an input
    /// that needs calibration).
    pub fn connect_analog_scaled(
        &self,
        from: AnalogOut,
        to_address: u8,
        to_channel: u8,
        gain: f64,
    ) {
        self.lock().links.insert((to_address, to_channel), (from, gain));
    }

    /// Current MCC 152 analog output.
    pub fn analog_output(&self, address: u8, channel: u8) -> Option<f64> {
        match self.lock().boards.get(&address) {
            Some(Board::Mcc152 { ao, .. }) => ao.get(usize::from(channel)).copied(),
            _ => None,
        }
    }

    /// `(is_input, output level)` of an MCC 152 DIO line.
    pub fn digital_line(&self, address: u8, channel: u8) -> Option<(bool, bool)> {
        match self.lock().boards.get(&address) {
            Some(Board::Mcc152 { is_input, out, .. }) => {
                let i = usize::from(channel);
                Some((*is_input.get(i)?, *out.get(i)?))
            }
            _ => None,
        }
    }

    /// The thermocouple type configured on an MCC 134 channel.
    pub fn tc_type(&self, address: u8, channel: u8) -> Option<u8> {
        match self.lock().boards.get(&address) {
            Some(Board::Mcc134 { types, .. }) => types.get(usize::from(channel)).copied(),
            _ => None,
        }
    }

    fn ai_value(state: &State, address: u8, channel: u8) -> f64 {
        if let Some((src, gain)) = state.links.get(&(address, channel)) {
            if let Some(Board::Mcc152 { ao, .. }) = state.boards.get(&src.address) {
                return ao.get(usize::from(src.channel)).copied().unwrap_or(0.0) * gain;
            }
        }
        match state.boards.get(&address) {
            Some(Board::Mcc118 { inputs, .. }) => {
                inputs.get(usize::from(channel)).copied().unwrap_or(0.0)
            }
            _ => 0.0,
        }
    }

    fn with_board<T>(
        &self,
        address: u8,
        want: u16,
        name: &str,
        f: impl FnOnce(&mut Board) -> Result<T>,
    ) -> Result<T> {
        let mut s = self.lock();
        match s.boards.get_mut(&address) {
            Some(b) if b.id() == want => f(b),
            _ => Err(invalid(address, name)),
        }
    }

    fn set_open(&self, address: u8, want: u16, name: &str, value: bool) -> Result<()> {
        self.with_board(address, want, name, |b| {
            match b {
                Board::Mcc118 { open, .. }
                | Board::Mcc134 { open, .. }
                | Board::Mcc152 { open, .. } => *open = value,
            }
            Ok(())
        })
    }

    fn require_open(b: &Board, address: u8) -> Result<()> {
        let open = match b {
            Board::Mcc118 { open, .. }
            | Board::Mcc134 { open, .. }
            | Board::Mcc152 { open, .. } => *open,
        };
        if open {
            Ok(())
        } else {
            Err(InstrumentError::Comms(format!(
                "HAT at address {address} is not open: resource unavailable (-6)"
            )))
        }
    }
}

fn channel_ok(channel: u8, max: u8) -> Result<usize> {
    if channel <= max {
        Ok(usize::from(channel))
    } else {
        Err(InstrumentError::Comms(format!("channel {channel}: bad parameter (-1)")))
    }
}

impl DaqHats for EmulatedDaqHats {
    fn list(&self) -> Result<Vec<HatInfo>> {
        let s = self.lock();
        let mut out: Vec<HatInfo> = s
            .boards
            .iter()
            .map(|(a, b)| HatInfo {
                address: *a,
                id: b.id(),
                version: 1,
                product_name: match b.id() {
                    ids::MCC_118 => "MCC 118 Voltage Measurement DAQ HAT",
                    ids::MCC_134 => "MCC 134 Thermocouple Measurement DAQ HAT",
                    _ => "MCC 152 Analog Output / DIO DAQ HAT",
                }
                .to_string(),
            })
            .collect();
        out.sort_by_key(|h| h.address);
        Ok(out)
    }

    fn mcc118_open(&self, address: u8) -> Result<()> {
        self.set_open(address, ids::MCC_118, "MCC 118", true)
    }
    fn mcc118_close(&self, address: u8) -> Result<()> {
        self.set_open(address, ids::MCC_118, "MCC 118", false)
    }
    fn mcc118_serial(&self, address: u8) -> Result<String> {
        self.with_board(address, ids::MCC_118, "MCC 118", |_| Ok(format!("EMU118{address:02}")))
    }
    fn mcc118_a_in_read(&self, address: u8, channel: u8) -> Result<f64> {
        let ch = channel_ok(channel, 7)?;
        let s = self.lock();
        match s.boards.get(&address) {
            Some(b @ Board::Mcc118 { scan, .. }) => {
                Self::require_open(b, address)?;
                if scan.as_ref().is_some_and(|sc| sc.running) {
                    return Err(InstrumentError::Comms(format!(
                        "MCC 118 at {address}: device busy (-2)"
                    )));
                }
                Ok(Self::ai_value(&s, address, u8::try_from(ch).unwrap_or(0)))
            }
            _ => Err(invalid(address, "MCC 118")),
        }
    }
    fn mcc118_scan_actual_rate(&self, channel_count: u8, rate: f64) -> Result<f64> {
        if channel_count == 0 || rate <= 0.0 {
            return Err(InstrumentError::Comms(
                "mcc118_a_in_scan_actual_rate: bad parameter (-1)".into(),
            ));
        }
        Ok(rate.min(100_000.0 / f64::from(channel_count)))
    }
    fn mcc118_scan_start(
        &self,
        address: u8,
        channel_mask: u8,
        _samples: u32,
        rate: f64,
        _options: u32,
    ) -> Result<()> {
        let channels: Vec<u8> = (0..8).filter(|c| channel_mask & (1 << c) != 0).collect();
        let actual =
            self.mcc118_scan_actual_rate(u8::try_from(channels.len()).unwrap_or(0), rate)?;
        self.with_board(address, ids::MCC_118, "MCC 118", |b| {
            Self::require_open(b, address)?;
            if let Board::Mcc118 { scan, .. } = b {
                if scan.as_ref().is_some_and(|s| s.running) {
                    return Err(InstrumentError::Comms(format!(
                        "MCC 118 at {address}: device busy (-2)"
                    )));
                }
                *scan = Some(Scan {
                    channels,
                    rate: actual,
                    started: Instant::now(),
                    consumed: 0,
                    running: true,
                });
            }
            Ok(())
        })
    }
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss, clippy::cast_precision_loss)]
    fn mcc118_scan_read(
        &self,
        address: u8,
        samples_per_channel: i32,
        timeout: f64,
        buffer: &mut [f64],
    ) -> Result<ScanRead> {
        let deadline = Instant::now() + Duration::from_secs_f64(timeout.max(0.0));
        loop {
            let mut s = self.lock();
            let (scan, available) = match s.boards.get(&address) {
                Some(Board::Mcc118 { scan: Some(sc), .. }) => {
                    let produced = (sc.started.elapsed().as_secs_f64() * sc.rate) as u64;
                    (sc.clone(), produced.saturating_sub(sc.consumed))
                }
                Some(Board::Mcc118 { .. }) => {
                    return Err(InstrumentError::Comms(format!(
                        "MCC 118 at {address}: no scan (-6)"
                    )))
                }
                _ => return Err(invalid(address, "MCC 118")),
            };
            let n_ch = scan.channels.len();
            let capacity = (buffer.len() / n_ch.max(1)) as u64;
            let wanted = if samples_per_channel < 0 {
                available
            } else {
                u64::from(samples_per_channel.unsigned_abs())
            };
            if available >= wanted || Instant::now() >= deadline || !scan.running {
                let n = available.min(wanted).min(capacity);
                let values: Vec<f64> =
                    scan.channels.iter().map(|c| Self::ai_value(&s, address, *c)).collect();
                for i in 0..n as usize {
                    buffer[i * n_ch..(i + 1) * n_ch].copy_from_slice(&values);
                }
                if let Some(Board::Mcc118 { scan: Some(sc), .. }) = s.boards.get_mut(&address) {
                    sc.consumed += n;
                }
                let st = if scan.running { status::RUNNING } else { 0 };
                return Ok(ScanRead { status: st, samples_per_channel: n as u32 });
            }
            let missing = wanted - available;
            drop(s);
            let wait = Duration::from_secs_f64(missing as f64 / scan.rate)
                .min(deadline.saturating_duration_since(Instant::now()));
            std::thread::sleep(wait.max(Duration::from_millis(1)));
        }
    }
    fn mcc118_scan_stop(&self, address: u8) -> Result<()> {
        self.with_board(address, ids::MCC_118, "MCC 118", |b| {
            if let Board::Mcc118 { scan: Some(sc), .. } = b {
                sc.running = false;
            }
            Ok(())
        })
    }
    fn mcc118_scan_cleanup(&self, address: u8) -> Result<()> {
        self.with_board(address, ids::MCC_118, "MCC 118", |b| {
            if let Board::Mcc118 { scan, .. } = b {
                *scan = None;
            }
            Ok(())
        })
    }

    fn mcc134_open(&self, address: u8) -> Result<()> {
        self.set_open(address, ids::MCC_134, "MCC 134", true)
    }
    fn mcc134_close(&self, address: u8) -> Result<()> {
        self.set_open(address, ids::MCC_134, "MCC 134", false)
    }
    fn mcc134_serial(&self, address: u8) -> Result<String> {
        self.with_board(address, ids::MCC_134, "MCC 134", |_| Ok(format!("EMU134{address:02}")))
    }
    fn mcc134_tc_type_write(&self, address: u8, channel: u8, tc_type: u8) -> Result<()> {
        let ch = channel_ok(channel, 3)?;
        self.with_board(address, ids::MCC_134, "MCC 134", |b| {
            Self::require_open(b, address)?;
            if let Board::Mcc134 { types, .. } = b {
                types[ch] = tc_type;
            }
            Ok(())
        })
    }
    fn mcc134_update_interval_write(&self, address: u8, _seconds: u8) -> Result<()> {
        self.with_board(address, ids::MCC_134, "MCC 134", |b| Self::require_open(b, address))
    }
    fn mcc134_t_in_read(&self, address: u8, channel: u8) -> Result<f64> {
        let ch = channel_ok(channel, 3)?;
        self.with_board(address, ids::MCC_134, "MCC 134", |b| {
            Self::require_open(b, address)?;
            match b {
                Board::Mcc134 { temps, types, .. } if types[ch] == 255 => {
                    let _ = temps;
                    Err(InstrumentError::Comms(format!(
                        "MCC 134 at {address} channel {ch}: thermocouple type not set (-1)"
                    )))
                }
                Board::Mcc134 { temps, .. } => Ok(temps[ch]),
                _ => unreachable!(),
            }
        })
    }

    fn mcc152_open(&self, address: u8) -> Result<()> {
        self.set_open(address, ids::MCC_152, "MCC 152", true)
    }
    fn mcc152_close(&self, address: u8) -> Result<()> {
        self.set_open(address, ids::MCC_152, "MCC 152", false)
    }
    fn mcc152_serial(&self, address: u8) -> Result<String> {
        self.with_board(address, ids::MCC_152, "MCC 152", |_| Ok(format!("EMU152{address:02}")))
    }
    fn mcc152_dio_config_write_bit(
        &self,
        address: u8,
        channel: u8,
        item: u8,
        value: u8,
    ) -> Result<()> {
        let ch = channel_ok(channel, 7)?;
        self.with_board(address, ids::MCC_152, "MCC 152", |b| {
            Self::require_open(b, address)?;
            if let (Board::Mcc152 { is_input, .. }, DIO_DIRECTION) = (b, item) {
                // libdaqhats: 0 = output, 1 = input.
                is_input[ch] = value != 0;
            }
            Ok(())
        })
    }
    fn mcc152_dio_input_read_bit(&self, address: u8, channel: u8) -> Result<bool> {
        let ch = channel_ok(channel, 7)?;
        self.with_board(address, ids::MCC_152, "MCC 152", |b| {
            Self::require_open(b, address)?;
            match b {
                Board::Mcc152 { is_input, out, ext_in, .. } => {
                    Ok(if is_input[ch] { ext_in[ch] } else { out[ch] })
                }
                _ => unreachable!(),
            }
        })
    }
    fn mcc152_dio_output_write_bit(&self, address: u8, channel: u8, value: bool) -> Result<()> {
        let ch = channel_ok(channel, 7)?;
        self.with_board(address, ids::MCC_152, "MCC 152", |b| {
            Self::require_open(b, address)?;
            if let Board::Mcc152 { out, .. } = b {
                out[ch] = value;
            }
            Ok(())
        })
    }
    fn mcc152_a_out_write(&self, address: u8, channel: u8, value: f64) -> Result<()> {
        let ch = channel_ok(channel, 1)?;
        if !(0.0..=5.0).contains(&value) {
            return Err(InstrumentError::Comms(format!(
                "mcc152_a_out_write {value}: bad parameter (-1)"
            )));
        }
        self.with_board(address, ids::MCC_152, "MCC 152", |b| {
            Self::require_open(b, address)?;
            if let Board::Mcc152 { ao, .. } = b {
                ao[ch] = value;
            }
            Ok(())
        })
    }
}
