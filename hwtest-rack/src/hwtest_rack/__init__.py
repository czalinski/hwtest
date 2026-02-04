"""Test rack orchestration and REST API for hwtest."""

from hwtest_rack.aliaser import AliasMapping, StreamAliaser
from hwtest_rack.channel import ChannelRegistry, ChannelType, LogicalChannel
from hwtest_rack.config import (
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
