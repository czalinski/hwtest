"""Logical channel naming and registry for test racks.

This module provides the mapping between logical channel names (used by test code)
and physical instrument channels. This allows test cases to use stable logical names
like "main_battery" or "cpu_power" that remain constant even if the physical
instruments change.

Hierarchy:
    Instrument (physical)
      └── Channel (logical name applied here)
            └── Fields (voltage, current, etc.)

Example YAML configuration:
    instruments:
      dc_psu_slot_3:
        driver: "hwtest_bkprecision.psu:create_instrument"
        kwargs:
          visa_address: "TCPIP::192.168.1.100::5025::SOCKET"
          channels:
            - id: 1
              logical_name: "main_battery"
            - id: 2
              logical_name: "cpu_power"

Example test code usage:
    # Access channel by logical name
    psu = rack.get_psu_channel("main_battery")
    await psu.set_voltage(12.0)
    await psu.set_output(True)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ChannelType(Enum):
    """Type of logical channel.

    Used to determine the appropriate interface protocol for a channel.

    Attributes:
        PSU: DC power supply output channel.
        LOAD: Electronic load input channel.
        DAQ_ANALOG: DAQ analog input/output channel.
        DAQ_DIGITAL: DAQ digital I/O channel.
    """

    PSU = "psu"  # DC power supply output
    LOAD = "load"  # Electronic load input
    DAQ_ANALOG = "daq_analog"  # DAQ analog input/output
    DAQ_DIGITAL = "daq_digital"  # DAQ digital I/O


@dataclass(frozen=True)
class LogicalChannel:
    """Maps a logical name to a physical instrument channel.

    Args:
        logical_name: The logical name used by test code (e.g., "main_battery").
        instrument_name: Physical instrument name in the rack config.
        channel_id: Physical channel ID on the instrument (often 1-based for PSUs).
        channel_type: Type of channel for interface selection.
        metadata: Optional additional configuration (e.g., max voltage, calibration).
    """

    logical_name: str
    instrument_name: str
    channel_id: int
    channel_type: ChannelType
    metadata: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        """Hash based on logical name only.

        Returns:
            Hash of the logical_name string.
        """
        return hash(self.logical_name)


@dataclass
class ChannelRegistry:
    """Registry mapping logical channel names to physical instrument channels.

    The registry is populated during rack initialization from the YAML config.
    Test code uses logical names to access channels, and the registry resolves
    these to the physical instrument and channel ID.

    Example:
        registry = ChannelRegistry()
        registry.register(LogicalChannel(
            logical_name="main_battery",
            instrument_name="dc_psu_slot_3",
            channel_id=1,
            channel_type=ChannelType.PSU,
        ))

        # Later, in test code:
        channel = registry.get("main_battery")
        instrument = rack.get_instrument(channel.instrument_name)
    """

    _channels: dict[str, LogicalChannel] = field(default_factory=dict)
    _by_instrument: dict[str, list[LogicalChannel]] = field(default_factory=dict)

    def register(self, channel: LogicalChannel) -> None:
        """Register a logical channel.

        Args:
            channel: The logical channel mapping.

        Raises:
            ValueError: If the logical name is already registered.
        """
        if channel.logical_name in self._channels:
            existing = self._channels[channel.logical_name]
            raise ValueError(
                f"Logical name '{channel.logical_name}' already registered "
                f"(instrument={existing.instrument_name}, channel={existing.channel_id})"
            )

        self._channels[channel.logical_name] = channel

        # Index by instrument for reverse lookup
        if channel.instrument_name not in self._by_instrument:
            self._by_instrument[channel.instrument_name] = []
        self._by_instrument[channel.instrument_name].append(channel)

        logger.debug(
            "Registered logical channel '%s' -> %s.channel[%d]",
            channel.logical_name,
            channel.instrument_name,
            channel.channel_id,
        )

    def get(self, logical_name: str) -> LogicalChannel | None:
        """Get a logical channel by name.

        Args:
            logical_name: The logical channel name.

        Returns:
            The LogicalChannel, or None if not found.
        """
        return self._channels.get(logical_name)

    def resolve(self, logical_name: str) -> tuple[str, int] | None:
        """Resolve a logical name to instrument name and channel ID.

        Args:
            logical_name: The logical channel name.

        Returns:
            Tuple of (instrument_name, channel_id), or None if not found.
        """
        channel = self._channels.get(logical_name)
        if channel is None:
            return None
        return (channel.instrument_name, channel.channel_id)

    def get_by_instrument(self, instrument_name: str) -> list[LogicalChannel]:
        """Get all logical channels for an instrument.

        Args:
            instrument_name: The physical instrument name.

        Returns:
            List of LogicalChannel mappings for this instrument.
        """
        return self._by_instrument.get(instrument_name, [])

    def get_by_type(self, channel_type: ChannelType) -> list[LogicalChannel]:
        """Get all logical channels of a specific type.

        Args:
            channel_type: The channel type to filter by.

        Returns:
            List of LogicalChannel mappings of this type.
        """
        return [ch for ch in self._channels.values() if ch.channel_type == channel_type]

    def list_all(self) -> list[LogicalChannel]:
        """List all registered logical channels.

        Returns:
            List of all LogicalChannel mappings.
        """
        return list(self._channels.values())

    def __contains__(self, logical_name: str) -> bool:
        """Check if a logical name is registered.

        Args:
            logical_name: The logical name to check.

        Returns:
            True if the logical name is registered.
        """
        return logical_name in self._channels

    def __len__(self) -> int:
        """Return the number of registered channels.

        Returns:
            The count of registered logical channels.
        """
        return len(self._channels)
