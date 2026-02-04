"""Rack orchestration class for managing instruments."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from hwtest_core.types.common import InstrumentIdentity

from hwtest_rack.channel import ChannelRegistry, ChannelType, LogicalChannel
from hwtest_rack.config import InstrumentConfig, RackConfig
from hwtest_rack.loader import load_driver
from hwtest_rack.models import (
    IdentityModel,
    InstrumentState,
    InstrumentStatus,
    RackStatus,
)
from hwtest_rack.protocols import DcPsuChannel

logger = logging.getLogger(__name__)


@runtime_checkable
class Instrument(Protocol):
    """Protocol for instruments that support identity queries."""

    def get_identity(self) -> InstrumentIdentity:
        """Return the instrument identity."""
        ...


@dataclass
class ManagedInstrument:
    """An instrument managed by the rack.

    Args:
        config: Instrument configuration.
        state: Current state of the instrument.
        instance: The instrument instance (if initialized).
        identity: The verified identity (if available).
        error: Error message (if in error state).
    """

    config: InstrumentConfig
    state: InstrumentState = InstrumentState.PENDING
    instance: Any = None
    identity: InstrumentIdentity | None = None
    error: str | None = None


@dataclass
class Rack:
    """Test rack orchestrator.

    Manages loading, initializing, and monitoring instruments.
    Provides access to instruments by name and channels by logical name.

    Args:
        config: Rack configuration.

    Example:
        config = load_config("rack.yaml")
        rack = Rack(config)
        rack.initialize()

        # Access instrument by name
        psu = rack.get_instrument("dc_psu_slot_3")

        # Access channel by logical name
        battery = rack.get_psu_channel("main_battery")
        battery.set_voltage(12.0)
    """

    config: RackConfig
    _instruments: dict[str, ManagedInstrument] = field(default_factory=dict, init=False)
    _state: str = field(default="initializing", init=False)

    def __post_init__(self) -> None:
        """Initialize managed instruments from config."""
        for inst_config in self.config.instruments:
            self._instruments[inst_config.name] = ManagedInstrument(config=inst_config)

    @property
    def rack_id(self) -> str:
        """Return the rack ID."""
        return self.config.rack_id

    @property
    def state(self) -> str:
        """Return the overall rack state."""
        return self._state

    def initialize(self) -> None:
        """Initialize all instruments.

        Loads drivers, creates instances, and verifies identities.
        Sets the rack state based on initialization results.
        """
        all_ready = True

        for name, managed in self._instruments.items():
            managed.state = InstrumentState.INITIALIZING

            try:
                # Load the driver factory
                factory = load_driver(managed.config.driver)

                # Create the instrument instance
                instance = factory(**managed.config.kwargs)
                managed.instance = instance

                # Open/start the instrument if needed
                if hasattr(instance, "open"):
                    instance.open()
                elif hasattr(instance, "start"):
                    # For async instruments, we can't await here
                    # They should be started separately
                    pass

                # Verify identity if the instrument supports it
                if isinstance(instance, Instrument):
                    identity = instance.get_identity()
                    managed.identity = identity

                    # Check against expected identity
                    expected = managed.config.identity
                    if identity.manufacturer != expected.manufacturer:
                        managed.state = InstrumentState.ERROR
                        managed.error = (
                            f"Manufacturer mismatch: expected '{expected.manufacturer}', "
                            f"got '{identity.manufacturer}'"
                        )
                        all_ready = False
                        logger.error("Instrument %s: %s", name, managed.error)
                        continue

                    if identity.model != expected.model:
                        managed.state = InstrumentState.ERROR
                        managed.error = (
                            f"Model mismatch: expected '{expected.model}', "
                            f"got '{identity.model}'"
                        )
                        all_ready = False
                        logger.error("Instrument %s: %s", name, managed.error)
                        continue

                managed.state = InstrumentState.READY
                logger.info(
                    "Instrument %s initialized: %s %s (S/N: %s)",
                    name,
                    managed.identity.manufacturer if managed.identity else "unknown",
                    managed.identity.model if managed.identity else "unknown",
                    managed.identity.serial if managed.identity else "unknown",
                )

            except Exception as exc:  # pylint: disable=broad-exception-caught
                managed.state = InstrumentState.ERROR
                managed.error = str(exc)
                all_ready = False
                logger.error("Failed to initialize instrument %s: %s", name, exc)

        self._state = "ready" if all_ready else "error"

    def close(self) -> None:
        """Close all instruments."""
        for name, managed in self._instruments.items():
            if managed.instance is not None:
                try:
                    if hasattr(managed.instance, "close"):
                        managed.instance.close()
                    managed.state = InstrumentState.CLOSED
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.warning("Error closing instrument %s: %s", name, exc)

        self._state = "closed"

    def get_instrument(self, name: str) -> Any | None:
        """Get an instrument instance by name.

        Args:
            name: Instrument name.

        Returns:
            The instrument instance, or None if not found or not ready.
        """
        managed = self._instruments.get(name)
        if managed and managed.state == InstrumentState.READY:
            return managed.instance
        return None

    def get_status(self) -> RackStatus:
        """Get the current rack status.

        Returns:
            Status of the rack and all instruments.
        """
        instruments: list[InstrumentStatus] = []

        for managed in self._instruments.values():
            identity_model = None
            if managed.identity:
                identity_model = IdentityModel(
                    manufacturer=managed.identity.manufacturer,
                    model=managed.identity.model,
                    serial=managed.identity.serial,
                    firmware=managed.identity.firmware,
                )

            instruments.append(
                InstrumentStatus(
                    name=managed.config.name,
                    driver=managed.config.driver,
                    state=managed.state,
                    expected_manufacturer=managed.config.identity.manufacturer,
                    expected_model=managed.config.identity.model,
                    identity=identity_model,
                    error=managed.error,
                )
            )

        return RackStatus(
            rack_id=self.config.rack_id,
            description=self.config.description,
            state=self._state,
            instruments=instruments,
        )

    def get_instrument_status(self, name: str) -> InstrumentStatus | None:
        """Get the status of a specific instrument.

        Args:
            name: Instrument name.

        Returns:
            Instrument status, or None if not found.
        """
        managed = self._instruments.get(name)
        if not managed:
            return None

        identity_model = None
        if managed.identity:
            identity_model = IdentityModel(
                manufacturer=managed.identity.manufacturer,
                model=managed.identity.model,
                serial=managed.identity.serial,
                firmware=managed.identity.firmware,
            )

        return InstrumentStatus(
            name=managed.config.name,
            driver=managed.config.driver,
            state=managed.state,
            expected_manufacturer=managed.config.identity.manufacturer,
            expected_model=managed.config.identity.model,
            identity=identity_model,
            error=managed.error,
        )

    def list_instruments(self) -> list[InstrumentStatus]:
        """List all instrument statuses.

        Returns:
            List of instrument statuses.
        """
        return self.get_status().instruments

    # -------------------------------------------------------------------------
    # Logical Channel Access
    # -------------------------------------------------------------------------

    @property
    def channel_registry(self) -> ChannelRegistry:
        """Return the channel registry for this rack."""
        return self.config.channel_registry

    def get_logical_channel(self, logical_name: str) -> LogicalChannel | None:
        """Get a logical channel by name.

        Args:
            logical_name: The logical channel name.

        Returns:
            LogicalChannel info, or None if not found.
        """
        return self.config.channel_registry.get(logical_name)

    def resolve_channel(self, logical_name: str) -> tuple[Any, int] | None:
        """Resolve a logical name to an instrument instance and channel ID.

        Args:
            logical_name: The logical channel name.

        Returns:
            Tuple of (instrument_instance, channel_id), or None if not found.
        """
        channel = self.config.channel_registry.get(logical_name)
        if channel is None:
            return None

        instrument = self.get_instrument(channel.instrument_name)
        if instrument is None:
            return None

        return (instrument, channel.channel_id)

    def get_psu_channel(self, logical_name: str) -> DcPsuChannel | None:
        """Get a PSU channel by logical name.

        The instrument must implement the MultiChannelPsu protocol
        (have a get_channel_by_name method).

        Args:
            logical_name: The logical channel name.

        Returns:
            DcPsuChannel interface, or None if not found.
        """
        channel = self.config.channel_registry.get(logical_name)
        if channel is None:
            logger.warning("Logical channel '%s' not found", logical_name)
            return None

        if channel.channel_type != ChannelType.PSU:
            logger.warning(
                "Channel '%s' is type %s, not PSU",
                logical_name,
                channel.channel_type.value,
            )
            return None

        instrument = self.get_instrument(channel.instrument_name)
        if instrument is None:
            logger.warning(
                "Instrument '%s' for channel '%s' not ready",
                channel.instrument_name,
                logical_name,
            )
            return None

        # Try to get channel by name (MultiChannelPsu protocol)
        if hasattr(instrument, "get_channel_by_name"):
            psu_channel = instrument.get_channel_by_name(logical_name)
            if psu_channel is not None:
                return psu_channel

        # Try to get channel by ID
        if hasattr(instrument, "get_channel"):
            try:
                return instrument.get_channel(channel.channel_id)
            except (KeyError, IndexError):
                pass

        logger.warning(
            "Instrument '%s' does not support channel access",
            channel.instrument_name,
        )
        return None

    def list_logical_channels(
        self,
        channel_type: ChannelType | None = None,
    ) -> list[LogicalChannel]:
        """List all logical channels, optionally filtered by type.

        Args:
            channel_type: Optional type filter.

        Returns:
            List of LogicalChannel info.
        """
        if channel_type is None:
            return self.config.channel_registry.list_all()
        return self.config.channel_registry.get_by_type(channel_type)

    def list_psu_channels(self) -> list[str]:
        """List all PSU logical channel names.

        Returns:
            List of logical names for PSU channels.
        """
        channels = self.config.channel_registry.get_by_type(ChannelType.PSU)
        return [ch.logical_name for ch in channels]
