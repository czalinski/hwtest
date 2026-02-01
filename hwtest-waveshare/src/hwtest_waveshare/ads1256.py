"""ADS1256 24-bit ADC driver for Waveshare High-Precision AD/DA board.

The ADS1256 is a 24-bit delta-sigma ADC with:
- 8 single-ended or 4 differential input channels
- Programmable gain amplifier (PGA): 1, 2, 4, 8, 16, 32, 64
- Sample rates from 2.5 to 30,000 SPS
- Internal or external voltage reference (default 2.5V)
- SPI interface with DRDY (data ready) signal

Pin connections on the Waveshare High-Precision AD/DA board:
- SPI0: MOSI=GPIO10, MISO=GPIO9, SCLK=GPIO11
- CS: GPIO22 (directly controlled, active low)
- DRDY: GPIO17 (data ready, active low)
- RESET: GPIO18 (optional, active low)
- PDWN: GPIO27 (power down, active low - active by default)
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing import Protocol

    class SpiDevice(Protocol):
        """Protocol for SPI device interface (spidev compatible)."""

        def xfer2(self, data: list[int]) -> list[int]:
            """Transfer data over SPI and return received data."""
            ...

        def writebytes(self, data: list[int]) -> None:
            """Write bytes to SPI without reading."""
            ...

        def readbytes(self, n: int) -> list[int]:
            """Read n bytes from SPI."""
            ...

        def close(self) -> None:
            """Close the SPI device."""
            ...

    class GpioInterface(Protocol):
        """Protocol for GPIO interface (RPi.GPIO compatible)."""

        BCM: int
        IN: int
        OUT: int
        HIGH: int
        LOW: int

        def setmode(self, mode: int) -> None: ...

        def setup(self, pin: int, direction: int, initial: int = ...) -> None: ...

        def input(self, pin: int) -> int: ...

        def output(self, pin: int, value: int) -> None: ...

        def cleanup(self, pin: int | list[int] | None = None) -> None: ...


class Ads1256Gain(IntEnum):
    """Programmable gain amplifier settings."""

    GAIN_1 = 0b000  # ±5V input range (with 2.5V reference)
    GAIN_2 = 0b001  # ±2.5V
    GAIN_4 = 0b010  # ±1.25V
    GAIN_8 = 0b011  # ±0.625V
    GAIN_16 = 0b100  # ±312.5mV
    GAIN_32 = 0b101  # ±156.25mV
    GAIN_64 = 0b110  # ±78.125mV


class Ads1256DataRate(IntEnum):
    """Data rate settings (samples per second)."""

    SPS_30000 = 0xF0
    SPS_15000 = 0xE0
    SPS_7500 = 0xD0
    SPS_3750 = 0xC0
    SPS_2000 = 0xB0
    SPS_1000 = 0xA1
    SPS_500 = 0x92
    SPS_100 = 0x82
    SPS_60 = 0x72
    SPS_50 = 0x63
    SPS_30 = 0x53
    SPS_25 = 0x43
    SPS_15 = 0x33
    SPS_10 = 0x23
    SPS_5 = 0x13
    SPS_2_5 = 0x03


# Convert data rate enum to actual SPS values
DATA_RATE_VALUES: dict[Ads1256DataRate, float] = {
    Ads1256DataRate.SPS_30000: 30000.0,
    Ads1256DataRate.SPS_15000: 15000.0,
    Ads1256DataRate.SPS_7500: 7500.0,
    Ads1256DataRate.SPS_3750: 3750.0,
    Ads1256DataRate.SPS_2000: 2000.0,
    Ads1256DataRate.SPS_1000: 1000.0,
    Ads1256DataRate.SPS_500: 500.0,
    Ads1256DataRate.SPS_100: 100.0,
    Ads1256DataRate.SPS_60: 60.0,
    Ads1256DataRate.SPS_50: 50.0,
    Ads1256DataRate.SPS_30: 30.0,
    Ads1256DataRate.SPS_25: 25.0,
    Ads1256DataRate.SPS_15: 15.0,
    Ads1256DataRate.SPS_10: 10.0,
    Ads1256DataRate.SPS_5: 5.0,
    Ads1256DataRate.SPS_2_5: 2.5,
}


class _Ads1256Cmd:
    """ADS1256 SPI commands."""

    WAKEUP = 0x00
    RDATA = 0x01
    RDATAC = 0x03
    SDATAC = 0x0F
    RREG = 0x10
    WREG = 0x50
    SELFCAL = 0xF0
    SELFOCAL = 0xF1
    SELFGCAL = 0xF2
    SYSOCAL = 0xF3
    SYSGCAL = 0xF4
    SYNC = 0xFC
    STANDBY = 0xFD
    RESET = 0xFE


class _Ads1256Reg:
    """ADS1256 register addresses."""

    STATUS = 0x00
    MUX = 0x01
    ADCON = 0x02
    DRATE = 0x03
    IO = 0x04
    OFC0 = 0x05
    OFC1 = 0x06
    OFC2 = 0x07
    FSC0 = 0x08
    FSC1 = 0x09
    FSC2 = 0x0A


# Default GPIO pins for Waveshare board
_DEFAULT_CS_PIN = 22
_DEFAULT_DRDY_PIN = 17
_DEFAULT_RESET_PIN = 18
_DEFAULT_PDWN_PIN = 27


@dataclass(frozen=True)
class Ads1256Config:
    """Configuration for the ADS1256 ADC.

    Args:
        spi_bus: SPI bus number (0 or 1).
        spi_device: SPI device number (typically 0).
        cs_pin: GPIO pin for chip select (BCM numbering).
        drdy_pin: GPIO pin for data ready signal (BCM numbering).
        reset_pin: GPIO pin for reset (BCM numbering), or None to skip reset.
        gain: Programmable gain amplifier setting.
        data_rate: Sample rate setting.
        vref: Reference voltage in volts (typically 2.5V).
    """

    spi_bus: int = 0
    spi_device: int = 0
    cs_pin: int = _DEFAULT_CS_PIN
    drdy_pin: int = _DEFAULT_DRDY_PIN
    reset_pin: int | None = _DEFAULT_RESET_PIN
    gain: Ads1256Gain = Ads1256Gain.GAIN_1
    data_rate: Ads1256DataRate = Ads1256DataRate.SPS_100
    vref: float = 2.5


class Ads1256:
    """Low-level driver for the ADS1256 24-bit ADC.

    This class handles direct SPI communication with the ADS1256 chip.
    For higher-level instrument usage, see :class:`Ads1256Instrument`.

    Args:
        config: ADC configuration.
        spi: Optional SPI device (for testing). If None, uses spidev.
        gpio: Optional GPIO interface (for testing). If None, uses RPi.GPIO.
    """

    def __init__(
        self,
        config: Ads1256Config | None = None,
        spi: Any | None = None,
        gpio: Any | None = None,
    ) -> None:
        self._config = config or Ads1256Config()
        self._spi = spi
        self._gpio = gpio
        self._opened = False

    @property
    def config(self) -> Ads1256Config:
        """Return the ADC configuration."""
        return self._config

    @property
    def is_open(self) -> bool:
        """Return True if the device is open."""
        return self._opened

    @property
    def sample_rate(self) -> float:
        """Return the configured sample rate in Hz."""
        return DATA_RATE_VALUES[self._config.data_rate]

    def open(self) -> None:
        """Open the SPI device and initialize GPIO.

        Raises:
            RuntimeError: If the device is already open.
            ImportError: If spidev or RPi.GPIO is not available.
        """
        if self._opened:
            raise RuntimeError("Device already open")

        # Initialize GPIO
        if self._gpio is None:
            try:
                import RPi.GPIO as GPIO  # type: ignore[import-untyped]

                self._gpio = GPIO
            except ImportError as exc:
                raise ImportError(
                    "RPi.GPIO library is not installed. Install with: pip install RPi.GPIO"
                ) from exc

        self._gpio.setmode(self._gpio.BCM)
        self._gpio.setup(self._config.cs_pin, self._gpio.OUT, initial=self._gpio.HIGH)
        self._gpio.setup(self._config.drdy_pin, self._gpio.IN)
        if self._config.reset_pin is not None:
            self._gpio.setup(self._config.reset_pin, self._gpio.OUT, initial=self._gpio.HIGH)

        # Initialize SPI
        if self._spi is None:
            try:
                import spidev  # type: ignore[import-not-found]

                self._spi = spidev.SpiDev()
            except ImportError as exc:
                self._gpio.cleanup([self._config.cs_pin, self._config.drdy_pin])
                if self._config.reset_pin is not None:
                    self._gpio.cleanup([self._config.reset_pin])
                raise ImportError(
                    "spidev library is not installed. Install with: pip install spidev"
                ) from exc

        self._spi.open(self._config.spi_bus, self._config.spi_device)
        self._spi.max_speed_hz = 1920000  # 1.92 MHz (ADS1256 supports up to CLKIN/4 = 1.92MHz)
        self._spi.mode = 0b01  # CPOL=0, CPHA=1 (SPI mode 1)
        self._spi.lsbfirst = False

        self._opened = True

        # Reset and configure the device
        self._reset()
        self._configure()

    def close(self) -> None:
        """Close the SPI device and release GPIO."""
        if not self._opened:
            return

        if self._spi is not None:
            try:
                self._spi.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        if self._gpio is not None:
            pins = [self._config.cs_pin, self._config.drdy_pin]
            if self._config.reset_pin is not None:
                pins.append(self._config.reset_pin)
            try:
                self._gpio.cleanup(pins)
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        self._opened = False

    def _cs_low(self) -> None:
        """Assert chip select (active low)."""
        assert self._gpio is not None
        self._gpio.output(self._config.cs_pin, self._gpio.LOW)

    def _cs_high(self) -> None:
        """Deassert chip select."""
        assert self._gpio is not None
        self._gpio.output(self._config.cs_pin, self._gpio.HIGH)

    def _wait_drdy(self, timeout_s: float = 1.0) -> bool:
        """Wait for DRDY to go low (data ready).

        Args:
            timeout_s: Maximum time to wait in seconds.

        Returns:
            True if DRDY went low, False if timeout.
        """
        assert self._gpio is not None
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            if self._gpio.input(self._config.drdy_pin) == self._gpio.LOW:
                return True
            time.sleep(0.0001)  # 100us
        return False

    def _write_cmd(self, cmd: int) -> None:
        """Write a single command byte."""
        assert self._spi is not None
        self._spi.writebytes([cmd])

    def _read_reg(self, reg: int) -> int:
        """Read a single register.

        Args:
            reg: Register address (0x00-0x0A).

        Returns:
            Register value.
        """
        assert self._spi is not None
        self._cs_low()
        # RREG command: 0x10 | reg, then number of registers - 1
        self._spi.xfer2([_Ads1256Cmd.RREG | reg, 0x00])
        time.sleep(0.00001)  # t6 delay (50 * tCLKIN = 6.5us at 7.68MHz)
        result: list[int] = self._spi.readbytes(1)
        self._cs_high()
        return result[0]

    def _write_reg(self, reg: int, value: int) -> None:
        """Write a single register.

        Args:
            reg: Register address (0x00-0x0A).
            value: Value to write.
        """
        assert self._spi is not None
        self._cs_low()
        # WREG command: 0x50 | reg, then number of registers - 1, then data
        self._spi.xfer2([_Ads1256Cmd.WREG | reg, 0x00, value])
        self._cs_high()

    def _reset(self) -> None:
        """Reset the ADS1256."""
        assert self._gpio is not None
        if self._config.reset_pin is not None:
            # Hardware reset
            self._gpio.output(self._config.reset_pin, self._gpio.LOW)
            time.sleep(0.001)  # 1ms
            self._gpio.output(self._config.reset_pin, self._gpio.HIGH)
            time.sleep(0.001)  # Wait for reset to complete
        else:
            # Software reset
            self._cs_low()
            self._write_cmd(_Ads1256Cmd.RESET)
            self._cs_high()
            time.sleep(0.001)

        self._wait_drdy()

    def _configure(self) -> None:
        """Configure the ADS1256 with current settings."""
        # Stop continuous read mode if active
        self._cs_low()
        self._write_cmd(_Ads1256Cmd.SDATAC)
        self._cs_high()
        time.sleep(0.0001)

        # Write configuration registers
        # STATUS: buffer disabled, auto-cal disabled
        self._write_reg(_Ads1256Reg.STATUS, 0x00)

        # MUX: AIN0 positive, AINCOM negative (single-ended)
        self._write_reg(_Ads1256Reg.MUX, 0x08)

        # ADCON: Clock out off, sensor detect off, gain setting
        self._write_reg(_Ads1256Reg.ADCON, self._config.gain.value)

        # DRATE: Data rate
        self._write_reg(_Ads1256Reg.DRATE, self._config.data_rate.value)

        # Perform self-calibration
        self._cs_low()
        self._write_cmd(_Ads1256Cmd.SELFCAL)
        self._cs_high()
        self._wait_drdy(timeout_s=2.0)

    def _set_channel(self, positive: int, negative: int = 8) -> None:
        """Set the input multiplexer.

        Args:
            positive: Positive input channel (0-7 for AIN0-AIN7).
            negative: Negative input channel (0-7 for AIN0-AIN7, 8 for AINCOM).
        """
        if not 0 <= positive <= 7:
            raise ValueError(f"positive channel must be 0-7, got {positive}")
        if not 0 <= negative <= 8:
            raise ValueError(f"negative channel must be 0-8, got {negative}")

        mux_value = (positive << 4) | negative
        self._write_reg(_Ads1256Reg.MUX, mux_value)

        # Issue SYNC and WAKEUP to apply new settings
        self._cs_low()
        self._write_cmd(_Ads1256Cmd.SYNC)
        time.sleep(0.000004)  # t11 delay
        self._write_cmd(_Ads1256Cmd.WAKEUP)
        self._cs_high()

    def _read_adc_raw(self) -> int:
        """Read the current ADC value as raw 24-bit signed integer.

        Returns:
            Raw ADC value (-8388608 to 8388607).
        """
        assert self._spi is not None
        self._wait_drdy()

        self._cs_low()
        self._write_cmd(_Ads1256Cmd.RDATA)
        time.sleep(0.00001)  # t6 delay

        data: list[int] = self._spi.readbytes(3)
        self._cs_high()

        # Convert 3 bytes to signed 24-bit integer (two's complement)
        value = (data[0] << 16) | (data[1] << 8) | data[2]
        if value >= 0x800000:
            value -= 0x1000000

        return value

    def read_voltage(self, channel: int) -> float:
        """Read voltage from a single-ended channel.

        Args:
            channel: Input channel number (0-7).

        Returns:
            Voltage in volts.

        Raises:
            RuntimeError: If device is not open.
            ValueError: If channel is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if not 0 <= channel <= 7:
            raise ValueError(f"channel must be 0-7, got {channel}")

        self._set_channel(channel, negative=8)  # 8 = AINCOM
        raw = self._read_adc_raw()

        # Convert to voltage
        # Full scale is ±Vref/gain
        # 24-bit range is -8388608 to 8388607
        gain_value = 2**self._config.gain.value
        voltage = (raw * self._config.vref) / (8388607 * gain_value)

        return voltage

    def read_differential(self, positive: int, negative: int) -> float:
        """Read differential voltage between two channels.

        Args:
            positive: Positive input channel (0-7).
            negative: Negative input channel (0-7).

        Returns:
            Differential voltage in volts.

        Raises:
            RuntimeError: If device is not open.
            ValueError: If channels are invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if not 0 <= positive <= 7:
            raise ValueError(f"positive channel must be 0-7, got {positive}")
        if not 0 <= negative <= 7:
            raise ValueError(f"negative channel must be 0-7, got {negative}")

        self._set_channel(positive, negative)
        raw = self._read_adc_raw()

        gain_value = 2**self._config.gain.value
        voltage = (raw * self._config.vref) / (8388607 * gain_value)

        return voltage

    def read_all_channels(self) -> list[float]:
        """Read voltage from all 8 single-ended channels.

        Returns:
            List of 8 voltage values in volts.

        Raises:
            RuntimeError: If device is not open.
        """
        if not self._opened:
            raise RuntimeError("Device not open")

        voltages: list[float] = []
        for ch in range(8):
            voltages.append(self.read_voltage(ch))

        return voltages

    def get_chip_id(self) -> int:
        """Read the chip ID from the STATUS register.

        Returns:
            Chip ID (should be 0x03 for ADS1256).

        Raises:
            RuntimeError: If device is not open.
        """
        if not self._opened:
            raise RuntimeError("Device not open")

        status = self._read_reg(_Ads1256Reg.STATUS)
        return (status >> 4) & 0x0F
