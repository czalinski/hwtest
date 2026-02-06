"""DAC8532 16-bit DAC driver for Waveshare High-Precision AD/DA board.

The DAC8532 is a dual-channel 16-bit digital-to-analog converter with:
- 2 independent output channels
- 16-bit resolution
- SPI interface
- Internal reference option or external Vref
- Output range: 0 to Vref (typically 0-5V with 5V supply)

Pin connections on the Waveshare High-Precision AD/DA board:
- SPI0: MOSI=GPIO10, MISO=GPIO9, SCLK=GPIO11
- CS: GPIO23 (directly controlled, active low)

Note: The DAC8532 shares the SPI bus with the ADS1256, but has a separate CS pin.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hwtest_waveshare.gpio import Gpio, OUTPUT, HIGH, LOW

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

        def close(self) -> None:
            """Close the SPI device."""
            ...


class Dac8532Channel:
    """DAC8532 channel identifiers.

    The DAC8532 has two independent output channels. Use these constants
    when specifying which channel to write.

    Attributes:
        CHANNEL_A: First DAC channel (output 0).
        CHANNEL_B: Second DAC channel (output 1).
    """

    CHANNEL_A = 0
    CHANNEL_B = 1


class _Dac8532Cmd:
    """DAC8532 command bytes.

    The command byte format is: [X X LDA LDB BUF X X X]
    where LDA/LDB control which DAC registers are loaded from
    their input registers, and BUF selects buffered mode.
    """

    # Direct write to DAC register
    WRITE_A = 0x10    # Write to channel A input register, update channel A output
    WRITE_B = 0x24    # Write to channel B input register, update channel B output
    WRITE_BOTH = 0x34 # Write to channel B input register, update both outputs


# Default GPIO pin for DAC chip select on Waveshare High-Precision AD/DA board
_DEFAULT_DAC_CS_PIN = 23  # Chip select (directly controlled, not SPI CE)


@dataclass(frozen=True)
class Dac8532Config:
    """Configuration for the DAC8532 DAC.

    Args:
        spi_bus: SPI bus number (0 or 1).
        spi_device: SPI device number (typically 0).
        cs_pin: GPIO pin for chip select (BCM numbering).
        vref: Reference voltage in volts (typically 5.0V).
    """

    spi_bus: int = 0
    spi_device: int = 0
    cs_pin: int = _DEFAULT_DAC_CS_PIN
    vref: float = 5.0


class Dac8532:
    """Low-level driver for the DAC8532 16-bit DAC.

    This class handles direct SPI communication with the DAC8532 chip.
    For higher-level instrument usage, see :class:`Dac8532Instrument`.

    Args:
        config: DAC configuration.
        spi: Optional SPI device (for testing). If None, uses spidev.
        gpio: Optional GPIO interface (for testing). If None, uses RPi.GPIO.
    """

    def __init__(
        self,
        config: Dac8532Config | None = None,
        spi: Any | None = None,
        gpio: Any | None = None,
    ) -> None:
        """Initialize the DAC8532 driver.

        Args:
            config: DAC configuration. If None, uses default configuration.
            spi: Optional SPI device for testing. If None, uses spidev.
            gpio: Optional GPIO interface for testing. If None, uses lgpio.
        """
        self._config = config or Dac8532Config()
        self._spi = spi
        self._gpio = gpio
        self._opened = False
        # Track current output values (DAC8532 does not support readback)
        self._channel_values: list[int] = [0, 0]

    @property
    def config(self) -> Dac8532Config:
        """The DAC configuration.

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

        # Initialize SPI
        if self._spi is None:
            try:
                import spidev  # type: ignore[import-not-found]

                self._spi = spidev.SpiDev()
            except ImportError as exc:
                self._gpio.cleanup([self._config.cs_pin])
                raise ImportError(
                    "spidev library is not installed. Install with: pip install spidev"
                ) from exc

        self._spi.open(self._config.spi_bus, self._config.spi_device)
        self._spi.max_speed_hz = 1000000  # 1 MHz
        self._spi.mode = 0b01  # CPOL=0, CPHA=1 (SPI mode 1)
        self._spi.lsbfirst = False

        self._opened = True

        # Initialize outputs to 0V
        self.write_raw(Dac8532Channel.CHANNEL_A, 0)
        self.write_raw(Dac8532Channel.CHANNEL_B, 0)

    def close(self) -> None:
        """Close the SPI device and release GPIO resources.

        Sets both DAC outputs to 0V before closing for safety.
        Safe to call multiple times. Errors during cleanup are silently ignored.
        """
        if not self._opened:
            return

        # Set outputs to 0V before closing
        try:
            self.write_raw(Dac8532Channel.CHANNEL_A, 0)
            self.write_raw(Dac8532Channel.CHANNEL_B, 0)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

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

    def write_raw(self, channel: int, value: int) -> None:
        """Write a raw 16-bit value to a DAC channel.

        Args:
            channel: Channel number (0 for A, 1 for B).
            value: 16-bit value (0-65535).

        Raises:
            RuntimeError: If device is not open.
            ValueError: If channel or value is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if channel not in (0, 1):
            raise ValueError(f"channel must be 0 or 1, got {channel}")
        if not 0 <= value <= 65535:
            raise ValueError(f"value must be 0-65535, got {value}")

        # Select command byte based on channel
        cmd = _Dac8532Cmd.WRITE_A if channel == 0 else _Dac8532Cmd.WRITE_B

        # Send 3 bytes: command, data MSB, data LSB
        data_msb = (value >> 8) & 0xFF
        data_lsb = value & 0xFF

        assert self._spi is not None
        self._cs_low()
        self._spi.writebytes([cmd, data_msb, data_lsb])
        self._cs_high()

        self._channel_values[channel] = value

    def write_voltage(self, channel: int, voltage: float) -> None:
        """Write a voltage to a DAC channel.

        Args:
            channel: Channel number (0 for A, 1 for B).
            voltage: Output voltage (0 to Vref).

        Raises:
            RuntimeError: If device is not open.
            ValueError: If channel or voltage is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if channel not in (0, 1):
            raise ValueError(f"channel must be 0 or 1, got {channel}")
        if not 0.0 <= voltage <= self._config.vref:
            raise ValueError(f"voltage must be 0-{self._config.vref}V, got {voltage}")

        # Convert voltage to 16-bit value
        # value = (voltage / vref) * 65535
        value = int((voltage / self._config.vref) * 65535)
        value = min(65535, max(0, value))  # Clamp to valid range

        self.write_raw(channel, value)

    def read_voltage(self, channel: int) -> float:
        """Read the last written voltage for a channel.

        Note: The DAC8532 does not support readback. This returns the
        last value written via software.

        Args:
            channel: Channel number (0 for A, 1 for B).

        Returns:
            Last written voltage in volts.

        Raises:
            RuntimeError: If device is not open.
            ValueError: If channel is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if channel not in (0, 1):
            raise ValueError(f"channel must be 0 or 1, got {channel}")

        value = self._channel_values[channel]
        voltage = (value / 65535.0) * self._config.vref
        return voltage

    def write_both(self, voltage_a: float, voltage_b: float) -> None:
        """Write voltages to both channels simultaneously.

        Both outputs update at the same time for synchronized operation.

        Args:
            voltage_a: Output voltage for channel A (0 to Vref).
            voltage_b: Output voltage for channel B (0 to Vref).

        Raises:
            RuntimeError: If device is not open.
            ValueError: If voltages are invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if not 0.0 <= voltage_a <= self._config.vref:
            raise ValueError(f"voltage_a must be 0-{self._config.vref}V, got {voltage_a}")
        if not 0.0 <= voltage_b <= self._config.vref:
            raise ValueError(f"voltage_b must be 0-{self._config.vref}V, got {voltage_b}")

        assert self._spi is not None

        # Write channel A (input register only, don't update output yet)
        value_a = int((voltage_a / self._config.vref) * 65535)
        value_a = min(65535, max(0, value_a))

        self._cs_low()
        # Write to A input register without updating output
        self._spi.writebytes([0x00, (value_a >> 8) & 0xFF, value_a & 0xFF])
        self._cs_high()
        self._channel_values[0] = value_a

        # Write channel B and update both outputs
        value_b = int((voltage_b / self._config.vref) * 65535)
        value_b = min(65535, max(0, value_b))

        self._cs_low()
        self._spi.writebytes([_Dac8532Cmd.WRITE_BOTH, (value_b >> 8) & 0xFF, value_b & 0xFF])
        self._cs_high()
        self._channel_values[1] = value_b
