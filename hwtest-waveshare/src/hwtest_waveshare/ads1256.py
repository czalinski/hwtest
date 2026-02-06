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

from hwtest_waveshare.gpio import Gpio, INPUT, OUTPUT, HIGH, LOW

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


class Ads1256Gain(IntEnum):
    """Programmable gain amplifier (PGA) settings for the ADS1256.

    The gain setting determines the input voltage range. With a 2.5V
    reference voltage, the input ranges are as shown below.

    Attributes:
        GAIN_1: Unity gain, +/-5V input range.
        GAIN_2: 2x gain, +/-2.5V input range.
        GAIN_4: 4x gain, +/-1.25V input range.
        GAIN_8: 8x gain, +/-625mV input range.
        GAIN_16: 16x gain, +/-312.5mV input range.
        GAIN_32: 32x gain, +/-156.25mV input range.
        GAIN_64: 64x gain, +/-78.125mV input range.
    """

    GAIN_1 = 0b000  # ±5V input range (with 2.5V reference)
    GAIN_2 = 0b001  # ±2.5V
    GAIN_4 = 0b010  # ±1.25V
    GAIN_8 = 0b011  # ±0.625V
    GAIN_16 = 0b100  # ±312.5mV
    GAIN_32 = 0b101  # ±156.25mV
    GAIN_64 = 0b110  # ±78.125mV


class Ads1256DataRate(IntEnum):
    """Data rate settings (samples per second) for the ADS1256.

    Higher data rates provide faster sampling but lower resolution due to
    reduced oversampling. For high-precision measurements, use lower rates.

    Note:
        The actual rate when scanning multiple channels will be lower than
        the configured rate because each channel switch requires settling time.
    """

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


#: Mapping from data rate enum values to actual samples per second.
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
    """ADS1256 SPI command bytes.

    These are the command bytes sent over SPI to control the ADS1256.
    Most commands are single-byte; register read/write commands are
    followed by additional bytes specifying the register address and count.
    """

    WAKEUP = 0x00    # Exit standby mode
    RDATA = 0x01     # Read single conversion result
    RDATAC = 0x03    # Read data continuously
    SDATAC = 0x0F    # Stop continuous read mode
    RREG = 0x10      # Read register (OR with register address)
    WREG = 0x50      # Write register (OR with register address)
    SELFCAL = 0xF0   # Self-offset and gain calibration
    SELFOCAL = 0xF1  # Self-offset calibration
    SELFGCAL = 0xF2  # Self-gain calibration
    SYSOCAL = 0xF3   # System offset calibration
    SYSGCAL = 0xF4   # System gain calibration
    SYNC = 0xFC      # Synchronize A/D conversion
    STANDBY = 0xFD   # Enter standby mode
    RESET = 0xFE     # Reset to power-up values


class _Ads1256Reg:
    """ADS1256 register addresses.

    These are the internal register addresses for configuring and
    reading status from the ADS1256.
    """

    STATUS = 0x00  # Status register (read-only chip ID, DRDY, buffer)
    MUX = 0x01     # Input multiplexer control
    ADCON = 0x02   # A/D control (clock, gain)
    DRATE = 0x03   # Data rate control
    IO = 0x04      # GPIO direction and state
    OFC0 = 0x05    # Offset calibration byte 0
    OFC1 = 0x06    # Offset calibration byte 1
    OFC2 = 0x07    # Offset calibration byte 2
    FSC0 = 0x08    # Full-scale calibration byte 0
    FSC1 = 0x09    # Full-scale calibration byte 1
    FSC2 = 0x0A    # Full-scale calibration byte 2


# Default GPIO pins for Waveshare High-Precision AD/DA board
_DEFAULT_CS_PIN = 22      # Chip select (directly controlled, not SPI CE)
_DEFAULT_DRDY_PIN = 17    # Data ready (active low)
_DEFAULT_RESET_PIN = 18   # Hardware reset (active low)
_DEFAULT_PDWN_PIN = 27    # Power down (active low, normally high)


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
        """Initialize the ADS1256 driver.

        Args:
            config: ADC configuration. If None, uses default configuration.
            spi: Optional SPI device for testing. If None, uses spidev.
            gpio: Optional GPIO interface for testing. If None, uses lgpio.
        """
        self._config = config or Ads1256Config()
        self._spi = spi
        self._gpio = gpio
        self._opened = False

    @property
    def config(self) -> Ads1256Config:
        """The ADC configuration.

        Returns:
            Frozen configuration dataclass.
        """
        return self._config

    @property
    def is_open(self) -> bool:
        """Whether the device is open and ready for operations.

        Returns:
            True if open() has been called successfully.
        """
        return self._opened

    @property
    def sample_rate(self) -> float:
        """The configured sample rate in samples per second.

        Returns:
            Sample rate in Hz.
        """
        return DATA_RATE_VALUES[self._config.data_rate]

    def open(self) -> None:
        """Open the SPI device and initialize GPIO.

        Raises:
            RuntimeError: If the device is already open.
            ImportError: If spidev or lgpio is not available.
        """
        if self._opened:
            raise RuntimeError("Device already open")

        # Initialize GPIO using lgpio (Pi 5 compatible)
        if self._gpio is None:
            self._gpio = Gpio()
            self._gpio.open()

        self._gpio.setup(self._config.cs_pin, OUTPUT, initial=HIGH)
        self._gpio.setup(self._config.drdy_pin, INPUT)
        if self._config.reset_pin is not None:
            self._gpio.setup(self._config.reset_pin, OUTPUT, initial=HIGH)

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
        """Close the SPI device and release GPIO resources.

        Safe to call multiple times. Errors during cleanup are silently ignored.
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
                self._gpio.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        self._opened = False

    def _cs_low(self) -> None:
        """Assert chip select (drive low to select device)."""
        assert self._gpio is not None
        self._gpio.output(self._config.cs_pin, LOW)

    def _cs_high(self) -> None:
        """Deassert chip select (drive high to deselect device)."""
        assert self._gpio is not None
        self._gpio.output(self._config.cs_pin, HIGH)

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
            if self._gpio.input(self._config.drdy_pin) == LOW:
                return True
            time.sleep(0.0001)  # 100us
        return False

    def _write_cmd(self, cmd: int) -> None:
        """Write a single command byte to the ADC.

        Args:
            cmd: Command byte from _Ads1256Cmd.
        """
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
        """Reset the ADS1256 to power-on defaults.

        Uses hardware reset if reset_pin is configured, otherwise
        sends the software reset command.
        """
        assert self._gpio is not None
        if self._config.reset_pin is not None:
            # Hardware reset
            self._gpio.output(self._config.reset_pin, LOW)
            time.sleep(0.001)  # 1ms
            self._gpio.output(self._config.reset_pin, HIGH)
            time.sleep(0.001)  # Wait for reset to complete
        else:
            # Software reset
            self._cs_low()
            self._write_cmd(_Ads1256Cmd.RESET)
            self._cs_high()
            time.sleep(0.001)

        self._wait_drdy()

    def _configure(self) -> None:
        """Configure the ADS1256 with the settings from config.

        Sets up the STATUS, MUX, ADCON, and DRATE registers, then
        performs a self-calibration.
        """
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
