//! MCP23017 16-bit I2C GPIO expander (pins 0-7 = port A, 8-15 = port B).

use std::sync::{Arc, Mutex, PoisonError};

use crate::error::{CommsExt, InstrumentError, Result};

/// Registers (IOCON.BANK = 0).
pub mod reg {
    #![allow(missing_docs)]
    pub const IODIRA: u8 = 0x00;
    pub const IODIRB: u8 = 0x01;
    pub const GPPUA: u8 = 0x0C;
    pub const GPPUB: u8 = 0x0D;
    pub const GPIOA: u8 = 0x12;
    pub const GPIOB: u8 = 0x13;
    pub const OLATA: u8 = 0x14;
    pub const OLATB: u8 = 0x15;
}

/// Register access on the I2C bus.
pub trait RegisterBus: Send {
    /// Reads a register.
    ///
    /// # Errors
    /// Bus error.
    fn read(&mut self, register: u8) -> Result<u8>;
    /// Writes a register.
    ///
    /// # Errors
    /// Bus error.
    fn write(&mut self, register: u8, value: u8) -> Result<()>;
}

struct LinuxI2c {
    dev: i2cdev::linux::LinuxI2CDevice,
    name: String,
}

impl RegisterBus for LinuxI2c {
    fn read(&mut self, register: u8) -> Result<u8> {
        use i2cdev::core::I2CDevice;
        self.dev.smbus_read_byte_data(register).comms(&self.name)
    }
    fn write(&mut self, register: u8, value: u8) -> Result<()> {
        use i2cdev::core::I2CDevice;
        self.dev.smbus_write_byte_data(register, value).comms(&self.name)
    }
}

/// Opens `/dev/i2c-{bus}` at `address` (0x20-0x27).
///
/// # Errors
/// [`InstrumentError::Comms`] if the bus cannot be opened.
pub fn linux_bus(bus: u32, address: u16) -> Result<Box<dyn RegisterBus>> {
    let path = format!("/dev/i2c-{bus}");
    let name = format!("{path} @ {address:#04x}");
    let dev = i2cdev::linux::LinuxI2CDevice::new(&path, address)
        .comms(format!("{name} (is I2C enabled? dtparam=i2c_arm=on)"))?;
    Ok(Box::new(LinuxI2c { dev, name }))
}

/// Register-level emulator; `set_external` drives input pins.
#[derive(Clone, Default)]
pub struct EmulatedMcp23017 {
    state: Arc<Mutex<Emu>>,
}

#[derive(Default)]
struct Emu {
    regs: [u8; 0x16],
    external: u16,
}

impl EmulatedMcp23017 {
    /// Power-on state: all pins inputs.
    pub fn new() -> Self {
        let e = Self::default();
        {
            let mut s = e.state.lock().unwrap_or_else(PoisonError::into_inner);
            s.regs[usize::from(reg::IODIRA)] = 0xFF;
            s.regs[usize::from(reg::IODIRB)] = 0xFF;
        }
        e
    }

    /// Level an external circuit drives on input pins (bit per pin).
    pub fn set_external(&self, levels: u16) {
        self.state.lock().unwrap_or_else(PoisonError::into_inner).external = levels;
    }

    /// A register-bus handle for the driver.
    pub fn bus(&self) -> Box<dyn RegisterBus> {
        Box::new(self.clone())
    }
}

impl RegisterBus for EmulatedMcp23017 {
    fn read(&mut self, register: u8) -> Result<u8> {
        let s = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        let r = usize::from(register);
        if r >= s.regs.len() {
            return Err(InstrumentError::Comms(format!("MCP23017: no register {register:#x}")));
        }
        let port = |dir: u8, olat: u8, ext: u8, pu: u8| {
            (s.regs[usize::from(olat)] & !s.regs[usize::from(dir)])
                | ((ext | pu) & s.regs[usize::from(dir)])
        };
        let [ext_a, ext_b] = s.external.to_le_bytes();
        Ok(match register {
            reg::GPIOA => port(reg::IODIRA, reg::OLATA, ext_a, 0),
            reg::GPIOB => port(reg::IODIRB, reg::OLATB, ext_b, 0),
            _ => s.regs[r],
        })
    }

    fn write(&mut self, register: u8, value: u8) -> Result<()> {
        let mut s = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        let r = match register {
            reg::GPIOA => reg::OLATA,
            reg::GPIOB => reg::OLATB,
            x => x,
        };
        *s.regs.get_mut(usize::from(r)).ok_or_else(|| {
            InstrumentError::Comms(format!("MCP23017: no register {register:#x}"))
        })? = value;
        Ok(())
    }
}

/// The expander.
pub struct Mcp23017 {
    bus: Box<dyn RegisterBus>,
}

fn pin_bit(pin: u8) -> Result<(u8, u8)> {
    match pin {
        0..=7 => Ok((0, 1 << pin)),
        8..=15 => Ok((1, 1 << (pin - 8))),
        _ => Err(InstrumentError::OutOfRange(format!("MCP23017 pin must be 0-15, got {pin}"))),
    }
}

/// Port letter to index.
///
/// # Errors
/// [`InstrumentError::OutOfRange`] for anything but A/B.
pub fn port_index(port: &str) -> Result<u8> {
    match port.to_ascii_uppercase().as_str() {
        "A" => Ok(0),
        "B" => Ok(1),
        _ => {
            Err(InstrumentError::OutOfRange(format!("MCP23017 port must be A or B, got {port:?}")))
        }
    }
}

impl Mcp23017 {
    /// Wraps a register bus and sets all pins to inputs (power-on state).
    ///
    /// # Errors
    /// Bus error (device not responding).
    pub fn open(mut bus: Box<dyn RegisterBus>) -> Result<Self> {
        bus.write(reg::IODIRA, 0xFF)?;
        bus.write(reg::IODIRB, 0xFF)?;
        Ok(Self { bus })
    }

    fn update(&mut self, register: u8, mask: u8, set: bool) -> Result<()> {
        let v = self.bus.read(register)?;
        self.bus.write(register, if set { v | mask } else { v & !mask })
    }

    /// Sets a pin's direction.
    ///
    /// # Errors
    /// Bad pin or bus error.
    pub fn set_direction(&mut self, pin: u8, output: bool) -> Result<()> {
        let (p, m) = pin_bit(pin)?;
        self.update(reg::IODIRA + p, m, !output)
    }

    /// Enables/disables a pin's pull-up.
    ///
    /// # Errors
    /// Bad pin or bus error.
    pub fn set_pullup(&mut self, pin: u8, enabled: bool) -> Result<()> {
        let (p, m) = pin_bit(pin)?;
        self.update(reg::GPPUA + p, m, enabled)
    }

    /// True if the pin is an output.
    ///
    /// # Errors
    /// Bad pin or bus error.
    pub fn is_output(&mut self, pin: u8) -> Result<bool> {
        let (p, m) = pin_bit(pin)?;
        Ok(self.bus.read(reg::IODIRA + p)? & m == 0)
    }

    /// Drives a pin (via the output latch).
    ///
    /// # Errors
    /// Bad pin or bus error.
    pub fn write_pin(&mut self, pin: u8, value: bool) -> Result<()> {
        let (p, m) = pin_bit(pin)?;
        self.update(reg::OLATA + p, m, value)
    }

    /// Reads a pin level.
    ///
    /// # Errors
    /// Bad pin or bus error.
    pub fn read_pin(&mut self, pin: u8) -> Result<bool> {
        let (p, m) = pin_bit(pin)?;
        Ok(self.bus.read(reg::GPIOA + p)? & m != 0)
    }

    /// Writes a port's output latch.
    ///
    /// # Errors
    /// Bus error.
    pub fn write_port(&mut self, port: u8, value: u8) -> Result<()> {
        self.bus.write(reg::OLATA + port, value)
    }

    /// Reads a port.
    ///
    /// # Errors
    /// Bus error.
    pub fn read_port(&mut self, port: u8) -> Result<u8> {
        self.bus.read(reg::GPIOA + port)
    }

    /// A port's direction mask (1 = input).
    ///
    /// # Errors
    /// Bus error.
    pub fn direction_mask(&mut self, port: u8) -> Result<u8> {
        self.bus.read(reg::IODIRA + port)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pins_and_ports() {
        let emu = EmulatedMcp23017::new();
        let mut g = Mcp23017::open(emu.bus()).unwrap();
        g.set_direction(3, true).unwrap();
        g.set_direction(12, true).unwrap();
        assert!(g.is_output(3).unwrap() && !g.is_output(4).unwrap());
        g.write_pin(3, true).unwrap();
        g.write_pin(12, true).unwrap();
        assert!(g.read_pin(3).unwrap());
        emu.set_external(1 << 5);
        assert!(g.read_pin(5).unwrap(), "input follows external level");
        assert!(!g.read_pin(6).unwrap());
        assert_eq!(g.read_port(0).unwrap(), 0b0010_1000);
        assert_eq!(g.read_port(1).unwrap(), 0b0001_0000);
        assert_eq!(g.direction_mask(0).unwrap(), 0b1111_0111);
        assert!(g.write_pin(16, true).is_err());
        assert_eq!(port_index("b").unwrap(), 1);
    }
}
