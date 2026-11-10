//! The simulator core.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use hwtest_instruments::can::{open_can, CanBus, CanMessage};
use hwtest_instruments::mcp23017::{self, EmulatedMcp23017, Mcp23017};
use hwtest_instruments::waveshare::ads1256::{Ads1256, Ads1256Config};
use hwtest_instruments::waveshare::bus::linux_bus;
use hwtest_instruments::waveshare::dac8532::Dac8532;
use hwtest_instruments::waveshare::emulator::{EmulatedAdDa, Pins};
use hwtest_instruments::InstrumentError;
use serde::{Deserialize, Serialize};

/// Simulator error (maps to HTTP status codes in the server).
#[derive(Debug, thiserror::Error)]
pub enum SimError {
    /// Bad request value.
    #[error("{0}")]
    Invalid(String),
    /// The subsystem is disabled or failed to start.
    #[error("{0} not available")]
    Unavailable(&'static str),
    /// Hardware error.
    #[error(transparent)]
    Hardware(#[from] InstrumentError),
}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, SimError>;

/// ADC/DAC hardware.
#[derive(Debug, Clone, PartialEq)]
pub enum AdDaBackend {
    /// None (DAC writes are remembered; ADC reads fail).
    None,
    /// Waveshare High-Precision AD/DA on SPI0 / gpiochip0.
    Waveshare,
    /// Emulated board; DAC A is wired to ADC 0 when `loopback`.
    Emulated {
        /// Wire DAC A to ADC 0 and DAC B to ADC 1.
        loopback: bool,
    },
}

/// GPIO hardware.
#[derive(Debug, Clone, PartialEq)]
pub enum GpioBackend {
    /// None.
    None,
    /// MCP23017 on `/dev/i2c-{bus}` at `address`.
    Mcp23017 {
        /// I2C bus.
        bus: u32,
        /// Address (0x20-0x27).
        address: u16,
    },
    /// Emulated expander.
    Emulated,
}

/// Simulator settings.
#[derive(Debug, Clone)]
pub struct SimConfig {
    /// CAN interface (`can0`, `vcan0`, `virtual:<name>`); `None` disables CAN.
    pub can_interface: Option<String>,
    /// Heartbeat id.
    pub heartbeat_id: u32,
    /// Heartbeat interval (0 disables).
    pub heartbeat_interval_ms: u64,
    /// ADC/DAC.
    pub adda: AdDaBackend,
    /// GPIO.
    pub gpio: GpioBackend,
    /// DAC reference voltage.
    pub dac_vref: f64,
    /// Fault injection delay (0 disables).
    pub failure_delay: f64,
    /// Fault duration (0 = permanent once active).
    pub failure_duration: f64,
    /// Fault DAC offset (V).
    pub failure_offset: f64,
}

impl Default for SimConfig {
    fn default() -> Self {
        Self {
            can_interface: None,
            heartbeat_id: 0x100,
            heartbeat_interval_ms: 100,
            adda: AdDaBackend::None,
            gpio: GpioBackend::None,
            dac_vref: 5.0,
            failure_delay: 0.0,
            failure_duration: 10.0,
            failure_offset: 1.0,
        }
    }
}

/// CAN echo settings.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct EchoConfig {
    /// Echo enabled.
    pub enabled: bool,
    /// Added to the id of echoed frames.
    #[serde(default)]
    pub id_offset: i64,
    /// Only echo these ids (empty/None = all).
    #[serde(default)]
    pub filter_ids: Option<Vec<u32>>,
}

/// Heartbeat status.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct HeartbeatStatus {
    /// Running.
    pub running: bool,
    /// Frames sent.
    pub message_count: u64,
    /// Id.
    pub arbitration_id: u32,
    /// Interval.
    pub interval_ms: u64,
}

/// Fault injection status.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FailureStatus {
    /// Enabled (delay > 0).
    pub enabled: bool,
    /// Delay before the fault.
    pub delay_seconds: f64,
    /// Fault duration (0 = permanent).
    pub duration_seconds: f64,
    /// DAC offset while active.
    pub voltage_offset: f64,
    /// Fault currently active.
    pub active: bool,
    /// Completed fault cycles.
    pub cycle_count: u64,
    /// Seconds until the fault activates (None if not armed).
    pub time_until_active: Option<f64>,
}

struct Failure {
    enabled: bool,
    delay: f64,
    duration: f64,
    offset: f64,
    start: Option<Instant>,
    active: bool,
    cycles: u64,
}

struct AdDa {
    adc: Option<Ads1256>,
    dac: Option<Dac8532>,
}

struct Inner {
    echo: EchoConfig,
    heartbeat_count: u64,
    heartbeat_running: bool,
    rx: VecDeque<CanMessage>,
    adda: Option<AdDa>,
    dac_values: [f64; 2],
    gpio: Option<Mcp23017>,
    failure: Failure,
}

/// A running simulator.
pub struct Simulator {
    config: SimConfig,
    inner: Arc<Mutex<Inner>>,
    can: Option<Arc<dyn CanBus>>,
    started: Instant,
    stop: Arc<AtomicBool>,
    threads: Vec<JoinHandle<()>>,
    emulated_adda: Option<EmulatedAdDa>,
    emulated_gpio: Option<EmulatedMcp23017>,
}

const RX_MAX: usize = 100;

fn lock(m: &Mutex<Inner>) -> MutexGuard<'_, Inner> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

impl Simulator {
    /// Starts the simulator. Subsystems that fail to start are disabled
    /// (logged), as in the original simulator.
    pub fn start(config: SimConfig) -> Self {
        let mut emulated_adda = None;
        let adda = match &config.adda {
            AdDaBackend::None => None,
            AdDaBackend::Waveshare => match linux_bus(0, 0, 1_000_000, "/dev/gpiochip0") {
                Ok(bus) => Some(Self::open_adda(bus, config.dac_vref)),
                Err(e) => {
                    tracing::error!("AD/DA unavailable: {e}");
                    None
                }
            },
            AdDaBackend::Emulated { loopback } => {
                let (emu, bus) =
                    EmulatedAdDa::new(Pins { dac_vref: config.dac_vref, ..Pins::default() });
                if *loopback {
                    emu.connect(0, 0);
                    emu.connect(1, 1);
                }
                emulated_adda = Some(emu);
                Some(Self::open_adda(bus, config.dac_vref))
            }
        };
        let mut emulated_gpio = None;
        let gpio_bus = match &config.gpio {
            GpioBackend::None => None,
            GpioBackend::Mcp23017 { bus, address } => mcp23017::linux_bus(*bus, *address)
                .map_err(|e| tracing::error!("GPIO unavailable: {e}"))
                .ok(),
            GpioBackend::Emulated => {
                let e = EmulatedMcp23017::new();
                let b = e.bus();
                emulated_gpio = Some(e);
                Some(b)
            }
        };
        let gpio = gpio_bus.and_then(|b| {
            Mcp23017::open(b).map_err(|e| tracing::error!("GPIO unavailable: {e}")).ok()
        });
        let can = config
            .can_interface
            .as_deref()
            .and_then(|i| open_can(i).map_err(|e| tracing::error!("CAN unavailable: {e}")).ok());
        let inner = Arc::new(Mutex::new(Inner {
            echo: EchoConfig::default(),
            heartbeat_count: 0,
            heartbeat_running: false,
            rx: VecDeque::new(),
            adda,
            dac_values: [0.0; 2],
            gpio,
            failure: Failure {
                enabled: config.failure_delay > 0.0,
                delay: config.failure_delay,
                duration: config.failure_duration,
                offset: config.failure_offset,
                start: None,
                active: false,
                cycles: 0,
            },
        }));
        let stop = Arc::new(AtomicBool::new(false));
        let mut threads = Vec::new();
        if let Some(bus) = &can {
            let (bus2, inner2, stop2) = (bus.clone(), inner.clone(), stop.clone());
            threads.push(std::thread::spawn(move || Self::rx_loop(bus2.as_ref(), &inner2, &stop2)));
            if config.heartbeat_interval_ms > 0 {
                let (bus3, inner3, stop3) = (bus.clone(), inner.clone(), stop.clone());
                let (id, interval) =
                    (config.heartbeat_id, Duration::from_millis(config.heartbeat_interval_ms));
                lock(&inner).heartbeat_running = true;
                threads.push(std::thread::spawn(move || {
                    Self::heartbeat_loop(bus3.as_ref(), &inner3, &stop3, id, interval);
                }));
            }
        }
        Self {
            config,
            inner,
            can,
            started: Instant::now(),
            stop,
            threads,
            emulated_adda,
            emulated_gpio,
        }
    }

    fn open_adda(bus: hwtest_instruments::waveshare::bus::SharedBus, vref: f64) -> AdDa {
        let adc = Ads1256::open(bus.clone(), Ads1256Config::default())
            .map_err(|e| tracing::error!("ADC unavailable: {e}"))
            .ok();
        let dac =
            Dac8532::open(bus, 23, vref).map_err(|e| tracing::error!("DAC unavailable: {e}")).ok();
        AdDa { adc, dac }
    }

    fn rx_loop(bus: &dyn CanBus, inner: &Mutex<Inner>, stop: &AtomicBool) {
        while !stop.load(Ordering::Relaxed) {
            match bus.recv(Duration::from_millis(100)) {
                Ok(Some(msg)) => {
                    let echo = {
                        let mut i = lock(inner);
                        if i.rx.len() == RX_MAX {
                            i.rx.pop_front();
                        }
                        i.rx.push_back(msg.clone());
                        let e = &i.echo;
                        let matches = e
                            .filter_ids
                            .as_ref()
                            .is_none_or(|f| f.is_empty() || f.contains(&msg.arbitration_id));
                        (e.enabled && matches).then(|| {
                            let id = i64::from(msg.arbitration_id) + e.id_offset;
                            CanMessage {
                                arbitration_id: u32::try_from(id).unwrap_or(0),
                                timestamp_ns: 0,
                                ..msg
                            }
                        })
                    };
                    if let Some(m) = echo {
                        if let Err(e) = bus.send(&m) {
                            tracing::warn!("CAN echo failed: {e}");
                        }
                    }
                }
                Ok(None) => {}
                Err(e) => {
                    tracing::warn!("CAN receive: {e}");
                    std::thread::sleep(Duration::from_millis(100));
                }
            }
        }
    }

    fn heartbeat_loop(
        bus: &dyn CanBus,
        inner: &Mutex<Inner>,
        stop: &AtomicBool,
        id: u32,
        interval: Duration,
    ) {
        let mut next = Instant::now();
        while !stop.load(Ordering::Relaxed) {
            let count = lock(inner).heartbeat_count;
            let msg = CanMessage {
                is_extended_id: id > 0x7FF,
                ..CanMessage::new(id, &count.to_be_bytes())
            };
            match bus.send(&msg) {
                Ok(()) => lock(inner).heartbeat_count += 1,
                Err(e) => tracing::warn!("CAN heartbeat: {e}"),
            }
            next += interval;
            std::thread::sleep(next.saturating_duration_since(Instant::now()));
        }
        lock(inner).heartbeat_running = false;
    }

    /// Settings.
    pub fn config(&self) -> &SimConfig {
        &self.config
    }

    /// Seconds since start.
    pub fn uptime(&self) -> f64 {
        self.started.elapsed().as_secs_f64()
    }

    /// `(can, dac, adc, gpio)` availability.
    pub fn available(&self) -> (bool, bool, bool, bool) {
        let i = lock(&self.inner);
        let (dac, adc) =
            i.adda.as_ref().map_or((false, false), |a| (a.dac.is_some(), a.adc.is_some()));
        (
            self.can.is_some(),
            dac || matches!(self.config.adda, AdDaBackend::None),
            adc,
            i.gpio.is_some(),
        )
    }

    // --- CAN ---------------------------------------------------------------

    /// Sends a frame.
    ///
    /// # Errors
    /// CAN disabled, invalid frame or bus error.
    pub fn can_send(&self, msg: &CanMessage) -> Result<()> {
        self.can.as_ref().ok_or(SimError::Unavailable("CAN interface"))?.send(msg).map_err(|e| {
            match e {
                InstrumentError::OutOfRange(m) => SimError::Invalid(m),
                other => SimError::Hardware(other),
            }
        })
    }

    /// Received frames (most recent 100).
    pub fn can_received(&self) -> Vec<CanMessage> {
        lock(&self.inner).rx.iter().cloned().collect()
    }

    /// Clears received frames.
    pub fn can_clear_received(&self) {
        lock(&self.inner).rx.clear();
    }

    /// Echo settings.
    pub fn can_echo(&self) -> EchoConfig {
        lock(&self.inner).echo.clone()
    }

    /// Sets echo.
    ///
    /// # Errors
    /// Offset out of ±0x7FF.
    pub fn can_set_echo(&self, echo: EchoConfig) -> Result<()> {
        if echo.id_offset.abs() > 0x7FF {
            return Err(SimError::Invalid(format!(
                "id_offset must be within ±0x7FF, got {}",
                echo.id_offset
            )));
        }
        lock(&self.inner).echo = echo;
        Ok(())
    }

    /// Heartbeat status.
    pub fn can_heartbeat(&self) -> HeartbeatStatus {
        let i = lock(&self.inner);
        HeartbeatStatus {
            running: i.heartbeat_running,
            message_count: i.heartbeat_count,
            arbitration_id: self.config.heartbeat_id,
            interval_ms: self.config.heartbeat_interval_ms,
        }
    }

    // --- DAC / ADC ---------------------------------------------------------

    fn update_failure(f: &mut Failure) {
        let Some(start) = f.start else { return };
        let elapsed = start.elapsed().as_secs_f64();
        let active = if f.duration <= 0.0 {
            elapsed >= f.delay
        } else {
            let period = f.delay + f.duration;
            // elapsed and period are positive and finite; whole cycles fit in u64.
            #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
            let cycle = (elapsed / period) as u64;
            f.cycles = f.cycles.max(cycle);
            elapsed % period >= f.delay
        };
        if active != f.active {
            tracing::warn!(
                "fault injection {} after {elapsed:.1}s",
                if active { "ACTIVATED" } else { "RECOVERED" }
            );
            f.active = active;
        }
    }

    /// Writes a DAC channel (0/1). With fault injection armed, the first write
    /// starts the timer and active faults add the offset to the output.
    ///
    /// # Errors
    /// Bad channel/voltage or hardware error.
    pub fn dac_write(&self, channel: u8, volts: f64) -> Result<()> {
        if channel > 1 {
            return Err(SimError::Invalid(format!("channel must be 0 or 1, got {channel}")));
        }
        if !(0.0..=self.config.dac_vref).contains(&volts) {
            return Err(SimError::Invalid(format!(
                "voltage must be 0-{}V, got {volts}",
                self.config.dac_vref
            )));
        }
        let mut i = lock(&self.inner);
        let f = &mut i.failure;
        if f.enabled && f.start.is_none() {
            f.start = Some(Instant::now());
        }
        let mut out = volts;
        if f.enabled {
            Self::update_failure(f);
            if f.active {
                out = (volts + f.offset).min(self.config.dac_vref);
            }
        }
        if let Some(dac) = i.adda.as_mut().and_then(|a| a.dac.as_mut()) {
            dac.write_voltage(channel, out)?;
        }
        i.dac_values[usize::from(channel)] = volts;
        Ok(())
    }

    /// The last requested DAC voltage (without fault offset).
    ///
    /// # Errors
    /// Bad channel.
    pub fn dac_read(&self, channel: u8) -> Result<f64> {
        lock(&self.inner)
            .dac_values
            .get(usize::from(channel))
            .copied()
            .ok_or_else(|| SimError::Invalid(format!("channel must be 0 or 1, got {channel}")))
    }

    /// Reads an ADC channel (0-7).
    ///
    /// # Errors
    /// Bad channel, ADC unavailable or hardware error.
    pub fn adc_read(&self, channel: u8) -> Result<f64> {
        if channel > 7 {
            return Err(SimError::Invalid(format!("channel must be 0-7, got {channel}")));
        }
        let i = lock(&self.inner);
        let adc =
            i.adda.as_ref().and_then(|a| a.adc.as_ref()).ok_or(SimError::Unavailable("ADC"))?;
        Ok(adc.read_voltage(channel)?)
    }

    /// Emulation: sets the voltage on an emulated ADC input.
    ///
    /// # Errors
    /// Not emulated.
    pub fn emulate_adc_input(&self, channel: u8, volts: f64) -> Result<()> {
        self.emulated_adda
            .as_ref()
            .ok_or(SimError::Unavailable("emulated ADC"))?
            .set_input(channel, volts);
        Ok(())
    }

    /// Emulation: levels driven onto emulated GPIO inputs.
    ///
    /// # Errors
    /// Not emulated.
    pub fn emulate_gpio_inputs(&self, levels: u16) -> Result<()> {
        self.emulated_gpio
            .as_ref()
            .ok_or(SimError::Unavailable("emulated GPIO"))?
            .set_external(levels);
        Ok(())
    }

    // --- GPIO --------------------------------------------------------------

    fn gpio<T>(&self, f: impl FnOnce(&mut Mcp23017) -> hwtest_instruments::Result<T>) -> Result<T> {
        let mut i = lock(&self.inner);
        let g = i.gpio.as_mut().ok_or(SimError::Unavailable("GPIO"))?;
        f(g).map_err(|e| match e {
            InstrumentError::OutOfRange(m) => SimError::Invalid(m),
            other => SimError::Hardware(other),
        })
    }

    /// Configures a pin.
    ///
    /// # Errors
    /// GPIO unavailable, bad pin or bus error.
    pub fn gpio_configure(&self, pin: u8, output: bool, pullup: bool) -> Result<()> {
        self.gpio(|g| {
            g.set_direction(pin, output)?;
            g.set_pullup(pin, pullup)
        })
    }

    /// Drives a pin.
    ///
    /// # Errors
    /// GPIO unavailable, bad pin or bus error.
    pub fn gpio_write(&self, pin: u8, value: bool) -> Result<()> {
        self.gpio(|g| g.write_pin(pin, value))
    }

    /// `(level, is_output)` of a pin.
    ///
    /// # Errors
    /// GPIO unavailable, bad pin or bus error.
    pub fn gpio_read(&self, pin: u8) -> Result<(bool, bool)> {
        self.gpio(|g| Ok((g.read_pin(pin)?, g.is_output(pin)?)))
    }

    /// Writes a port (`A`/`B`).
    ///
    /// # Errors
    /// GPIO unavailable, bad port or bus error.
    pub fn gpio_write_port(&self, port: &str, value: u8) -> Result<()> {
        self.gpio(|g| g.write_port(mcp23017::port_index(port)?, value))
    }

    /// Writes all 16 pins.
    ///
    /// # Errors
    /// GPIO unavailable or bus error.
    pub fn gpio_write_all(&self, value: u16) -> Result<()> {
        let [a, b] = value.to_le_bytes();
        self.gpio(|g| {
            g.write_port(0, a)?;
            g.write_port(1, b)
        })
    }

    /// `(value, direction_mask)` of a port.
    ///
    /// # Errors
    /// GPIO unavailable, bad port or bus error.
    pub fn gpio_port(&self, port: &str) -> Result<(u8, u8)> {
        self.gpio(|g| {
            let p = mcp23017::port_index(port)?;
            Ok((g.read_port(p)?, g.direction_mask(p)?))
        })
    }

    // --- fault injection ---------------------------------------------------

    /// Fault injection status.
    pub fn failure_status(&self) -> FailureStatus {
        let mut i = lock(&self.inner);
        let f = &mut i.failure;
        if f.enabled {
            Self::update_failure(f);
        }
        let until = match (f.enabled, f.start, f.active) {
            (true, Some(_), true) => Some(0.0),
            (true, Some(s), false) if f.cycles == 0 => {
                Some((f.delay - s.elapsed().as_secs_f64()).max(0.0))
            }
            _ => None,
        };
        FailureStatus {
            enabled: f.enabled,
            delay_seconds: f.delay,
            duration_seconds: f.duration,
            voltage_offset: f.offset,
            active: f.active,
            cycle_count: f.cycles,
            time_until_active: until,
        }
    }

    /// Changes fault injection settings (a new delay re-arms it).
    ///
    /// # Errors
    /// Negative values or offset above 5 V.
    pub fn failure_configure(
        &self,
        delay: Option<f64>,
        duration: Option<f64>,
        offset: Option<f64>,
    ) -> Result<()> {
        for (name, v, max) in [
            ("delay_seconds", delay, f64::INFINITY),
            ("duration_seconds", duration, f64::INFINITY),
            ("voltage_offset", offset, 5.0),
        ] {
            if v.is_some_and(|v| !(0.0..=max).contains(&v)) {
                return Err(SimError::Invalid(format!("{name} out of range")));
            }
        }
        let mut i = lock(&self.inner);
        let f = &mut i.failure;
        if let Some(d) = delay {
            f.delay = d;
            f.enabled = d > 0.0;
            f.start = None;
            f.active = false;
            f.cycles = 0;
        }
        if let Some(d) = duration {
            f.duration = d;
        }
        if let Some(o) = offset {
            f.offset = o;
        }
        Ok(())
    }

    /// Resets the fault timer.
    pub fn failure_reset(&self) {
        let mut i = lock(&self.inner);
        i.failure.start = None;
        i.failure.active = false;
        i.failure.cycles = 0;
    }

    /// Emulation: DAC output voltage actually driven (including faults).
    pub fn emulated_dac_output(&self, channel: u8) -> Option<f64> {
        self.emulated_adda.as_ref().map(|e| e.dac_output(channel))
    }
}

impl Drop for Simulator {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        for t in self.threads.drain(..) {
            let _ = t.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use hwtest_instruments::can::{collect, wait_for};

    fn sim(name: &str) -> (Simulator, Arc<dyn CanBus>) {
        let bus = open_can(&format!("virtual:{name}")).unwrap();
        let s = Simulator::start(SimConfig {
            can_interface: Some(format!("virtual:{name}")),
            heartbeat_interval_ms: 20,
            adda: AdDaBackend::Emulated { loopback: true },
            gpio: GpioBackend::Emulated,
            ..SimConfig::default()
        });
        (s, bus)
    }

    #[test]
    fn heartbeat_counts_up() {
        let (_s, rack) = sim("hb");
        let a = wait_for(rack.as_ref(), 0x100, Duration::from_secs(1)).unwrap().unwrap();
        let b = wait_for(rack.as_ref(), 0x100, Duration::from_secs(1)).unwrap().unwrap();
        let n = |m: &CanMessage| u64::from_be_bytes(m.data.clone().try_into().unwrap());
        assert_eq!(n(&b), n(&a) + 1);
    }

    #[test]
    fn echo_with_offset_and_filter() {
        let (s, rack) = sim("echo");
        s.can_set_echo(EchoConfig {
            enabled: true,
            id_offset: 0x10,
            filter_ids: Some(vec![0x200]),
        })
        .unwrap();
        rack.send(&CanMessage::new(0x200, &[1, 2, 3])).unwrap();
        rack.send(&CanMessage::new(0x300, &[4])).unwrap();
        let echoed = wait_for(rack.as_ref(), 0x210, Duration::from_secs(1)).unwrap().unwrap();
        assert_eq!(echoed.data, vec![1, 2, 3]);
        assert!(
            collect(rack.as_ref(), Duration::from_millis(100), Some(0x310)).unwrap().is_empty(),
            "filtered"
        );
        std::thread::sleep(Duration::from_millis(50));
        let rx: Vec<u32> = s.can_received().iter().map(|m| m.arbitration_id).collect();
        assert_eq!(rx, vec![0x200, 0x300]);
        s.can_clear_received();
        assert_eq!(s.can_received(), [] as [hwtest_instruments::can::CanMessage; 0]);
        assert!(s
            .can_set_echo(EchoConfig { enabled: true, id_offset: 0x800, filter_ids: None })
            .is_err());
    }

    #[test]
    fn dac_adc_loopback_and_validation() {
        let (s, _) = sim("adda");
        s.dac_write(0, 2.5).unwrap();
        assert!((s.adc_read(0).unwrap() - 2.5).abs() < 1e-3);
        assert_eq!(s.dac_read(0).unwrap(), 2.5);
        assert!(
            s.dac_write(2, 1.0).is_err() && s.dac_write(0, 6.0).is_err() && s.adc_read(8).is_err()
        );
        s.emulate_adc_input(5, 1.25).unwrap();
        assert!((s.adc_read(5).unwrap() - 1.25).abs() < 1e-6);
    }

    #[test]
    fn fault_injection_offsets_output() {
        let s = Simulator::start(SimConfig {
            adda: AdDaBackend::Emulated { loopback: false },
            failure_delay: 0.05,
            failure_duration: 0.0,
            failure_offset: 1.0,
            ..SimConfig::default()
        });
        s.dac_write(0, 2.0).unwrap(); // arms the timer
        assert!((s.emulated_dac_output(0).unwrap() - 2.0).abs() < 1e-3);
        assert!(s.failure_status().time_until_active.unwrap() > 0.0);
        std::thread::sleep(Duration::from_millis(80));
        s.dac_write(0, 2.0).unwrap();
        assert!((s.emulated_dac_output(0).unwrap() - 3.0).abs() < 1e-3, "offset applied");
        assert_eq!(s.dac_read(0).unwrap(), 2.0, "readback is the requested value");
        let st = s.failure_status();
        assert!(st.active && st.enabled);
        s.failure_configure(Some(0.0), None, None).unwrap();
        s.dac_write(0, 2.0).unwrap();
        assert!((s.emulated_dac_output(0).unwrap() - 2.0).abs() < 1e-3, "disabled");
        assert!(s.failure_configure(None, None, Some(6.0)).is_err());
    }

    #[test]
    fn cyclic_faults_recover() {
        let s = Simulator::start(SimConfig {
            adda: AdDaBackend::Emulated { loopback: false },
            failure_delay: 0.03,
            failure_duration: 0.03,
            ..SimConfig::default()
        });
        s.dac_write(0, 1.0).unwrap();
        std::thread::sleep(Duration::from_millis(40));
        assert!(s.failure_status().active);
        std::thread::sleep(Duration::from_millis(30));
        let st = s.failure_status();
        assert!(!st.active && st.cycle_count >= 1);
    }

    #[test]
    fn gpio() {
        let (s, _) = sim("gpio");
        s.gpio_configure(3, true, false).unwrap();
        s.gpio_write(3, true).unwrap();
        assert_eq!(s.gpio_read(3).unwrap(), (true, true));
        s.emulate_gpio_inputs(1 << 9).unwrap();
        assert_eq!(s.gpio_read(9).unwrap(), (true, false));
        s.gpio_write_port("A", 0xF0).unwrap();
        assert_eq!(
            s.gpio_port("A").unwrap(),
            (0x00, 0xF7),
            "only pin 3 is an output and its latch bit is 0 now"
        );
        assert!(matches!(s.gpio_write(20, true), Err(SimError::Invalid(_))));
        assert!(matches!(s.gpio_port("C"), Err(SimError::Invalid(_))));
    }

    #[test]
    fn disabled_subsystems() {
        let s = Simulator::start(SimConfig::default());
        assert!(matches!(s.can_send(&CanMessage::new(1, &[])), Err(SimError::Unavailable(_))));
        assert!(matches!(s.adc_read(0), Err(SimError::Unavailable(_))));
        s.dac_write(1, 1.0).unwrap(); // remembered without hardware
        assert_eq!(s.dac_read(1).unwrap(), 1.0);
        assert_eq!(s.available(), (false, true, false, false));
    }
}
