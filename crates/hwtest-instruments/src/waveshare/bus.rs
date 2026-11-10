//! SPI and GPIO abstractions plus Linux implementations.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use gpiocdev::line::Value;
use spidev::{SpiModeFlags, Spidev, SpidevOptions, SpidevTransfer};

use crate::error::{CommsExt, InstrumentError, Result};

/// A SPI device (chip select handled separately via GPIO).
pub trait SpiBus: Send {
    /// Writes bytes.
    ///
    /// # Errors
    /// Bus failure.
    fn write(&mut self, data: &[u8]) -> Result<()>;
    /// Reads `n` bytes (clocking out zeros).
    ///
    /// # Errors
    /// Bus failure.
    fn read(&mut self, n: usize) -> Result<Vec<u8>>;
}

/// GPIO lines by BCM number.
pub trait GpioLines: Send {
    /// Claims an output line.
    ///
    /// # Errors
    /// Line unavailable.
    fn setup_output(&mut self, pin: u32, high: bool) -> Result<()>;
    /// Claims an input line.
    ///
    /// # Errors
    /// Line unavailable.
    fn setup_input(&mut self, pin: u32) -> Result<()>;
    /// Drives an output.
    ///
    /// # Errors
    /// Line not claimed as output.
    fn set(&mut self, pin: u32, high: bool) -> Result<()>;
    /// Reads a line.
    ///
    /// # Errors
    /// Line not claimed.
    fn get(&mut self, pin: u32) -> Result<bool>;
}

/// A SPI device with its GPIO lines; shared by the chips on one board.
pub struct Bus {
    /// SPI device.
    pub spi: Box<dyn SpiBus>,
    /// GPIO lines.
    pub gpio: Box<dyn GpioLines>,
}

/// Shared bus handle.
pub type SharedBus = Arc<Mutex<Bus>>;

/// Locks a shared bus.
pub fn lock(bus: &SharedBus) -> std::sync::MutexGuard<'_, Bus> {
    bus.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

/// Busy-wait-free short delay.
pub fn delay_us(us: u64) {
    std::thread::sleep(Duration::from_micros(us));
}

struct LinuxSpi {
    dev: Spidev,
    path: String,
}

impl SpiBus for LinuxSpi {
    fn write(&mut self, data: &[u8]) -> Result<()> {
        self.dev.write_all(data).comms(&self.path)
    }
    fn read(&mut self, n: usize) -> Result<Vec<u8>> {
        let mut buf = vec![0u8; n];
        self.dev.read_exact(&mut buf).comms(&self.path)?;
        Ok(buf)
    }
}

impl LinuxSpi {
    #[allow(dead_code)]
    fn transfer(&mut self, tx: &[u8]) -> Result<Vec<u8>> {
        let mut rx = vec![0u8; tx.len()];
        let mut t = SpidevTransfer::read_write(tx, &mut rx);
        self.dev.transfer(&mut t).comms(&self.path)?;
        Ok(rx)
    }
}

struct LinuxGpio {
    chip: String,
    lines: HashMap<u32, gpiocdev::Request>,
}

impl LinuxGpio {
    fn line(&mut self, pin: u32) -> Result<&mut gpiocdev::Request> {
        self.lines
            .get_mut(&pin)
            .ok_or_else(|| InstrumentError::State(format!("GPIO{pin} not configured")))
    }
}

impl GpioLines for LinuxGpio {
    fn setup_output(&mut self, pin: u32, high: bool) -> Result<()> {
        let req = gpiocdev::Request::builder()
            .on_chip(&self.chip)
            .with_consumer("hwtest")
            .with_line(pin)
            .as_output(if high { Value::Active } else { Value::Inactive })
            .request()
            .comms(format!("{} GPIO{pin}", self.chip))?;
        self.lines.insert(pin, req);
        Ok(())
    }
    fn setup_input(&mut self, pin: u32) -> Result<()> {
        let req = gpiocdev::Request::builder()
            .on_chip(&self.chip)
            .with_consumer("hwtest")
            .with_line(pin)
            .as_input()
            .request()
            .comms(format!("{} GPIO{pin}", self.chip))?;
        self.lines.insert(pin, req);
        Ok(())
    }
    fn set(&mut self, pin: u32, high: bool) -> Result<()> {
        let chip = self.chip.clone();
        self.line(pin)?
            .set_value(pin, if high { Value::Active } else { Value::Inactive })
            .comms(format!("{chip} GPIO{pin}"))
    }
    fn get(&mut self, pin: u32) -> Result<bool> {
        let chip = self.chip.clone();
        Ok(self.line(pin)?.value(pin).comms(format!("{chip} GPIO{pin}"))? == Value::Active)
    }
}

/// Opens `/dev/spidev{bus}.{device}` (SPI mode 1) and `gpio_chip`.
///
/// # Errors
/// [`InstrumentError::Comms`] if the devices cannot be opened (SPI disabled,
/// permissions, wrong chip).
pub fn linux_bus(
    spi_bus: u32,
    spi_device: u32,
    speed_hz: u32,
    gpio_chip: &str,
) -> Result<SharedBus> {
    let path = format!("/dev/spidev{spi_bus}.{spi_device}");
    let mut dev = Spidev::open(&path).comms(format!("{path} (is SPI enabled? dtparam=spi=on)"))?;
    let opts = SpidevOptions::new()
        .bits_per_word(8)
        .max_speed_hz(speed_hz)
        .mode(SpiModeFlags::SPI_MODE_1)
        .build();
    dev.configure(&opts).comms(&path)?;
    Ok(Arc::new(Mutex::new(Bus {
        spi: Box::new(LinuxSpi { dev, path }),
        gpio: Box::new(LinuxGpio { chip: gpio_chip.to_string(), lines: HashMap::new() }),
    })))
}
