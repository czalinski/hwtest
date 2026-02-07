"""Test rack orchestration and REST API for hwtest.

This package provides infrastructure for managing hardware test racks,
including instrument discovery, lifecycle management, logical channel
naming, and a REST API for rack status and control.

Key components:
    - Rack: Main orchestrator that manages instrument lifecycle and provides
      access to instruments by name and channels by logical name.
    - ChannelRegistry: Maps logical channel names to physical instrument channels.
    - StreamAliaser: Republishes physical streams under logical channel names.
    - REST API: FastAPI-based service for rack status and instrument discovery.

Example:
    from hwtest_rack import Rack, load_config

    config = load_config("rack.yaml")
    rack = Rack(config)
    rack.initialize()

    # Access instrument by name
    psu = rack.get_instrument("dc_psu_slot_3")

    # Access channel by logical name
    battery = rack.get_psu_channel("main_battery")
    battery.set_voltage(12.0)
"""

from hwtest_rack.aliaser import AliasMapping, StreamAliaser
from hwtest_rack.channel import ChannelRegistry, ChannelType, LogicalChannel
from hwtest_rack.config import (
    CalibrationConfig,
    ChannelConfig,
    ExpectedIdentity,
    InstrumentConfig,
    RackConfig,
    load_config,
)
from hwtest_rack.loader import load_driver
from hwtest_rack.protocols import DcPsuChannel, ElectronicLoadChannel, MultiChannelPsu
from hwtest_rack.rack import Rack

__all__ = [
    # Config
    "CalibrationConfig",
    "ChannelConfig",
    "ExpectedIdentity",
    "InstrumentConfig",
    "RackConfig",
    "load_config",
    "load_driver",
    # Rack
    "Rack",
    # Channel registry
    "ChannelRegistry",
    "ChannelType",
    "LogicalChannel",
    # Stream aliaser
    "AliasMapping",
    "StreamAliaser",
    # Protocols
    "DcPsuChannel",
    "ElectronicLoadChannel",
    "MultiChannelPsu",
]
