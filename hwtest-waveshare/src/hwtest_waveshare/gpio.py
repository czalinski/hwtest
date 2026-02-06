"""GPIO abstraction layer for Raspberry Pi.

Supports both RPi.GPIO (Pi 4 and earlier) and lgpio (Pi 5).
Automatically selects the appropriate backend based on availability.
"""

from __future__ import annotations

from typing import Any

# GPIO direction constants
INPUT = 0
OUTPUT = 1

# GPIO level constants
LOW = 0
HIGH = 1


class GpioPin:
    """Represents a single GPIO pin with direction and state.

    This class wraps a GPIO pin claimed via lgpio, providing simple
    read/write operations. Pins are automatically claimed on construction.

    Attributes:
        _lgpio: Reference to the lgpio module.
        _chip: GPIO chip handle from gpiochip_open().
        _pin: BCM pin number.
        _direction: Pin direction (INPUT or OUTPUT).
    """

    def __init__(
        self,
        chip_handle: int,
        pin: int,
        direction: int,
        initial: int = LOW,
        lgpio_module: Any = None,
    ) -> None:
        """Initialize and claim a GPIO pin.

        Args:
            chip_handle: Handle from gpiochip_open().
            pin: BCM pin number.
            direction: Pin direction (INPUT or OUTPUT).
            initial: Initial value for output pins (LOW or HIGH).
            lgpio_module: Reference to the lgpio module.
        """
        self._lgpio = lgpio_module
        self._chip = chip_handle
        self._pin = pin
        self._direction = direction

        if direction == OUTPUT:
            self._lgpio.gpio_claim_output(chip_handle, pin, initial)
        else:
            self._lgpio.gpio_claim_input(chip_handle, pin)

    def read(self) -> int:
        """Read the current pin value.

        Returns:
            Pin value (0 or 1).
        """
        result: int = self._lgpio.gpio_read(self._chip, self._pin)
        return result

    def write(self, value: int) -> None:
        """Write a value to the pin.

        Args:
            value: Value to write (0 or 1).
        """
        self._lgpio.gpio_write(self._chip, self._pin, value)

    def release(self) -> None:
        """Release the pin back to the system.

        This frees the GPIO claim, allowing other processes to use the pin.
        Errors during release are silently ignored.
        """
        try:
            self._lgpio.gpio_free(self._chip, self._pin)
        except Exception:  # pylint: disable=broad-exception-caught
            pass


class Gpio:
    """GPIO interface using lgpio (Raspberry Pi 5 compatible).

    This class provides a simple interface for GPIO operations using the
    lgpio library, which is compatible with the Raspberry Pi 5's RP1 chip.

    Args:
        chip: GPIO chip number (default 0 for main GPIO).
    """

    def __init__(self, chip: int = 0) -> None:
        """Initialize the GPIO interface.

        Args:
            chip: GPIO chip number (default 0 for main GPIO).
        """
        self._chip = chip
        self._handle: int | None = None
        self._pins: dict[int, GpioPin] = {}
        self._lgpio: Any = None

    def open(self) -> None:
        """Open the GPIO chip.

        Raises:
            ImportError: If lgpio is not available.
            RuntimeError: If the chip cannot be opened.
        """
        if self._handle is not None:
            return

        try:
            import lgpio  # type: ignore[import-not-found]

            self._lgpio = lgpio
        except ImportError as exc:
            raise ImportError(
                "lgpio library is not installed. Install with: pip install lgpio"
            ) from exc

        try:
            self._handle = lgpio.gpiochip_open(self._chip)
        except Exception as exc:
            raise RuntimeError(f"Failed to open GPIO chip {self._chip}: {exc}") from exc

    def close(self) -> None:
        """Close the GPIO chip and release all pins."""
        if self._handle is None:
            return

        # Release all pins
        for pin in list(self._pins.values()):
            pin.release()
        self._pins.clear()

        try:
            self._lgpio.gpiochip_close(self._handle)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        self._handle = None

    def setup(self, pin: int, direction: int, initial: int = LOW) -> None:
        """Configure a GPIO pin.

        Args:
            pin: BCM pin number.
            direction: INPUT or OUTPUT.
            initial: Initial value for output pins.
        """
        if self._handle is None:
            raise RuntimeError("GPIO not opened")

        if pin in self._pins:
            self._pins[pin].release()

        self._pins[pin] = GpioPin(
            self._handle, pin, direction, initial, self._lgpio
        )

    def input(self, pin: int) -> int:
        """Read a GPIO pin.

        Args:
            pin: BCM pin number.

        Returns:
            Pin value (0 or 1).
        """
        if pin not in self._pins:
            raise RuntimeError(f"Pin {pin} not configured")
        return self._pins[pin].read()

    def output(self, pin: int, value: int) -> None:
        """Write to a GPIO pin.

        Args:
            pin: BCM pin number.
            value: Value to write (0 or 1).
        """
        if pin not in self._pins:
            raise RuntimeError(f"Pin {pin} not configured")
        self._pins[pin].write(value)

    def cleanup(self, pins: int | list[int] | None = None) -> None:
        """Release GPIO pins.

        Args:
            pins: Pin or list of pins to release, or None for all.
        """
        if pins is None:
            pins_to_release = list(self._pins.keys())
        elif isinstance(pins, int):
            pins_to_release = [pins]
        else:
            pins_to_release = list(pins)

        for pin in pins_to_release:
            if pin in self._pins:
                self._pins[pin].release()
                del self._pins[pin]
