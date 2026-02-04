"""UUT (Unit Under Test) simulator.

Integrates CAN bus, DAC, ADC, and GPIO for simulating a device under test.
Controllable over REST API for integration testing scenarios.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from hwtest_uut.can_interface import CanConfig, CanInterface, CanMessage
from hwtest_uut.mcp23017 import Mcp23017, Mcp23017Config, PinDirection

logger = logging.getLogger(__name__)


@dataclass
class SimulatorConfig:
    """Configuration for the UUT simulator.

    Args:
        can_enabled: Enable CAN bus interface.
        can_interface: CAN interface name (e.g., "can0").
        can_bitrate: CAN bitrate in bits/second.
        can_fd: Enable CAN FD mode.
        can_heartbeat_id: CAN arbitration ID for heartbeat messages.
        can_heartbeat_interval_ms: Heartbeat interval in milliseconds (0 to disable).
        dac_enabled: Enable DAC output.
        dac_vref: DAC reference voltage.
        adc_enabled: Enable ADC input.
        gpio_enabled: Enable MCP23017 GPIO expander.
        gpio_i2c_bus: I2C bus number for GPIO expander.
        gpio_address: I2C address for GPIO expander (0x20-0x27).
        failure_delay_seconds: Delay before failure injection activates (0 to disable).
        failure_voltage_offset: Voltage offset to add when failure is active.
    """

    can_enabled: bool = True
    can_interface: str = "can0"
    can_bitrate: int = 500000
    can_fd: bool = False
    can_heartbeat_id: int = 0x100
    can_heartbeat_interval_ms: int = 100
    dac_enabled: bool = True
    dac_vref: float = 5.0
    adc_enabled: bool = True
    gpio_enabled: bool = True
    gpio_i2c_bus: int = 1
    gpio_address: int = 0x20
    failure_delay_seconds: float = 0.0
    failure_voltage_offset: float = 1.0


@dataclass
class CanEchoState:
    """State for CAN echo mode."""

    enabled: bool = False
    id_offset: int = 0
    filter_ids: list[int] = field(default_factory=list)


@dataclass
class CanHeartbeatState:
    """State for CAN heartbeat."""

    running: bool = False
    message_count: int = 0
    arbitration_id: int = 0x100
    interval_ms: int = 100


@dataclass
class FailureState:
    """State for failure injection."""

    enabled: bool = False
    delay_seconds: float = 0.0
    voltage_offset: float = 1.0
    start_time: float | None = None
    active: bool = False


class UutSimulator:
    """UUT simulator integrating CAN, DAC, ADC, and GPIO.

    Provides a unified interface for controlling simulated device behavior:
    - CAN message sending, receiving, and echo mode
    - DAC voltage output
    - ADC voltage input
    - Digital I/O via MCP23017 GPIO expander

    Args:
        config: Simulator configuration.
        can_bus: Optional CAN bus object (for testing).
        gpio_bus: Optional I2C bus object (for testing).
        dac: Optional DAC object (for testing).
        adc: Optional ADC object (for testing).
    """

    def __init__(
        self,
        config: SimulatorConfig | None = None,
        can_bus: Any | None = None,
        gpio_bus: Any | None = None,
        dac: Any | None = None,
        adc: Any | None = None,
    ) -> None:
        self._config = config or SimulatorConfig()
        self._start_time = time.time()
        self._running = False

        # CAN interface
        self._can: CanInterface | None = None
        self._can_bus = can_bus
        self._can_echo = CanEchoState()
        self._can_heartbeat = CanHeartbeatState(
            arbitration_id=self._config.can_heartbeat_id,
            interval_ms=self._config.can_heartbeat_interval_ms,
        )
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._can_rx_messages: list[CanMessage] = []
        self._can_rx_max = 100

        # GPIO expander
        self._gpio: Mcp23017 | None = None
        self._gpio_bus = gpio_bus

        # DAC (from hwtest-waveshare)
        self._dac = dac
        self._dac_values: list[float] = [0.0, 0.0]

        # ADC (from hwtest-waveshare)
        self._adc = adc

        # Failure injection state
        self._failure = FailureState(
            enabled=self._config.failure_delay_seconds > 0,
            delay_seconds=self._config.failure_delay_seconds,
            voltage_offset=self._config.failure_voltage_offset,
        )

    @property
    def config(self) -> SimulatorConfig:
        """Return the simulator configuration."""
        return self._config

    @property
    def is_running(self) -> bool:
        """Return True if the simulator is running."""
        return self._running

    @property
    def uptime(self) -> float:
        """Return uptime in seconds."""
        return time.time() - self._start_time

    def start(self) -> None:
        """Start the simulator and initialize all enabled interfaces.

        Raises:
            RuntimeError: If already running.
        """
        if self._running:
            raise RuntimeError("Simulator already running")

        self._start_time = time.time()

        # Initialize CAN
        if self._config.can_enabled:
            can_config = CanConfig(
                interface=self._config.can_interface,
                bitrate=self._config.can_bitrate,
                fd=self._config.can_fd,
            )
            self._can = CanInterface(config=can_config, bus=self._can_bus)
            try:
                self._can.open()
                self._can.add_callback(self._on_can_message)
                logger.info("CAN interface opened: %s", self._config.can_interface)
            except Exception as exc:
                logger.warning("Failed to open CAN interface: %s", exc)
                self._can = None

        # Initialize GPIO
        if self._config.gpio_enabled:
            gpio_config = Mcp23017Config(
                i2c_bus=self._config.gpio_i2c_bus,
                address=self._config.gpio_address,
            )
            self._gpio = Mcp23017(config=gpio_config, bus=self._gpio_bus)
            try:
                self._gpio.open()
                logger.info("GPIO expander opened: 0x%02X", self._config.gpio_address)
            except Exception as exc:
                logger.warning("Failed to open GPIO expander: %s", exc)
                self._gpio = None

        # Initialize DAC
        if self._config.dac_enabled and self._dac is not None:
            try:
                self._dac.open()
                logger.info("DAC opened")
            except Exception as exc:
                logger.warning("Failed to open DAC: %s", exc)
                self._dac = None

        # Initialize ADC
        if self._config.adc_enabled and self._adc is not None:
            try:
                self._adc.open()
                logger.info("ADC opened")
            except Exception as exc:
                logger.warning("Failed to open ADC: %s", exc)
                self._adc = None

        self._running = True
        logger.info("UUT simulator started")

    def stop(self) -> None:
        """Stop the simulator and close all interfaces."""
        if not self._running:
            return

        self._running = False

        if self._can is not None:
            self._can.close()
            self._can = None

        if self._gpio is not None:
            self._gpio.close()
            self._gpio = None

        if self._dac is not None:
            try:
                self._dac.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            self._dac = None

        if self._adc is not None:
            try:
                self._adc.close()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            self._adc = None

        logger.info("UUT simulator stopped")

    async def run(self) -> None:
        """Run the simulator with async CAN receive loop and heartbeat.

        This method blocks until stop() is called.
        """
        if self._can is not None and self._can.is_open:
            await self._can.start_receiving()
            # Start heartbeat if interval > 0
            if self._config.can_heartbeat_interval_ms > 0:
                await self._start_heartbeat()

        while self._running:
            await asyncio.sleep(0.1)

        # Stop heartbeat
        await self._stop_heartbeat()

        if self._can is not None:
            await self._can.stop_receiving()

    # -------------------------------------------------------------------------
    # CAN Operations
    # -------------------------------------------------------------------------

    def can_send(self, message: CanMessage) -> None:
        """Send a CAN message.

        Args:
            message: The message to send.

        Raises:
            RuntimeError: If CAN interface not available.
        """
        if self._can is None or not self._can.is_open:
            raise RuntimeError("CAN interface not available")
        self._can.send(message)

    def can_send_data(
        self,
        arbitration_id: int,
        data: bytes | list[int],
        is_extended_id: bool = False,
    ) -> None:
        """Send a CAN message with the given ID and data.

        Args:
            arbitration_id: CAN arbitration ID.
            data: Message data.
            is_extended_id: True for 29-bit extended ID.

        Raises:
            RuntimeError: If CAN interface not available.
        """
        if self._can is None or not self._can.is_open:
            raise RuntimeError("CAN interface not available")
        self._can.send_data(arbitration_id, data, is_extended_id)

    def can_get_received(self) -> list[CanMessage]:
        """Get list of received CAN messages.

        Returns:
            List of received messages (oldest first).
        """
        return list(self._can_rx_messages)

    def can_clear_received(self) -> None:
        """Clear the received message buffer."""
        self._can_rx_messages.clear()

    def can_set_echo(
        self,
        enabled: bool,
        id_offset: int = 0,
        filter_ids: list[int] | None = None,
    ) -> None:
        """Configure CAN echo mode.

        In echo mode, received messages are automatically echoed back
        with an optional ID offset.

        Args:
            enabled: Enable or disable echo mode.
            id_offset: Offset to add to echoed message ID.
            filter_ids: Only echo messages with these IDs (None for all).
        """
        self._can_echo.enabled = enabled
        self._can_echo.id_offset = id_offset
        self._can_echo.filter_ids = filter_ids or []

    def can_get_echo_config(self) -> CanEchoState:
        """Get the current CAN echo configuration."""
        return self._can_echo

    def _on_can_message(self, message: CanMessage) -> None:
        """Handle received CAN message."""
        # Store message
        self._can_rx_messages.append(message)
        if len(self._can_rx_messages) > self._can_rx_max:
            self._can_rx_messages.pop(0)

        # Echo if enabled
        if self._can_echo.enabled:
            if not self._can_echo.filter_ids or message.arbitration_id in self._can_echo.filter_ids:
                echo_msg = CanMessage(
                    arbitration_id=message.arbitration_id + self._can_echo.id_offset,
                    data=message.data,
                    is_extended_id=message.is_extended_id,
                    is_fd=message.is_fd,
                    bitrate_switch=message.bitrate_switch,
                )
                try:
                    self.can_send(echo_msg)
                except Exception:  # pylint: disable=broad-exception-caught
                    logger.exception("Failed to echo CAN message")

    # -------------------------------------------------------------------------
    # CAN Heartbeat
    # -------------------------------------------------------------------------

    def can_get_heartbeat_state(self) -> CanHeartbeatState:
        """Get the current heartbeat state.

        Returns:
            Current heartbeat state including running status and message count.
        """
        return self._can_heartbeat

    async def _start_heartbeat(self) -> None:
        """Start the heartbeat background task."""
        if self._heartbeat_task is not None:
            return

        self._can_heartbeat.running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            "CAN heartbeat started: ID=0x%03X, interval=%dms",
            self._can_heartbeat.arbitration_id,
            self._can_heartbeat.interval_ms,
        )

    async def _stop_heartbeat(self) -> None:
        """Stop the heartbeat background task."""
        self._can_heartbeat.running = False

        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
            logger.info("CAN heartbeat stopped")

    async def _heartbeat_loop(self) -> None:
        """Background task for sending heartbeat messages."""
        interval_sec = self._can_heartbeat.interval_ms / 1000.0

        while self._can_heartbeat.running:
            try:
                # Build heartbeat data: 8-byte counter (big-endian)
                count = self._can_heartbeat.message_count
                data = count.to_bytes(8, byteorder="big")

                msg = CanMessage(
                    arbitration_id=self._can_heartbeat.arbitration_id,
                    data=data,
                )
                self.can_send(msg)
                self._can_heartbeat.message_count += 1

                await asyncio.sleep(interval_sec)

            except asyncio.CancelledError:
                break
            except Exception:  # pylint: disable=broad-exception-caught
                if self._can_heartbeat.running:
                    logger.exception("Error sending heartbeat")
                    await asyncio.sleep(interval_sec)

    # -------------------------------------------------------------------------
    # DAC Operations
    # -------------------------------------------------------------------------

    def dac_write(self, channel: int, voltage: float) -> None:
        """Write a voltage to the DAC.

        If failure injection is enabled and active, an offset is added to the voltage.

        Args:
            channel: DAC channel (0 or 1).
            voltage: Voltage to output (0 to vref).

        Raises:
            RuntimeError: If DAC not available.
            ValueError: If channel or voltage invalid.
        """
        if channel not in (0, 1):
            raise ValueError(f"channel must be 0 or 1, got {channel}")
        if not 0 <= voltage <= self._config.dac_vref:
            raise ValueError(f"voltage must be 0-{self._config.dac_vref}V, got {voltage}")

        # Track first DAC write for failure injection timing
        if self._failure.enabled and self._failure.start_time is None:
            self._failure.start_time = time.time()
            logger.info(
                "Failure injection timer started (delay: %.1fs, offset: %.2fV)",
                self._failure.delay_seconds,
                self._failure.voltage_offset,
            )

        # Check if failure should activate
        output_voltage = voltage
        if self._failure.enabled and self._failure.start_time is not None:
            elapsed = time.time() - self._failure.start_time
            if elapsed >= self._failure.delay_seconds:
                if not self._failure.active:
                    self._failure.active = True
                    logger.warning(
                        "Failure injection ACTIVATED after %.1fs (offset: +%.2fV)",
                        elapsed,
                        self._failure.voltage_offset,
                    )
                # Apply voltage offset, clamped to DAC range
                output_voltage = min(voltage + self._failure.voltage_offset, self._config.dac_vref)

        if self._dac is not None:
            self._dac.write_voltage(channel, output_voltage)

        self._dac_values[channel] = voltage

    def dac_write_both(self, voltage_a: float, voltage_b: float) -> None:
        """Write voltages to both DAC channels.

        Args:
            voltage_a: Voltage for channel A.
            voltage_b: Voltage for channel B.
        """
        self.dac_write(0, voltage_a)
        self.dac_write(1, voltage_b)

    def dac_read(self, channel: int) -> float:
        """Read the last written DAC voltage.

        Args:
            channel: DAC channel (0 or 1).

        Returns:
            Last written voltage.

        Raises:
            ValueError: If channel invalid.
        """
        if channel not in (0, 1):
            raise ValueError(f"channel must be 0 or 1, got {channel}")
        return self._dac_values[channel]

    def dac_read_all(self) -> list[float]:
        """Read all DAC channel voltages.

        Returns:
            List of voltages [channel_a, channel_b].
        """
        return list(self._dac_values)

    # -------------------------------------------------------------------------
    # ADC Operations
    # -------------------------------------------------------------------------

    def adc_read(self, channel: int) -> float:
        """Read an ADC channel voltage.

        Args:
            channel: ADC channel (0-7).

        Returns:
            Measured voltage.

        Raises:
            RuntimeError: If ADC not available.
            ValueError: If channel invalid.
        """
        if not 0 <= channel <= 7:
            raise ValueError(f"channel must be 0-7, got {channel}")
        if self._adc is None:
            raise RuntimeError("ADC not available")
        result: float = self._adc.read_voltage(channel)
        return result

    def adc_read_all(self) -> list[float]:
        """Read all ADC channels.

        Returns:
            List of voltages for all channels.

        Raises:
            RuntimeError: If ADC not available.
        """
        if self._adc is None:
            raise RuntimeError("ADC not available")
        result: list[float] = self._adc.read_all_channels()
        return result

    # -------------------------------------------------------------------------
    # GPIO Operations
    # -------------------------------------------------------------------------

    def gpio_set_direction(self, pin: int, direction: PinDirection) -> None:
        """Set the direction of a GPIO pin.

        Args:
            pin: Pin number (0-15).
            direction: PinDirection.INPUT or PinDirection.OUTPUT.

        Raises:
            RuntimeError: If GPIO not available.
        """
        if self._gpio is None:
            raise RuntimeError("GPIO not available")
        self._gpio.set_pin_direction(pin, direction)

    def gpio_write(self, pin: int, value: bool) -> None:
        """Write a value to a GPIO output pin.

        Args:
            pin: Pin number (0-15).
            value: True for high, False for low.

        Raises:
            RuntimeError: If GPIO not available.
        """
        if self._gpio is None:
            raise RuntimeError("GPIO not available")
        self._gpio.write_pin(pin, value)

    def gpio_read(self, pin: int) -> bool:
        """Read a GPIO pin value.

        Args:
            pin: Pin number (0-15).

        Returns:
            True if high, False if low.

        Raises:
            RuntimeError: If GPIO not available.
        """
        if self._gpio is None:
            raise RuntimeError("GPIO not available")
        return self._gpio.read_pin(pin)

    def gpio_write_port(self, port: str, value: int) -> None:
        """Write a value to all pins on a port.

        Args:
            port: "A" or "B".
            value: 8-bit value.

        Raises:
            RuntimeError: If GPIO not available.
        """
        if self._gpio is None:
            raise RuntimeError("GPIO not available")
        self._gpio.write_port(port, value)

    def gpio_read_port(self, port: str) -> int:
        """Read all pins on a port.

        Args:
            port: "A" or "B".

        Returns:
            8-bit value representing pin states.

        Raises:
            RuntimeError: If GPIO not available.
        """
        if self._gpio is None:
            raise RuntimeError("GPIO not available")
        return self._gpio.read_port(port)

    def gpio_write_all(self, value: int) -> None:
        """Write a value to all 16 GPIO pins.

        Args:
            value: 16-bit value (bits 0-7 = port A, bits 8-15 = port B).

        Raises:
            RuntimeError: If GPIO not available.
        """
        if self._gpio is None:
            raise RuntimeError("GPIO not available")
        self._gpio.write_all(value)

    def gpio_read_all(self) -> int:
        """Read all 16 GPIO pins.

        Returns:
            16-bit value representing all pin states.

        Raises:
            RuntimeError: If GPIO not available.
        """
        if self._gpio is None:
            raise RuntimeError("GPIO not available")
        return self._gpio.read_all()

    def gpio_set_pullup(self, pin: int, enabled: bool) -> None:
        """Enable or disable pull-up resistor on a pin.

        Args:
            pin: Pin number (0-15).
            enabled: True to enable, False to disable.

        Raises:
            RuntimeError: If GPIO not available.
        """
        if self._gpio is None:
            raise RuntimeError("GPIO not available")
        self._gpio.set_pullup(pin, enabled)

    # -------------------------------------------------------------------------
    # Failure Injection
    # -------------------------------------------------------------------------

    def failure_get_state(self) -> FailureState:
        """Get the current failure injection state.

        Returns:
            Current failure state including enabled, delay, offset, and active status.
        """
        return self._failure

    def failure_configure(
        self,
        delay_seconds: float | None = None,
        voltage_offset: float | None = None,
    ) -> None:
        """Configure failure injection parameters.

        Args:
            delay_seconds: New delay in seconds (0 to disable). If None, unchanged.
            voltage_offset: New voltage offset in volts. If None, unchanged.
        """
        if delay_seconds is not None:
            self._failure.delay_seconds = delay_seconds
            self._failure.enabled = delay_seconds > 0
            # Reset state if disabling or changing delay
            self._failure.start_time = None
            self._failure.active = False
            logger.info(
                "Failure injection %s (delay: %.1fs)",
                "enabled" if self._failure.enabled else "disabled",
                delay_seconds,
            )

        if voltage_offset is not None:
            self._failure.voltage_offset = voltage_offset
            logger.info("Failure voltage offset set to %.2fV", voltage_offset)

    def failure_reset(self) -> None:
        """Reset failure injection state without changing configuration.

        This clears the start time and active flag, allowing the failure
        sequence to begin again on the next DAC write.
        """
        self._failure.start_time = None
        self._failure.active = False
        logger.info("Failure injection state reset")

    def failure_time_until_active(self) -> float | None:
        """Get the time in seconds until failure injection activates.

        Returns:
            Seconds until failure activates, 0 if already active,
            or None if failure injection is disabled or not yet started.
        """
        if not self._failure.enabled:
            return None
        if self._failure.start_time is None:
            return None
        if self._failure.active:
            return 0.0
        elapsed = time.time() - self._failure.start_time
        remaining = self._failure.delay_seconds - elapsed
        return max(0.0, remaining)
