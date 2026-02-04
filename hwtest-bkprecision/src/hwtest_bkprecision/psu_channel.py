"""BK Precision DC PSU channel wrapper for logical naming.

This module provides a per-channel interface to BK Precision DC power supplies,
allowing test code to interact with individual channels by logical name.

Example:
    # Create PSU with logical channel names
    psu = create_multichannel_instrument(
        visa_address="TCPIP::192.168.1.100::5025::SOCKET",
        channels=[
            {"id": 1, "logical_name": "main_battery"},
            {"id": 2, "logical_name": "cpu_power"},
        ],
    )

    # Access channels by logical name
    battery = psu.get_channel_by_name("main_battery")
    battery.set_voltage(12.0)
    battery.set_output(True)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any

from hwtest_bkprecision.psu import BkDcPsu, create_instrument

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PsuChannelConfig:
    """Configuration for a PSU channel.

    Args:
        id: Physical channel ID (1-based for BK Precision).
        logical_name: Logical name for this channel.
        max_voltage: Optional maximum voltage limit.
        max_current: Optional maximum current limit.
    """

    id: int
    logical_name: str
    max_voltage: float | None = None
    max_current: float | None = None


class BkDcPsuChannel:
    """A single channel of a BK Precision DC power supply.

    Implements the DcPsuChannel protocol for logical channel access.
    Thread-safe via a lock shared with other channels on the same PSU.

    Args:
        psu: The underlying BkDcPsu driver.
        config: Channel configuration.
        lock: Threading lock shared across all channels on this PSU.
    """

    def __init__(
        self,
        psu: BkDcPsu,
        config: PsuChannelConfig,
        lock: threading.Lock,
    ) -> None:
        self._psu = psu
        self._config = config
        self._lock = lock

    @property
    def logical_name(self) -> str:
        """The logical name of this channel."""
        return self._config.logical_name

    @property
    def channel_id(self) -> int:
        """The physical channel ID on the instrument."""
        return self._config.id

    def _select(self) -> None:
        """Select this channel on the PSU (must hold lock)."""
        self._psu.select_channel(self._config.id)

    # -- Setpoint commands --

    def set_voltage(self, voltage: float) -> None:
        """Set the output voltage setpoint.

        Args:
            voltage: Voltage in volts.

        Raises:
            ValueError: If voltage exceeds max_voltage limit.
        """
        if self._config.max_voltage is not None and voltage > self._config.max_voltage:
            raise ValueError(
                f"Voltage {voltage}V exceeds limit {self._config.max_voltage}V "
                f"for channel '{self._config.logical_name}'"
            )

        with self._lock:
            self._select()
            self._psu.set_voltage(voltage)
            logger.debug(
                "Set voltage on '%s' (ch%d): %.3fV",
                self._config.logical_name,
                self._config.id,
                voltage,
            )

    def set_current_limit(self, current: float) -> None:
        """Set the output current limit.

        Args:
            current: Current in amps.

        Raises:
            ValueError: If current exceeds max_current limit.
        """
        if self._config.max_current is not None and current > self._config.max_current:
            raise ValueError(
                f"Current {current}A exceeds limit {self._config.max_current}A "
                f"for channel '{self._config.logical_name}'"
            )

        with self._lock:
            self._select()
            self._psu.set_current(current)
            logger.debug(
                "Set current limit on '%s' (ch%d): %.3fA",
                self._config.logical_name,
                self._config.id,
                current,
            )

    def set_output(self, enabled: bool) -> None:
        """Enable or disable the output.

        Args:
            enabled: True to enable, False to disable.
        """
        with self._lock:
            self._select()
            if enabled:
                self._psu.enable_output()
            else:
                self._psu.disable_output()
            logger.debug(
                "Set output on '%s' (ch%d): %s",
                self._config.logical_name,
                self._config.id,
                "ON" if enabled else "OFF",
            )

    def apply(self, voltage: float, current: float) -> None:
        """Set voltage and current in a single command.

        Args:
            voltage: Voltage in volts.
            current: Current in amps.
        """
        if self._config.max_voltage is not None and voltage > self._config.max_voltage:
            raise ValueError(f"Voltage {voltage}V exceeds limit {self._config.max_voltage}V")
        if self._config.max_current is not None and current > self._config.max_current:
            raise ValueError(f"Current {current}A exceeds limit {self._config.max_current}A")

        with self._lock:
            self._psu.apply(self._config.id, voltage, current)
            logger.debug(
                "Applied to '%s' (ch%d): %.3fV, %.3fA",
                self._config.logical_name,
                self._config.id,
                voltage,
                current,
            )

    # -- Setpoint queries --

    def get_voltage(self) -> float:
        """Get the output voltage setpoint.

        Returns:
            Voltage setpoint in volts.
        """
        with self._lock:
            self._select()
            return self._psu.get_voltage()

    def get_current_limit(self) -> float:
        """Get the output current limit.

        Returns:
            Current limit in amps.
        """
        with self._lock:
            self._select()
            return self._psu.get_current()

    def is_output_enabled(self) -> bool:
        """Check if the output is enabled.

        Returns:
            True if output is enabled.
        """
        with self._lock:
            self._select()
            return self._psu.is_output_enabled()

    # -- Measurements --

    def measure_voltage(self) -> float:
        """Measure the actual output voltage.

        Returns:
            Measured voltage in volts.
        """
        with self._lock:
            self._select()
            return self._psu.measure_voltage()

    def measure_current(self) -> float:
        """Measure the actual output current.

        Returns:
            Measured current in amps.
        """
        with self._lock:
            self._select()
            return self._psu.measure_current()

    def measure_power(self) -> float:
        """Measure the actual output power.

        Returns:
            Measured power in watts.
        """
        with self._lock:
            self._select()
            return self._psu.measure_power()


class BkMultiChannelPsu:
    """Multi-channel wrapper for BK Precision DC power supplies.

    Provides access to individual channels by ID or logical name.
    Implements the MultiChannelPsu protocol.

    Args:
        psu: The underlying BkDcPsu driver.
        channels: Channel configurations with logical names.
    """

    def __init__(
        self,
        psu: BkDcPsu,
        channels: tuple[PsuChannelConfig, ...],
    ) -> None:
        self._psu = psu
        self._lock = threading.Lock()
        self._channels_by_id: dict[int, BkDcPsuChannel] = {}
        self._channels_by_name: dict[str, BkDcPsuChannel] = {}

        for config in channels:
            channel = BkDcPsuChannel(psu, config, self._lock)
            self._channels_by_id[config.id] = channel
            self._channels_by_name[config.logical_name] = channel

    def get_identity(self) -> Any:
        """Get the instrument identity.

        Returns:
            InstrumentIdentity from the underlying PSU.
        """
        return self._psu.get_identity()

    def close(self) -> None:
        """Close the underlying PSU connection."""
        self._psu.close()

    def get_channel(self, channel_id: int) -> BkDcPsuChannel:
        """Get a channel interface by physical channel ID.

        Args:
            channel_id: Physical channel ID (1-based).

        Returns:
            BkDcPsuChannel interface.

        Raises:
            KeyError: If channel ID not configured.
        """
        if channel_id not in self._channels_by_id:
            raise KeyError(f"Channel {channel_id} not configured")
        return self._channels_by_id[channel_id]

    def get_channel_by_name(self, logical_name: str) -> BkDcPsuChannel | None:
        """Get a channel interface by logical name.

        Args:
            logical_name: Logical channel name.

        Returns:
            BkDcPsuChannel interface, or None if name not registered.
        """
        return self._channels_by_name.get(logical_name)

    def list_channels(self) -> list[BkDcPsuChannel]:
        """List all available channels.

        Returns:
            List of BkDcPsuChannel interfaces.
        """
        return list(self._channels_by_id.values())

    def list_logical_names(self) -> list[str]:
        """List all logical channel names.

        Returns:
            List of logical names.
        """
        return list(self._channels_by_name.keys())


def create_multichannel_instrument(
    visa_address: str,
    channels: list[dict[str, Any]],
) -> BkMultiChannelPsu:
    """Create a multi-channel BK Precision PSU with logical naming.

    Factory function for the test rack.

    Args:
        visa_address: VISA resource string.
        channels: List of channel configs, each with:
            - id: Physical channel ID (1-based)
            - logical_name: Logical name for the channel
            - max_voltage: Optional voltage limit
            - max_current: Optional current limit

    Returns:
        Configured BkMultiChannelPsu instance.

    Example YAML config:
        dc_psu_slot_3:
          driver: "hwtest_bkprecision.psu_channel:create_multichannel_instrument"
          kwargs:
            visa_address: "TCPIP::192.168.1.100::5025::SOCKET"
            channels:
              - id: 1
                logical_name: "main_battery"
                max_voltage: 15.0
                max_current: 10.0
              - id: 2
                logical_name: "cpu_power"
    """
    psu = create_instrument(visa_address)

    channel_configs = tuple(
        PsuChannelConfig(
            id=ch["id"],
            logical_name=ch["logical_name"],
            max_voltage=ch.get("max_voltage"),
            max_current=ch.get("max_current"),
        )
        for ch in channels
    )

    return BkMultiChannelPsu(psu, channel_configs)
