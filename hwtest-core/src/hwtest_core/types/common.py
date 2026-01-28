"""Common types used across hwtest modules."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import NewType

# Type aliases for clarity and type safety
SourceId = NewType("SourceId", str)
ChannelId = NewType("ChannelId", str)
StateId = NewType("StateId", str)
MonitorId = NewType("MonitorId", str)


class DataType(Enum):
    """Data type codes for streaming protocol."""

    I8 = 0x01
    I16 = 0x02
    I32 = 0x03
    I64 = 0x04
    U8 = 0x05
    U16 = 0x06
    U32 = 0x07
    U64 = 0x08
    F32 = 0x09
    F64 = 0x0A

    @property
    def size(self) -> int:
        """Return the size in bytes for this data type."""
        sizes = {
            DataType.I8: 1,
            DataType.I16: 2,
            DataType.I32: 4,
            DataType.I64: 8,
            DataType.U8: 1,
            DataType.U16: 2,
            DataType.U32: 4,
            DataType.U64: 8,
            DataType.F32: 4,
            DataType.F64: 8,
        }
        return sizes[self]

    @property
    def struct_format(self) -> str:
        """Return the struct format character for this data type (big-endian)."""
        formats = {
            DataType.I8: "b",
            DataType.I16: "h",
            DataType.I32: "i",
            DataType.I64: "q",
            DataType.U8: "B",
            DataType.U16: "H",
            DataType.U32: "I",
            DataType.U64: "Q",
            DataType.F32: "f",
            DataType.F64: "d",
        }
        return formats[self]

    @property
    def is_signed(self) -> bool:
        """Return True if this is a signed integer type."""
        return self in (DataType.I8, DataType.I16, DataType.I32, DataType.I64)

    @property
    def is_unsigned(self) -> bool:
        """Return True if this is an unsigned integer type."""
        return self in (DataType.U8, DataType.U16, DataType.U32, DataType.U64)

    @property
    def is_float(self) -> bool:
        """Return True if this is a floating point type."""
        return self in (DataType.F32, DataType.F64)


@dataclass(frozen=True)
class InstrumentIdentity:
    """Instrument identification metadata.

    Represents the four standard fields returned by the SCPI ``*IDN?`` query,
    but is general enough for any instrument type.

    Args:
        manufacturer: Instrument manufacturer name.
        model: Instrument model number or name.
        serial: Serial number string.
        firmware: Firmware or hardware version string.
    """

    manufacturer: str
    model: str
    serial: str
    firmware: str


@dataclass(frozen=True)
class Timestamp:
    """High-resolution timestamp with source tracking."""

    unix_ns: int
    source: str = "local"

    @classmethod
    def now(cls, source: str = "local") -> Timestamp:
        """Create a timestamp for the current time."""
        return cls(unix_ns=time.time_ns(), source=source)

    @classmethod
    def from_datetime(cls, dt: datetime, source: str = "local") -> Timestamp:
        """Create a timestamp from a datetime object."""
        unix_ns = int(dt.timestamp() * 1_000_000_000)
        return cls(unix_ns=unix_ns, source=source)

    def to_datetime(self) -> datetime:
        """Convert to a datetime object (UTC)."""
        return datetime.fromtimestamp(self.unix_ns / 1_000_000_000, tz=timezone.utc)

    @property
    def unix_seconds(self) -> float:
        """Return the timestamp as seconds since Unix epoch."""
        return self.unix_ns / 1_000_000_000

    @property
    def unix_ms(self) -> int:
        """Return the timestamp as milliseconds since Unix epoch."""
        return self.unix_ns // 1_000_000

    @property
    def unix_us(self) -> int:
        """Return the timestamp as microseconds since Unix epoch."""
        return self.unix_ns // 1_000
