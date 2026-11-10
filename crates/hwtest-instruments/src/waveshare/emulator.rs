//! SPI-level model of the High-Precision AD/DA board.
//!
//! The model decodes the bytes the real drivers send: ADS1256 commands
//! (`RREG`/`WREG`/`RDATA`/`SYNC`/...), register contents, the active-low DRDY
//! line, and DAC8532 3-byte frames. Both chip selects low at once is reported
//! as a bus-contention error, catching driver bugs.

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use super::ads1256::{cmd, AINCOM};
use super::bus::{Bus, GpioLines, SharedBus, SpiBus};
use super::dac8532::cmd as dac_cmd;
use crate::error::{InstrumentError, Result};

/// Board wiring for the model.
#[derive(Debug, Clone, Copy)]
pub struct Pins {
    /// ADC chip select.
    pub adc_cs: u32,
    /// ADC DRDY.
    pub drdy: u32,
    /// DAC chip select.
    pub dac_cs: u32,
    /// ADC reference voltage.
    pub adc_vref: f64,
    /// DAC reference voltage.
    pub dac_vref: f64,
}

impl Default for Pins {
    fn default() -> Self {
        Self { adc_cs: 22, drdy: 17, dac_cs: 23, adc_vref: 2.5, dac_vref: 5.0 }
    }
}

#[derive(Debug)]
struct Model {
    pins: Pins,
    levels: HashMap<u32, bool>,
    regs: [u8; 11],
    rx: VecDeque<u8>,
    pending_wreg: Option<(u8, u8)>,   // (next reg, remaining)
    expect_count: Option<(u8, bool)>, // (reg, is_write) awaiting count byte
    inputs: [f64; 8],
    dac_in: [u16; 2],
    dac_out: [u16; 2],
    dac_frame: Vec<u8>,
    /// ADC input -> DAC output wired to it.
    links: HashMap<u8, u8>,
    log: Vec<u8>,
}

impl Model {
    fn new(pins: Pins) -> Self {
        let mut regs = [0u8; 11];
        regs[0] = 0x30; // chip id 3
        regs[1] = 0x01;
        regs[3] = 0xF0;
        Self {
            pins,
            levels: HashMap::new(),
            regs,
            rx: VecDeque::new(),
            pending_wreg: None,
            expect_count: None,
            inputs: [0.0; 8],
            dac_in: [0; 2],
            dac_out: [0; 2],
            dac_frame: Vec::new(),
            links: HashMap::new(),
            log: Vec::new(),
        }
    }

    fn low(&self, pin: u32) -> bool {
        self.levels.get(&pin) == Some(&false)
    }

    fn selected(&self) -> Result<Option<u32>> {
        match (self.low(self.pins.adc_cs), self.low(self.pins.dac_cs)) {
            (true, true) => Err(InstrumentError::Comms(
                "emulated SPI: both chip selects asserted (bus contention)".into(),
            )),
            (true, false) => Ok(Some(self.pins.adc_cs)),
            (false, true) => Ok(Some(self.pins.dac_cs)),
            (false, false) => Ok(None),
        }
    }

    fn input_volts(&self, ch: u8) -> f64 {
        if ch == AINCOM {
            return 0.0;
        }
        if let Some(dac) = self.links.get(&ch) {
            return f64::from(self.dac_out[usize::from(*dac)]) / 65535.0 * self.pins.dac_vref;
        }
        self.inputs.get(usize::from(ch)).copied().unwrap_or(0.0)
    }

    #[allow(clippy::cast_possible_truncation)]
    fn conversion(&self) -> [u8; 3] {
        let mux = self.regs[1];
        let v = self.input_volts(mux >> 4) - self.input_volts(mux & 0x0F);
        let gain = f64::from(1u32 << (self.regs[2] & 0x07));
        let code = (v * gain * 8_388_607.0 / self.pins.adc_vref)
            .round()
            .clamp(-8_388_608.0, 8_388_607.0) as i32;
        let u = code.cast_unsigned() & 0x00FF_FFFF;
        [(u >> 16) as u8, (u >> 8) as u8, u as u8]
    }

    fn adc_byte(&mut self, b: u8) {
        self.log.push(b);
        if let Some((reg, is_write)) = self.expect_count.take() {
            let n = b + 1;
            if is_write {
                self.pending_wreg = Some((reg, n));
            } else {
                for i in 0..n {
                    let r = usize::from(reg + i);
                    self.rx.push_back(self.regs.get(r).copied().unwrap_or(0));
                }
            }
            return;
        }
        if let Some((reg, remaining)) = self.pending_wreg {
            if usize::from(reg) < self.regs.len() && reg != 0 {
                self.regs[usize::from(reg)] = b;
            } else if reg == 0 {
                self.regs[0] = (self.regs[0] & 0xF0) | (b & 0x0F); // id bits are read-only
            }
            self.pending_wreg = (remaining > 1).then(|| (reg + 1, remaining - 1));
            return;
        }
        match b {
            x if x & 0xF0 == cmd::RREG => self.expect_count = Some((x & 0x0F, false)),
            x if x & 0xF0 == cmd::WREG => self.expect_count = Some((x & 0x0F, true)),
            cmd::RDATA => {
                let c = self.conversion();
                self.rx.extend(c);
            }
            cmd::RESET => {
                let pins = self.pins;
                let (inputs, links, dac_out, dac_in, levels) = (
                    self.inputs,
                    self.links.clone(),
                    self.dac_out,
                    self.dac_in,
                    self.levels.clone(),
                );
                *self = Model::new(pins);
                self.inputs = inputs;
                self.links = links;
                self.dac_out = dac_out;
                self.dac_in = dac_in;
                self.levels = levels;
            }
            _ => {} // WAKEUP, SYNC, SDATAC, SELFCAL: no state to model
        }
    }

    fn dac_byte(&mut self, b: u8) {
        self.dac_frame.push(b);
        if self.dac_frame.len() == 3 {
            let (c, v) = (
                self.dac_frame[0],
                (u16::from(self.dac_frame[1]) << 8) | u16::from(self.dac_frame[2]),
            );
            self.dac_frame.clear();
            match c {
                dac_cmd::WRITE_A => {
                    self.dac_in[0] = v;
                    self.dac_out[0] = v;
                }
                dac_cmd::WRITE_B => {
                    self.dac_in[1] = v;
                    self.dac_out[1] = v;
                }
                dac_cmd::LOAD_A => self.dac_in[0] = v,
                dac_cmd::WRITE_B_UPDATE_BOTH => {
                    self.dac_in[1] = v;
                    self.dac_out = self.dac_in;
                }
                _ => {}
            }
        }
    }
}

/// Handle for inspecting and driving the emulated board.
#[derive(Clone, Debug)]
pub struct EmulatedAdDa {
    model: Arc<Mutex<Model>>,
}

struct EmuSpi(Arc<Mutex<Model>>);
struct EmuGpio(Arc<Mutex<Model>>);

fn lock(m: &Arc<Mutex<Model>>) -> MutexGuard<'_, Model> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

impl SpiBus for EmuSpi {
    fn write(&mut self, data: &[u8]) -> Result<()> {
        let mut m = lock(&self.0);
        let sel = m.selected()?;
        for &b in data {
            match sel {
                Some(p) if p == m.pins.adc_cs => m.adc_byte(b),
                Some(_) => m.dac_byte(b),
                None => {}
            }
        }
        Ok(())
    }

    fn read(&mut self, n: usize) -> Result<Vec<u8>> {
        let mut m = lock(&self.0);
        let sel = m.selected()?;
        if sel != Some(m.pins.adc_cs) {
            return Ok(vec![0; n]);
        }
        Ok((0..n).map(|_| m.rx.pop_front().unwrap_or(0)).collect())
    }
}

impl GpioLines for EmuGpio {
    fn setup_output(&mut self, pin: u32, high: bool) -> Result<()> {
        lock(&self.0).levels.insert(pin, high);
        Ok(())
    }
    fn setup_input(&mut self, _pin: u32) -> Result<()> {
        Ok(())
    }
    fn set(&mut self, pin: u32, high: bool) -> Result<()> {
        let mut m = lock(&self.0);
        if high && (pin == m.pins.dac_cs) {
            m.dac_frame.clear(); // CS rising edge ends a frame
        }
        m.levels.insert(pin, high);
        Ok(())
    }
    fn get(&mut self, pin: u32) -> Result<bool> {
        let m = lock(&self.0);
        // DRDY is active low; conversions are always ready in the model.
        Ok(if pin == m.pins.drdy { false } else { m.levels.get(&pin).copied().unwrap_or(false) })
    }
}

impl EmulatedAdDa {
    /// Creates the model and a bus connected to it.
    pub fn new(pins: Pins) -> (Self, SharedBus) {
        let model = Arc::new(Mutex::new(Model::new(pins)));
        let bus = Arc::new(Mutex::new(Bus {
            spi: Box::new(EmuSpi(model.clone())),
            gpio: Box::new(EmuGpio(model.clone())),
        }));
        (Self { model }, bus)
    }

    /// Sets the voltage on ADC input `channel` (0-7, against AINCOM).
    pub fn set_input(&self, channel: u8, volts: f64) {
        if let Some(v) = lock(&self.model).inputs.get_mut(usize::from(channel)) {
            *v = volts;
        }
    }

    /// Wires DAC output `dac` (0=A, 1=B) to ADC input `adc`.
    pub fn connect(&self, dac: u8, adc: u8) {
        lock(&self.model).links.insert(adc, dac);
    }

    /// Current DAC output voltage.
    pub fn dac_output(&self, channel: u8) -> f64 {
        let m = lock(&self.model);
        f64::from(m.dac_out[usize::from(channel)]) / 65535.0 * m.pins.dac_vref
    }

    /// ADS1256 register value.
    pub fn register(&self, r: u8) -> u8 {
        lock(&self.model).regs[usize::from(r)]
    }

    /// Bytes the ADC has received so far.
    pub fn adc_log(&self) -> Vec<u8> {
        lock(&self.model).log.clone()
    }
}
