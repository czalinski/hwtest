"""Protocol definitions for logical channel interfaces.

These protocols define the interface for logical channels that wrap physical
instrument channels. Test code uses these interfaces to interact with channels
by logical name, independent of the physical instrument implementation.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class DcPsuChannel(Protocol):
    """Protocol for a single DC power supply channel (logical device).

    This interface represents one output channel of a DC power supply,
    accessed by its logical name. The implementation handles mapping
    to the physical instrument and channel ID.

    Example:
        # Get a PSU channel by logical name
        battery = rack.get_psu_channel("main_battery")

        # Control the output
        await battery.set_voltage(12.0)
        await battery.set_current_limit(5.0)
        await battery.set_output(True)

        # Read measurements
        voltage = await battery.measure_voltage()
        current = await battery.measure_current()
    """

    @property
    def logical_name(self) -> str:
        """The logical name of this channel."""
        ...

    @property
    def channel_id(self) -> int:
        """The physical channel ID on the instrument."""
        ...

    # -- Setpoint commands --

    def set_voltage(self, voltage: float) -> None:
        """Set the output voltage setpoint.

        Args:
            voltage: Voltage in volts.
        """
        ...

    def set_current_limit(self, current: float) -> None:
        """Set the output current limit.

        Args:
            current: Current in amps.
        """
        ...

    def set_output(self, enabled: bool) -> None:
        """Enable or disable the output.

        Args:
            enabled: True to enable, False to disable.
        """
        ...

    # -- Setpoint queries --

    def get_voltage(self) -> float:
        """Get the output voltage setpoint.

        Returns:
            Voltage setpoint in volts.
        """
        ...

    def get_current_limit(self) -> float:
        """Get the output current limit.

        Returns:
            Current limit in amps.
        """
        ...

    def is_output_enabled(self) -> bool:
        """Check if the output is enabled.

        Returns:
            True if output is enabled.
        """
        ...

    # -- Measurements --

    def measure_voltage(self) -> float:
        """Measure the actual output voltage.

        Returns:
            Measured voltage in volts.
        """
        ...

    def measure_current(self) -> float:
        """Measure the actual output current.

        Returns:
            Measured current in amps.
        """
        ...

    def measure_power(self) -> float:
        """Measure the actual output power.

        Returns:
            Measured power in watts.
        """
        ...


@runtime_checkable
class MultiChannelPsu(Protocol):
    """Protocol for a multi-channel DC power supply instrument.

    This interface is implemented by PSU drivers that support multiple
    output channels. It provides access to individual channels by ID
    or logical name.
    """

    def get_channel(self, channel_id: int) -> DcPsuChannel:
        """Get a channel interface by physical channel ID.

        Args:
            channel_id: Physical channel ID (typically 1-based).

        Returns:
            DcPsuChannel interface for the channel.
        """
        ...

    def get_channel_by_name(self, logical_name: str) -> DcPsuChannel | None:
        """Get a channel interface by logical name.

        Args:
            logical_name: Logical channel name.

        Returns:
            DcPsuChannel interface, or None if name not registered.
        """
        ...

    def list_channels(self) -> list[DcPsuChannel]:
        """List all available channels.

        Returns:
            List of DcPsuChannel interfaces.
        """
        ...


@runtime_checkable
class ElectronicLoadChannel(Protocol):
    """Protocol for a single electronic load channel (logical device).

    This interface represents one input channel of an electronic load,
    accessed by its logical name.
    """

    @property
    def logical_name(self) -> str:
        """The logical name of this channel."""
        ...

    @property
    def channel_id(self) -> int:
        """The physical channel ID on the instrument."""
        ...

    def set_mode(self, mode: str) -> None:
        """Set the load mode (CC, CV, CR, CP).

        Args:
            mode: Load mode string.
        """
        ...

    def set_current(self, current: float) -> None:
        """Set the load current (CC mode).

        Args:
            current: Current in amps.
        """
        ...

    def set_voltage(self, voltage: float) -> None:
        """Set the load voltage (CV mode).

        Args:
            voltage: Voltage in volts.
        """
        ...

    def set_resistance(self, resistance: float) -> None:
        """Set the load resistance (CR mode).

        Args:
            resistance: Resistance in ohms.
        """
        ...

    def set_power(self, power: float) -> None:
        """Set the load power (CP mode).

        Args:
            power: Power in watts.
        """
        ...

    def set_input(self, enabled: bool) -> None:
        """Enable or disable the load input.

        Args:
            enabled: True to enable, False to disable.
        """
        ...

    def measure_voltage(self) -> float:
        """Measure the input voltage.

        Returns:
            Measured voltage in volts.
        """
        ...

    def measure_current(self) -> float:
        """Measure the input current.

        Returns:
            Measured current in amps.
        """
        ...

    def measure_power(self) -> float:
        """Measure the input power.

        Returns:
            Measured power in watts.
        """
        ...
