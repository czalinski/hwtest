"""MCP23017 I2C GPIO expander driver.

This module provides a driver for the MCP23017 16-bit I/O expander with I2C
interface. The MCP23017 features:

- 16 GPIO pins organized as two 8-bit ports (A and B)
- Each pin individually configurable as input or output
- Internal pull-up resistors (100k ohm)
- Interrupt-on-change capability (not implemented in this driver)

Default I2C address is 0x20, configurable via A0-A2 pins (0x20-0x27).

Example:
    >>> gpio = Mcp23017(Mcp23017Config(i2c_bus=1, address=0x20))
    >>> gpio.open()
    >>> gpio.set_pin_direction(0, PinDirection.OUTPUT)
    >>> gpio.write_pin(0, True)
    >>> value = gpio.read_pin(1)
    >>> gpio.close()
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class Mcp23017Register(IntEnum):
    """MCP23017 register addresses in IOCON.BANK=0 mode.

    Register layout when BANK bit is 0 (default). Registers are paired
    for Port A and Port B operations.
    """

    IODIRA = 0x00  # I/O direction port A (1=input, 0=output)
    IODIRB = 0x01  # I/O direction port B
    IPOLA = 0x02  # Input polarity port A
    IPOLB = 0x03  # Input polarity port B
    GPINTENA = 0x04  # Interrupt-on-change enable port A
    GPINTENB = 0x05  # Interrupt-on-change enable port B
    DEFVALA = 0x06  # Default compare value port A
    DEFVALB = 0x07  # Default compare value port B
    INTCONA = 0x08  # Interrupt control port A
    INTCONB = 0x09  # Interrupt control port B
    IOCON = 0x0A  # Configuration register
    GPPUA = 0x0C  # Pull-up resistor enable port A
    GPPUB = 0x0D  # Pull-up resistor enable port B
    INTFA = 0x0E  # Interrupt flag port A
    INTFB = 0x0F  # Interrupt flag port B
    INTCAPA = 0x10  # Interrupt captured value port A
    INTCAPB = 0x11  # Interrupt captured value port B
    GPIOA = 0x12  # GPIO port A
    GPIOB = 0x13  # GPIO port B
    OLATA = 0x14  # Output latch port A
    OLATB = 0x15  # Output latch port B


class PinDirection(IntEnum):
    """Pin direction constants for GPIO configuration.

    Attributes:
        OUTPUT: Configure pin as output (value 0).
        INPUT: Configure pin as input (value 1).
    """

    OUTPUT = 0
    INPUT = 1


@dataclass(frozen=True)
class Mcp23017Config:
    """Configuration for the MCP23017 GPIO expander.

    Immutable configuration specifying the I2C bus and device address.

    Attributes:
        i2c_bus: I2C bus number (typically 1 on Raspberry Pi).
        address: I2C address (0x20-0x27, configurable via A0-A2 pins).

    Raises:
        ValueError: If address is not in valid range 0x20-0x27.
    """

    i2c_bus: int = 1
    address: int = 0x20

    def __post_init__(self) -> None:
        """Validate configuration parameters.

        Raises:
            ValueError: If address is not in valid range 0x20-0x27.
        """
        if not 0x20 <= self.address <= 0x27:
            raise ValueError(f"address must be 0x20-0x27, got {hex(self.address)}")


class Mcp23017:
    """Driver for the MCP23017 16-bit I2C GPIO expander.

    Provides access to 16 GPIO pins organized as two 8-bit ports (A and B).
    Pins are numbered 0-15, where 0-7 are port A and 8-15 are port B.

    The device must be opened before use and should be closed when done to
    release I2C bus resources.

    Attributes:
        config: The device configuration (read-only).
        is_open: True if the device is currently open.

    Example:
        >>> gpio = Mcp23017(Mcp23017Config(i2c_bus=1, address=0x20))
        >>> gpio.open()
        >>> gpio.set_pin_direction(0, PinDirection.OUTPUT)
        >>> gpio.write_pin(0, True)
        >>> gpio.close()
    """

    def __init__(
        self,
        config: Mcp23017Config | None = None,
        bus: Any | None = None,
    ) -> None:
        """Initialize the MCP23017 driver.

        Args:
            config: Device configuration. Uses defaults if None.
            bus: Optional pre-configured I2C bus object for testing/mocking.
        """
        self._config = config or Mcp23017Config()
        self._bus = bus
        self._opened = False
        # Track pin directions (1=input, 0=output)
        self._direction_a = 0xFF  # All inputs by default
        self._direction_b = 0xFF
        # Track output states
        self._output_a = 0x00
        self._output_b = 0x00

    @property
    def config(self) -> Mcp23017Config:
        """Return the device configuration."""
        return self._config

    @property
    def is_open(self) -> bool:
        """Return True if the device is open."""
        return self._opened

    def open(self) -> None:
        """Open the I2C bus and initialize the device.

        Raises:
            RuntimeError: If the device is already open.
            ImportError: If smbus2 is not available.
        """
        if self._opened:
            raise RuntimeError("Device already open")

        if self._bus is None:
            try:
                import smbus2

                self._bus = smbus2.SMBus(self._config.i2c_bus)
            except ImportError as exc:
                raise ImportError(
                    "smbus2 library is not installed. Install with: pip install smbus2"
                ) from exc

        self._opened = True

        # Initialize device - set all pins as inputs with pull-ups disabled
        self._write_register(Mcp23017Register.IODIRA, 0xFF)
        self._write_register(Mcp23017Register.IODIRB, 0xFF)
        self._write_register(Mcp23017Register.GPPUA, 0x00)
        self._write_register(Mcp23017Register.GPPUB, 0x00)
        self._write_register(Mcp23017Register.OLATA, 0x00)
        self._write_register(Mcp23017Register.OLATB, 0x00)

    def close(self) -> None:
        """Close the I2C bus and release resources.

        Resets all output pins to low before closing. Safe to call multiple
        times or on an already closed device.
        """
        if not self._opened:
            return

        # Reset all outputs to low before closing
        try:
            self._write_register(Mcp23017Register.OLATA, 0x00)
            self._write_register(Mcp23017Register.OLATB, 0x00)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        if self._bus is not None:
            try:
                self._bus.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        self._opened = False

    def _write_register(self, register: int, value: int) -> None:
        """Write a value to a device register.

        Args:
            register: Register address to write to.
            value: 8-bit value to write.
        """
        assert self._bus is not None
        self._bus.write_byte_data(self._config.address, register, value)

    def _read_register(self, register: int) -> int:
        """Read a value from a device register.

        Args:
            register: Register address to read from.

        Returns:
            8-bit register value.
        """
        assert self._bus is not None
        result: int = self._bus.read_byte_data(self._config.address, register)
        return result

    def set_pin_direction(self, pin: int, direction: PinDirection) -> None:
        """Set the direction of a single pin.

        Args:
            pin: Pin number (0-15, where 0-7 are port A, 8-15 are port B).
            direction: PinDirection.INPUT or PinDirection.OUTPUT.

        Raises:
            RuntimeError: If device is not open.
            ValueError: If pin is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if not 0 <= pin <= 15:
            raise ValueError(f"pin must be 0-15, got {pin}")

        if pin < 8:
            # Port A
            if direction == PinDirection.INPUT:
                self._direction_a |= 1 << pin
            else:
                self._direction_a &= ~(1 << pin)
            self._write_register(Mcp23017Register.IODIRA, self._direction_a)
        else:
            # Port B
            bit = pin - 8
            if direction == PinDirection.INPUT:
                self._direction_b |= 1 << bit
            else:
                self._direction_b &= ~(1 << bit)
            self._write_register(Mcp23017Register.IODIRB, self._direction_b)

    def set_port_direction(self, port: str, direction_mask: int) -> None:
        """Set the direction of all pins on a port.

        Args:
            port: "A" or "B".
            direction_mask: 8-bit mask where 1=input, 0=output.

        Raises:
            RuntimeError: If device is not open.
            ValueError: If port is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if port.upper() not in ("A", "B"):
            raise ValueError(f"port must be 'A' or 'B', got {port}")

        if port.upper() == "A":
            self._direction_a = direction_mask & 0xFF
            self._write_register(Mcp23017Register.IODIRA, self._direction_a)
        else:
            self._direction_b = direction_mask & 0xFF
            self._write_register(Mcp23017Register.IODIRB, self._direction_b)

    def set_all_directions(self, direction_mask: int) -> None:
        """Set the direction of all 16 pins.

        Args:
            direction_mask: 16-bit mask where 1=input, 0=output.
                Bits 0-7 are port A, bits 8-15 are port B.

        Raises:
            RuntimeError: If device is not open.
        """
        if not self._opened:
            raise RuntimeError("Device not open")

        self._direction_a = direction_mask & 0xFF
        self._direction_b = (direction_mask >> 8) & 0xFF
        self._write_register(Mcp23017Register.IODIRA, self._direction_a)
        self._write_register(Mcp23017Register.IODIRB, self._direction_b)

    def write_pin(self, pin: int, value: bool) -> None:
        """Write a value to an output pin.

        Args:
            pin: Pin number (0-15).
            value: True for high, False for low.

        Raises:
            RuntimeError: If device is not open.
            ValueError: If pin is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if not 0 <= pin <= 15:
            raise ValueError(f"pin must be 0-15, got {pin}")

        if pin < 8:
            if value:
                self._output_a |= 1 << pin
            else:
                self._output_a &= ~(1 << pin)
            self._write_register(Mcp23017Register.OLATA, self._output_a)
        else:
            bit = pin - 8
            if value:
                self._output_b |= 1 << bit
            else:
                self._output_b &= ~(1 << bit)
            self._write_register(Mcp23017Register.OLATB, self._output_b)

    def write_port(self, port: str, value: int) -> None:
        """Write a value to all pins on a port.

        Args:
            port: "A" or "B".
            value: 8-bit value to write.

        Raises:
            RuntimeError: If device is not open.
            ValueError: If port is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if port.upper() not in ("A", "B"):
            raise ValueError(f"port must be 'A' or 'B', got {port}")

        if port.upper() == "A":
            self._output_a = value & 0xFF
            self._write_register(Mcp23017Register.OLATA, self._output_a)
        else:
            self._output_b = value & 0xFF
            self._write_register(Mcp23017Register.OLATB, self._output_b)

    def write_all(self, value: int) -> None:
        """Write a value to all 16 output pins.

        Args:
            value: 16-bit value (bits 0-7 = port A, bits 8-15 = port B).

        Raises:
            RuntimeError: If device is not open.
        """
        if not self._opened:
            raise RuntimeError("Device not open")

        self._output_a = value & 0xFF
        self._output_b = (value >> 8) & 0xFF
        self._write_register(Mcp23017Register.OLATA, self._output_a)
        self._write_register(Mcp23017Register.OLATB, self._output_b)

    def read_pin(self, pin: int) -> bool:
        """Read the value of a pin.

        Args:
            pin: Pin number (0-15).

        Returns:
            True if high, False if low.

        Raises:
            RuntimeError: If device is not open.
            ValueError: If pin is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if not 0 <= pin <= 15:
            raise ValueError(f"pin must be 0-15, got {pin}")

        if pin < 8:
            value = self._read_register(Mcp23017Register.GPIOA)
            return bool(value & (1 << pin))
        value = self._read_register(Mcp23017Register.GPIOB)
        return bool(value & (1 << (pin - 8)))

    def read_port(self, port: str) -> int:
        """Read all pins on a port.

        Args:
            port: "A" or "B".

        Returns:
            8-bit value representing pin states.

        Raises:
            RuntimeError: If device is not open.
            ValueError: If port is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if port.upper() not in ("A", "B"):
            raise ValueError(f"port must be 'A' or 'B', got {port}")

        if port.upper() == "A":
            return self._read_register(Mcp23017Register.GPIOA)
        return self._read_register(Mcp23017Register.GPIOB)

    def read_all(self) -> int:
        """Read all 16 pins.

        Returns:
            16-bit value (bits 0-7 = port A, bits 8-15 = port B).

        Raises:
            RuntimeError: If device is not open.
        """
        if not self._opened:
            raise RuntimeError("Device not open")

        port_a = self._read_register(Mcp23017Register.GPIOA)
        port_b = self._read_register(Mcp23017Register.GPIOB)
        return port_a | (port_b << 8)

    def set_pullup(self, pin: int, enabled: bool) -> None:
        """Enable or disable the internal pull-up resistor on a pin.

        Args:
            pin: Pin number (0-15).
            enabled: True to enable pull-up, False to disable.

        Raises:
            RuntimeError: If device is not open.
            ValueError: If pin is invalid.
        """
        if not self._opened:
            raise RuntimeError("Device not open")
        if not 0 <= pin <= 15:
            raise ValueError(f"pin must be 0-15, got {pin}")

        if pin < 8:
            current = self._read_register(Mcp23017Register.GPPUA)
            if enabled:
                current |= 1 << pin
            else:
                current &= ~(1 << pin)
            self._write_register(Mcp23017Register.GPPUA, current)
        else:
            bit = pin - 8
            current = self._read_register(Mcp23017Register.GPPUB)
            if enabled:
                current |= 1 << bit
            else:
                current &= ~(1 << bit)
            self._write_register(Mcp23017Register.GPPUB, current)
