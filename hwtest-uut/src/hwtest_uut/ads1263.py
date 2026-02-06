"""ADS1263 32-bit ADC driver for Waveshare High-Precision AD HAT.

This module provides a driver for the ADS1263 32-bit delta-sigma ADC commonly
found on Waveshare High-Precision AD/DA HAT boards. Key features:

- 10-channel 32-bit ADC with up to 38.4 kSPS sample rate
- Programmable gain amplifier (1x to 32x)
- Internal 2.5V reference voltage
- Supports both differential and single-ended inputs

This driver uses software-controlled chip select (GPIO22) to allow
coexistence with other SPI devices (like MCP2515 CAN controller)
that use hardware chip selects.

Example:
    >>> adc = Ads1263(Ads1263Config(spi_bus=0, spi_device=1))
    >>> adc.open()
    >>> voltage = adc.read_voltage(channel=0)
    >>> print(f"Channel 0: {voltage:.6f}V")
    >>> adc.close()
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class Ads1263Register(IntEnum):
    """ADS1263 register addresses.

    Complete register map for the ADS1263 ADC. Registers control ADC
    configuration, input multiplexing, calibration, and GPIO functions.
    """

    ID = 0x00
    POWER = 0x01
    INTERFACE = 0x02
    MODE0 = 0x03
    MODE1 = 0x04
    MODE2 = 0x05
    INPMUX = 0x06
    OFCAL0 = 0x07
    OFCAL1 = 0x08
    OFCAL2 = 0x09
    FSCAL0 = 0x0A
    FSCAL1 = 0x0B
    FSCAL2 = 0x0C
    IDACMUX = 0x0D
    IDACMAG = 0x0E
    REFMUX = 0x0F
    TDACP = 0x10
    TDACN = 0x11
    GPIOCON = 0x12
    GPIODIR = 0x13
    GPIODAT = 0x14
    ADC2CFG = 0x15
    ADC2MUX = 0x16
    ADC2OFC0 = 0x17
    ADC2OFC1 = 0x18
    ADC2FSC0 = 0x19
    ADC2FSC1 = 0x1A


class Ads1263Command(IntEnum):
    """ADS1263 SPI command opcodes.

    Command bytes sent over SPI to control ADC operation, read data,
    perform calibration, and access registers.
    """

    NOP = 0x00
    RESET = 0x06
    START1 = 0x08
    STOP1 = 0x0A
    START2 = 0x0C
    STOP2 = 0x0E
    RDATA1 = 0x12
    RDATA2 = 0x14
    SYOCAL1 = 0x16
    SYGCAL1 = 0x17
    SFOCAL1 = 0x19
    SYOCAL2 = 0x1B
    SYGCAL2 = 0x1C
    SFOCAL2 = 0x1E
    RREG = 0x20
    WREG = 0x40


class Ads1263Gain(IntEnum):
    """Programmable gain amplifier (PGA) settings.

    Higher gain values provide better resolution for small signals but
    reduce the full-scale input range (VREF/gain).

    Attributes:
        GAIN_1: Unity gain (1x), full scale = +/- VREF.
        GAIN_2: 2x gain, full scale = +/- VREF/2.
        GAIN_4: 4x gain, full scale = +/- VREF/4.
        GAIN_8: 8x gain, full scale = +/- VREF/8.
        GAIN_16: 16x gain, full scale = +/- VREF/16.
        GAIN_32: 32x gain, full scale = +/- VREF/32.
    """

    GAIN_1 = 0
    GAIN_2 = 1
    GAIN_4 = 2
    GAIN_8 = 3
    GAIN_16 = 4
    GAIN_32 = 5


class Ads1263DataRate(IntEnum):
    """ADC1 data rate settings in samples per second.

    Higher data rates provide faster conversions but with more noise.
    Lower data rates provide better noise rejection and accuracy.

    Attributes:
        SPS_2_5: 2.5 samples per second (lowest noise).
        SPS_5: 5 samples per second.
        SPS_10: 10 samples per second.
        SPS_16_6: 16.6 samples per second.
        SPS_20: 20 samples per second.
        SPS_50: 50 samples per second.
        SPS_60: 60 samples per second.
        SPS_100: 100 samples per second.
        SPS_400: 400 samples per second.
        SPS_1200: 1200 samples per second.
        SPS_2400: 2400 samples per second.
        SPS_4800: 4800 samples per second.
        SPS_7200: 7200 samples per second.
        SPS_14400: 14400 samples per second.
        SPS_19200: 19200 samples per second.
        SPS_38400: 38400 samples per second (fastest).
    """

    SPS_2_5 = 0
    SPS_5 = 1
    SPS_10 = 2
    SPS_16_6 = 3
    SPS_20 = 4
    SPS_50 = 5
    SPS_60 = 6
    SPS_100 = 7
    SPS_400 = 8
    SPS_1200 = 9
    SPS_2400 = 10
    SPS_4800 = 11
    SPS_7200 = 12
    SPS_14400 = 13
    SPS_19200 = 14
    SPS_38400 = 15


@dataclass(frozen=True)
class Ads1263Config:
    """Configuration for the ADS1263 ADC.

    Immutable configuration specifying SPI bus, GPIO pins, and ADC parameters.
    Default pin assignments are configured for coexistence with a CAN HAT
    that uses SPI0 CE0.

    Attributes:
        spi_bus: SPI bus number (default 0).
        spi_device: SPI device number (default 1 to avoid conflict with CAN).
        cs_pin: GPIO pin (BCM) for software chip select (default 22).
        drdy_pin: GPIO pin (BCM) for data ready signal (default 17).
        reset_pin: GPIO pin (BCM) for hardware reset (default 18).
        vref: Reference voltage in volts (default 2.5V internal).
        gain: Programmable gain amplifier setting.
        data_rate: Sample rate setting.
    """

    spi_bus: int = 0
    spi_device: int = 1
    cs_pin: int = 22
    drdy_pin: int = 17
    reset_pin: int = 18
    vref: float = 2.5
    gain: Ads1263Gain = Ads1263Gain.GAIN_1
    data_rate: Ads1263DataRate = Ads1263DataRate.SPS_400


class Ads1263:
    """Driver for the ADS1263 32-bit ADC.

    Uses software-controlled chip select to allow sharing the SPI bus
    with other devices (e.g., MCP2515 CAN controller on spidev0.0).

    The device must be opened before use and should be closed when done
    to release SPI and GPIO resources.

    Attributes:
        AIN0-AIN9: Input channel constants (0-9).
        AINCOM: Common/ground reference input (10).
        TEMP_SENSOR_P: Positive temperature sensor input (11).
        TEMP_SENSOR_N: Negative temperature sensor input (12).
        VAVDD: Analog supply voltage input (13).
        VAVSS: Analog ground reference input (14).
        FLOAT: Floating/disconnected input (15).
        config: The ADC configuration (read-only).
        is_open: True if the device is currently open.

    Example:
        >>> adc = Ads1263(Ads1263Config(gain=Ads1263Gain.GAIN_1))
        >>> adc.open()
        >>> voltage = adc.read_voltage(channel=0)
        >>> diff = adc.read_differential(positive=0, negative=1)
        >>> adc.close()
    """

    # Input multiplexer channel definitions
    AIN0 = 0
    AIN1 = 1
    AIN2 = 2
    AIN3 = 3
    AIN4 = 4
    AIN5 = 5
    AIN6 = 6
    AIN7 = 7
    AIN8 = 8
    AIN9 = 9
    AINCOM = 10
    TEMP_SENSOR_P = 11
    TEMP_SENSOR_N = 12
    VAVDD = 13
    VAVSS = 14
    FLOAT = 15

    def __init__(
        self,
        config: Ads1263Config | None = None,
        spi: Any | None = None,
        gpio: Any | None = None,
    ) -> None:
        """Initialize the ADS1263 driver.

        Args:
            config: ADC configuration. Uses defaults if None.
            spi: Optional pre-configured SPI device object for testing/mocking.
            gpio: Optional GPIO module for testing/mocking.
        """
        self._config = config or Ads1263Config()
        self._spi = spi
        self._gpio = gpio
        self._opened = False

    @property
    def config(self) -> Ads1263Config:
        """Return the ADC configuration."""
        return self._config

    @property
    def is_open(self) -> bool:
        """Return True if the device is open."""
        return self._opened

    def open(self) -> None:
        """Open the SPI bus and initialize the ADC.

        Raises:
            RuntimeError: If the device is already open.
            ImportError: If spidev or RPi.GPIO is not available.
        """
        if self._opened:
            raise RuntimeError("Device already open")

        # Initialize GPIO
        if self._gpio is None:
            try:
                import RPi.GPIO as GPIO

                self._gpio = GPIO
            except ImportError as exc:
                raise ImportError(
                    "RPi.GPIO library is not installed. "
                    "Install with: pip install RPi.GPIO"
                ) from exc

        self._gpio.setmode(self._gpio.BCM)
        self._gpio.setwarnings(False)
        self._gpio.setup(self._config.cs_pin, self._gpio.OUT, initial=self._gpio.HIGH)
        self._gpio.setup(self._config.drdy_pin, self._gpio.IN)
        self._gpio.setup(self._config.reset_pin, self._gpio.OUT, initial=self._gpio.HIGH)

        # Initialize SPI
        if self._spi is None:
            try:
                import spidev

                self._spi = spidev.SpiDev()
                self._spi.open(self._config.spi_bus, self._config.spi_device)
                self._spi.max_speed_hz = 1920000
                self._spi.mode = 1
                self._spi.no_cs = True  # Use software CS
            except ImportError as exc:
                raise ImportError(
                    "spidev library is not installed. "
                    "Install with: pip install spidev"
                ) from exc

        self._opened = True

        # Hardware reset
        self._reset()

        # Initialize ADC
        self._init_adc()

    def close(self) -> None:
        """Close the SPI bus and release GPIO resources.

        Cleans up SPI device and GPIO pins. Safe to call multiple times
        or on an already closed device.
        """
        if not self._opened:
            return

        if self._spi is not None:
            try:
                self._spi.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        if self._gpio is not None:
            try:
                self._gpio.cleanup([
                    self._config.cs_pin,
                    self._config.drdy_pin,
                    self._config.reset_pin,
                ])
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        self._opened = False

    def _reset(self) -> None:
        """Perform hardware reset via the reset pin.

        Pulses the reset pin low for 100ms then waits 500ms for the
        device to complete its power-on sequence.
        """
        assert self._gpio is not None
        self._gpio.output(self._config.reset_pin, self._gpio.LOW)
        time.sleep(0.1)
        self._gpio.output(self._config.reset_pin, self._gpio.HIGH)
        time.sleep(0.5)

    def _init_adc(self) -> None:
        """Initialize the ADC with configured settings.

        Sends a software reset command and configures the MODE2 register
        with the specified gain and data rate settings.
        """
        # Send reset command
        self._cs_low()
        self._spi_write([Ads1263Command.RESET])
        self._cs_high()
        time.sleep(0.1)

        # Configure MODE2 register (gain and data rate)
        mode2 = (self._config.gain << 4) | self._config.data_rate
        self._write_register(Ads1263Register.MODE2, mode2)

    def _cs_low(self) -> None:
        """Assert chip select (active low) with settling delay."""
        assert self._gpio is not None
        self._gpio.output(self._config.cs_pin, self._gpio.LOW)
        time.sleep(0.00001)

    def _cs_high(self) -> None:
        """Deassert chip select (set high)."""
        assert self._gpio is not None
        self._gpio.output(self._config.cs_pin, self._gpio.HIGH)

    def _spi_write(self, data: list[int]) -> list[int]:
        """Write data to SPI and return response.

        Args:
            data: List of bytes to write.

        Returns:
            List of bytes read during the transfer.
        """
        assert self._spi is not None
        result: list[int] = self._spi.xfer2(data)
        return result

    def _wait_drdy(self, timeout: float = 2.0) -> bool:
        """Wait for DRDY to go low (data ready).

        Args:
            timeout: Maximum time to wait in seconds.

        Returns:
            True if data ready, False if timeout.
        """
        assert self._gpio is not None
        start = time.time()
        while self._gpio.input(self._config.drdy_pin) == 1:
            if time.time() - start > timeout:
                return False
        return True

    def _read_register(self, register: int) -> int:
        """Read a single register.

        Args:
            register: Register address.

        Returns:
            Register value.
        """
        self._cs_low()
        self._spi_write([Ads1263Command.RREG | register, 0x00])
        time.sleep(0.00001)
        result = self._spi_write([0xFF])[0]
        self._cs_high()
        return result

    def _write_register(self, register: int, value: int) -> None:
        """Write a single register.

        Args:
            register: Register address.
            value: Value to write.
        """
        self._cs_low()
        self._spi_write([Ads1263Command.WREG | register, 0x00, value])
        self._cs_high()
        time.sleep(0.001)

    def get_chip_id(self) -> int:
        """Read the chip ID register.

        Returns:
            Chip ID (should be 0x01 for ADS1263).

        Raises:
            RuntimeError: If device is not open.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        return self._read_register(Ads1263Register.ID)

    def set_channel(self, positive: int, negative: int = AINCOM) -> None:
        """Set the input multiplexer channels.

        Args:
            positive: Positive input channel (AIN0-AIN9, AINCOM, etc.).
            negative: Negative input channel (default AINCOM for single-ended).

        Raises:
            RuntimeError: If device is not open.
            ValueError: If channel is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if not 0 <= positive <= 15:
            raise ValueError(f"positive channel must be 0-15, got {positive}")
        if not 0 <= negative <= 15:
            raise ValueError(f"negative channel must be 0-15, got {negative}")

        inpmux = (positive << 4) | negative
        self._write_register(Ads1263Register.INPMUX, inpmux)

    def read_raw(self) -> int:
        """Read raw ADC value (32-bit signed).

        Returns:
            Raw ADC reading as signed 32-bit integer.

        Raises:
            RuntimeError: If device is not open or read times out.
        """
        if not self._opened:
            raise RuntimeError("Device not open")

        # Start conversion
        self._cs_low()
        self._spi_write([Ads1263Command.START1])
        self._cs_high()

        # Wait for data ready
        if not self._wait_drdy():
            raise RuntimeError("ADC conversion timeout")

        # Read data
        self._cs_low()
        data = self._spi_write([Ads1263Command.RDATA1, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF])
        self._cs_high()

        # Parse 32-bit value (bytes 2-5, skipping status byte)
        raw = (data[2] << 24) | (data[3] << 16) | (data[4] << 8) | data[5]

        # Convert to signed
        if raw & 0x80000000:
            raw -= 0x100000000

        return raw

    def read_voltage(self, channel: int | None = None) -> float:
        """Read voltage from a channel.

        Args:
            channel: Input channel (0-9). If None, uses current channel setting.

        Returns:
            Measured voltage in volts.

        Raises:
            RuntimeError: If device is not open.
            ValueError: If channel is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")

        if channel is not None:
            if not 0 <= channel <= 9:
                raise ValueError(f"channel must be 0-9, got {channel}")
            self.set_channel(channel, self.AINCOM)

        raw = self.read_raw()

        # Convert to voltage
        # Full scale is ±VREF/gain
        gain_factor = 1 << self._config.gain
        voltage = (raw / 0x7FFFFFFF) * (self._config.vref / gain_factor)

        return voltage

    def read_differential(self, positive: int, negative: int) -> float:
        """Read differential voltage between two channels.

        Args:
            positive: Positive input channel (0-9).
            negative: Negative input channel (0-9).

        Returns:
            Measured differential voltage in volts.

        Raises:
            RuntimeError: If device is not open.
            ValueError: If channel is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if not 0 <= positive <= 9:
            raise ValueError(f"positive channel must be 0-9, got {positive}")
        if not 0 <= negative <= 9:
            raise ValueError(f"negative channel must be 0-9, got {negative}")

        self.set_channel(positive, negative)
        raw = self.read_raw()

        gain_factor = 1 << self._config.gain
        voltage = (raw / 0x7FFFFFFF) * (self._config.vref / gain_factor)

        return voltage

    def read_all_channels(self) -> list[float]:
        """Read all 10 single-ended input channels.

        Returns:
            List of voltages for channels 0-9.

        Raises:
            RuntimeError: If device is not open.
        """
        if not self._opened:
            raise RuntimeError("Device not open")

        voltages = []
        for channel in range(10):
            voltages.append(self.read_voltage(channel))

        return voltages
