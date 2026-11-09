"""Common types used across hwtest modules.

``Timestamp`` and ``InstrumentIdentity`` are implemented in Rust
(``crates/hwtest-core``); ``DataType`` is a Python enum mirrored in Rust.

Type Aliases:
    SourceId: Identifies a data source (e.g., instrument or sensor).
    ChannelId: Identifies a measurement channel within a source.
    StateId: Identifies an environmental state.
    MonitorId: Identifies a telemetry monitor.

Classes:
    DataType: Data type codes for the binary streaming protocol.
    InstrumentIdentity: Instrument identification metadata (``*IDN?`` fields).
    Timestamp: Nanosecond timestamp with source tracking (immutable).
"""

from typing import NewType

from hwtest_core._enums import DataType
from hwtest_core._native import InstrumentIdentity, Timestamp

SourceId = NewType("SourceId", str)
"""Type alias for data source identifiers (e.g., instrument or sensor name)."""

ChannelId = NewType("ChannelId", str)
"""Type alias for measurement channel identifiers within a source."""

StateId = NewType("StateId", str)
"""Type alias for environmental state identifiers."""

MonitorId = NewType("MonitorId", str)
"""Type alias for telemetry monitor identifiers."""

__all__ = [
    "ChannelId",
    "DataType",
    "InstrumentIdentity",
    "MonitorId",
    "SourceId",
    "StateId",
    "Timestamp",
]
