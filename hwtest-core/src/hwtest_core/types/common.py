"""Common types used across hwtest modules.

This module provides foundational types used throughout the hwtest framework,
including type aliases for identifiers, data type enumerations, and core
data structures like Timestamp and InstrumentIdentity.

Type Aliases:
    SourceId: Identifies a data source (e.g., instrument or sensor).
    ChannelId: Identifies a measurement channel within a source.
    StateId: Identifies an environmental state.
    MonitorId: Identifies a telemetry monitor.

Classes:
    DataType: Enumeration of supported data types for streaming protocol.
    InstrumentIdentity: Instrument identification metadata.
    Timestamp: High-resolution timestamp with nanosecond precision.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import NewType

# Type aliases for clarity and type safety
SourceId = NewType("SourceId", str)
"""Type alias for data source identifiers (e.g., instrument or sensor name)."""

ChannelId = NewType("ChannelId", str)
"""Type alias for measurement channel identifiers within a source."""

StateId = NewType("StateId", str)
"""Type alias for environmental state identifiers."""

MonitorId = NewType("MonitorId", str)
"""Type alias for telemetry monitor identifiers."""


class DataType(Enum):
    """Data type codes for the binary streaming protocol.

    These codes identify the binary representation of values in streaming
    data messages. Each type has an associated size, struct format character,
    and category (signed/unsigned integer or floating point).

    Attributes:
        I8: 8-bit signed integer.
        I16: 16-bit signed integer.
        I32: 32-bit signed integer.
        I64: 64-bit signed integer.
        U8: 8-bit unsigned integer.
        U16: 16-bit unsigned integer.
        U32: 32-bit unsigned integer.
        U64: 64-bit unsigned integer.
        F32: 32-bit IEEE 754 floating point.
        F64: 64-bit IEEE 754 floating point.
    """

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
        """Return the size in bytes for this data type.

        Returns:
            Number of bytes required to store a value of this type.
        """
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
        """Return the struct format character for this data type.

        The format character is suitable for use with Python's struct module
        in big-endian mode (prepend '!' to the format string).

        Returns:
            Single character struct format code.
        """
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
        """Check if this is a signed integer type.

        Returns:
            True if this type is I8, I16, I32, or I64.
        """
        return self in (DataType.I8, DataType.I16, DataType.I32, DataType.I64)

    @property
    def is_unsigned(self) -> bool:
        """Check if this is an unsigned integer type.

        Returns:
            True if this type is U8, U16, U32, or U64.
        """
        return self in (DataType.U8, DataType.U16, DataType.U32, DataType.U64)

    @property
    def is_float(self) -> bool:
        """Check if this is a floating point type.

        Returns:
            True if this type is F32 or F64.
        """
        return self in (DataType.F32, DataType.F64)


@dataclass(frozen=True)
class InstrumentIdentity:
    """Instrument identification metadata.

    Represents the four standard fields returned by the SCPI ``*IDN?`` query,
    but is general enough for any instrument type. Used by the test rack to
    verify that connected instruments match the expected configuration.

    Attributes:
        manufacturer: Instrument manufacturer name (e.g., "BK Precision").
        model: Instrument model number or name (e.g., "9115").
        serial: Serial number string.
        firmware: Firmware or hardware version string.

    Example:
        >>> identity = InstrumentIdentity(
        ...     manufacturer="BK Precision",
        ...     model="9115",
        ...     serial="123456",
        ...     firmware="1.0.0"
        ... )
    """

    manufacturer: str
    model: str
    serial: str
    firmware: str


@dataclass(frozen=True)
class Timestamp:
    """High-resolution timestamp with nanosecond precision and source tracking.

    Timestamps are stored as nanoseconds since the Unix epoch (1970-01-01 00:00:00 UTC).
    The source field tracks where the timestamp originated (e.g., "local", "ntp", "ptp").

    Attributes:
        unix_ns: Nanoseconds since Unix epoch.
        source: Origin of the timestamp (default: "local").

    Example:
        >>> ts = Timestamp.now()
        >>> print(f"Time: {ts.to_datetime().isoformat()}")
        >>> print(f"Source: {ts.source}")
    """

    unix_ns: int
    source: str = "local"

    @classmethod
    def now(cls, source: str = "local") -> Timestamp:
        """Create a timestamp for the current time.

        Args:
            source: Origin identifier for this timestamp.

        Returns:
            A new Timestamp with the current time.
        """
        return cls(unix_ns=time.time_ns(), source=source)

    @classmethod
    def from_datetime(cls, dt: datetime, source: str = "local") -> Timestamp:
        """Create a timestamp from a datetime object.

        Args:
            dt: A datetime object to convert. Timezone-aware datetimes are
                recommended; naive datetimes are assumed to be in local time.
            source: Origin identifier for this timestamp.

        Returns:
            A new Timestamp corresponding to the given datetime.
        """
        unix_ns = int(dt.timestamp() * 1_000_000_000)
        return cls(unix_ns=unix_ns, source=source)

    def to_datetime(self) -> datetime:
        """Convert to a timezone-aware datetime object in UTC.

        Returns:
            A datetime object in UTC timezone.
        """
        return datetime.fromtimestamp(self.unix_ns / 1_000_000_000, tz=timezone.utc)

    @property
    def unix_seconds(self) -> float:
        """Return the timestamp as seconds since Unix epoch.

        Returns:
            Floating-point seconds with sub-second precision.
        """
        return self.unix_ns / 1_000_000_000

    @property
    def unix_ms(self) -> int:
        """Return the timestamp as milliseconds since Unix epoch.

        Returns:
            Integer milliseconds (truncated, not rounded).
        """
        return self.unix_ns // 1_000_000

    @property
    def unix_us(self) -> int:
        """Return the timestamp as microseconds since Unix epoch.

        Returns:
            Integer microseconds (truncated, not rounded).
        """
        return self.unix_ns // 1_000
