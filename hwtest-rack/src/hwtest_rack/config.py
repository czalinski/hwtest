"""YAML configuration loading for test racks.

This module provides configuration loading and parsing for test rack YAML files.
It handles parsing of rack metadata, instrument configurations, channel mappings,
and identity verification settings.

Example YAML configuration:
    rack:
      id: "orange-pi-5-integration"
      description: "Integration test rack"

    instruments:
      dc_psu_slot_3:
        driver: "hwtest_bkprecision.psu:create_instrument"
        identity:
          manufacturer: "B&K Precision"
          model: "9115"
        kwargs:
          visa_address: "TCPIP::192.168.1.100::5025::SOCKET"
          channels:
            - id: 1
              logical_name: "main_battery"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hwtest_rack.channel import ChannelRegistry, ChannelType, LogicalChannel


@dataclass(frozen=True)
class ExpectedIdentity:
    """Expected instrument identity for verification.

    Used during rack initialization to verify that the correct instrument
    is connected. The rack compares this against the actual identity
    returned by the instrument's get_identity() method.

    Attributes:
        manufacturer: Expected manufacturer name (e.g., "B&K Precision").
        model: Expected model name/number (e.g., "9115").
    """

    manufacturer: str
    model: str


@dataclass(frozen=True)
class ChannelConfig:
    """Configuration for a logical channel on an instrument.

    Maps a physical channel ID to a logical name and type, allowing
    test code to use meaningful names instead of numeric IDs.

    Attributes:
        id: Physical channel ID on the instrument (often 1-based for PSUs).
        logical_name: Logical name for this channel (e.g., "main_battery").
        channel_type: Type of channel (PSU, LOAD, DAQ_ANALOG, DAQ_DIGITAL).
        metadata: Additional channel-specific configuration (e.g., max voltage).
    """

    id: int
    logical_name: str
    channel_type: ChannelType
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InstrumentConfig:
    """Configuration for a single instrument in the rack.

    Contains all information needed to load, initialize, and verify
    an instrument, including its driver path, expected identity, and
    channel configurations.

    Attributes:
        name: Unique instrument name within the rack (e.g., "dc_psu_slot_3").
        driver: Driver path in "module:function" format
            (e.g., "hwtest_bkprecision.psu:create_instrument").
        identity: Expected identity for verification at initialization.
        kwargs: Additional keyword arguments passed to the driver factory.
        channels: Logical channel configurations (extracted from kwargs).
    """

    name: str
    driver: str
    identity: ExpectedIdentity
    kwargs: dict[str, Any] = field(default_factory=dict)
    channels: tuple[ChannelConfig, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RackConfig:
    """Configuration for a test rack.

    The top-level configuration object representing an entire test rack,
    including all instruments and the channel registry built from their
    channel configurations.

    Attributes:
        rack_id: Unique identifier for this rack (e.g., "orange-pi-5-integration").
        description: Human-readable description of the rack.
        instruments: Tuple of instrument configurations.
        channel_registry: Registry of logical channel names populated from
            instrument channel configurations.
    """

    rack_id: str
    description: str
    instruments: tuple[InstrumentConfig, ...]
    channel_registry: ChannelRegistry = field(default_factory=ChannelRegistry)


def _infer_channel_type(driver: str, channel_data: dict[str, Any]) -> ChannelType:
    """Infer the channel type from the driver path and channel data.

    Args:
        driver: Driver path (module:function format).
        channel_data: Channel configuration dictionary.

    Returns:
        Inferred ChannelType.
    """
    # Check for explicit type in channel data
    if "type" in channel_data:
        type_str = channel_data["type"].lower()
        try:
            return ChannelType(type_str)
        except ValueError:
            pass

    # Infer from driver path
    driver_lower = driver.lower()
    if "psu" in driver_lower or "power" in driver_lower:
        return ChannelType.PSU
    if "load" in driver_lower:
        return ChannelType.LOAD
    if "mcc118" in driver_lower or "mcc134" in driver_lower or "ads" in driver_lower:
        return ChannelType.DAQ_ANALOG
    if "mcc152" in driver_lower and channel_data.get("direction"):
        return ChannelType.DAQ_DIGITAL
    if "dac" in driver_lower or "adc" in driver_lower:
        return ChannelType.DAQ_ANALOG

    # Default to analog DAQ
    return ChannelType.DAQ_ANALOG


def _parse_channels(
    instrument_name: str,
    driver: str,
    kwargs: dict[str, Any],
) -> tuple[ChannelConfig, ...]:
    """Parse channel configurations from instrument kwargs.

    Looks for 'channels' key in kwargs and extracts logical names.
    Also checks for 'dio_channels' and 'analog_channels' for MCC 152.

    Args:
        instrument_name: Name of the instrument.
        driver: Driver path.
        kwargs: Instrument kwargs.

    Returns:
        Tuple of ChannelConfig objects.
    """
    channels: list[ChannelConfig] = []

    # Standard channels list (PSU, MCC 118, MCC 134, etc.)
    if "channels" in kwargs:
        for ch_data in kwargs["channels"]:
            if not isinstance(ch_data, dict):
                continue

            ch_id = ch_data.get("id")
            logical_name = ch_data.get("logical_name") or ch_data.get("name")

            if ch_id is None or logical_name is None:
                continue

            channel_type = _infer_channel_type(driver, ch_data)

            # Extract metadata (everything except id/name/type)
            metadata = {
                k: v for k, v in ch_data.items() if k not in ("id", "logical_name", "name", "type")
            }

            channels.append(
                ChannelConfig(
                    id=ch_id,
                    logical_name=logical_name,
                    channel_type=channel_type,
                    metadata=metadata,
                )
            )

    # MCC 152 dio_channels
    if "dio_channels" in kwargs:
        for ch_data in kwargs["dio_channels"]:
            if not isinstance(ch_data, dict):
                continue

            ch_id = ch_data.get("id")
            logical_name = ch_data.get("logical_name") or ch_data.get("name")

            if ch_id is None or logical_name is None:
                continue

            metadata = {k: v for k, v in ch_data.items() if k not in ("id", "logical_name", "name")}

            channels.append(
                ChannelConfig(
                    id=ch_id,
                    logical_name=logical_name,
                    channel_type=ChannelType.DAQ_DIGITAL,
                    metadata=metadata,
                )
            )

    # MCC 152 analog_channels
    if "analog_channels" in kwargs:
        for ch_data in kwargs["analog_channels"]:
            if not isinstance(ch_data, dict):
                continue

            ch_id = ch_data.get("id")
            logical_name = ch_data.get("logical_name") or ch_data.get("name")

            if ch_id is None or logical_name is None:
                continue

            metadata = {k: v for k, v in ch_data.items() if k not in ("id", "logical_name", "name")}

            channels.append(
                ChannelConfig(
                    id=ch_id,
                    logical_name=logical_name,
                    channel_type=ChannelType.DAQ_ANALOG,
                    metadata=metadata,
                )
            )

    return tuple(channels)


def _build_channel_registry(
    instruments: list[InstrumentConfig],
) -> ChannelRegistry:
    """Build a channel registry from instrument configurations.

    Args:
        instruments: List of instrument configurations.

    Returns:
        Populated ChannelRegistry.
    """
    registry = ChannelRegistry()

    for inst in instruments:
        for ch_config in inst.channels:
            logical_channel = LogicalChannel(
                logical_name=ch_config.logical_name,
                instrument_name=inst.name,
                channel_id=ch_config.id,
                channel_type=ch_config.channel_type,
                metadata=ch_config.metadata,
            )
            registry.register(logical_channel)

    return registry


def load_config(path: str | Path) -> RackConfig:
    """Load rack configuration from a YAML file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        Parsed rack configuration.

    Raises:
        FileNotFoundError: If the config file doesn't exist.
        ValueError: If the config is invalid or missing required fields.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError("Config must be a YAML mapping")

    # Parse rack section
    rack_section = data.get("rack", {})
    rack_id = rack_section.get("id")
    if not rack_id:
        raise ValueError("Missing required field: rack.id")
    description = rack_section.get("description", "")

    # Parse instruments section
    instruments_data = data.get("instruments", {})
    if not isinstance(instruments_data, dict):
        raise ValueError("instruments must be a mapping")

    instruments: list[InstrumentConfig] = []
    for name, inst_data in instruments_data.items():
        if not isinstance(inst_data, dict):
            raise ValueError(f"Instrument '{name}' must be a mapping")

        driver = inst_data.get("driver")
        if not driver:
            raise ValueError(f"Instrument '{name}' missing required field: driver")

        identity_data = inst_data.get("identity", {})
        if not identity_data.get("manufacturer"):
            raise ValueError(f"Instrument '{name}' missing required field: identity.manufacturer")
        if not identity_data.get("model"):
            raise ValueError(f"Instrument '{name}' missing required field: identity.model")

        identity = ExpectedIdentity(
            manufacturer=identity_data["manufacturer"],
            model=identity_data["model"],
        )

        kwargs = inst_data.get("kwargs", {})
        if not isinstance(kwargs, dict):
            raise ValueError(f"Instrument '{name}' kwargs must be a mapping")

        # Parse channel configurations
        channels = _parse_channels(name, driver, kwargs)

        instruments.append(
            InstrumentConfig(
                name=name,
                driver=driver,
                identity=identity,
                kwargs=kwargs,
                channels=channels,
            )
        )

    # Build the channel registry
    channel_registry = _build_channel_registry(instruments)

    return RackConfig(
        rack_id=rack_id,
        description=description,
        instruments=tuple(instruments),
        channel_registry=channel_registry,
    )
