//! Waveshare High-Precision AD/DA board (ADS1256 ADC + DAC8532 DAC) on SPI.
//!
//! Hardware access goes through [`bus::SpiBus`] and [`bus::GpioLines`];
//! [`bus::linux_bus`] uses `/dev/spidevB.D` and `/dev/gpiochipN`, and
//! [`emulator::EmulatedAdDa`] models both chips at the SPI command level.

pub mod adda;
pub mod ads1256;
pub mod bus;
pub mod dac8532;
pub mod emulator;
