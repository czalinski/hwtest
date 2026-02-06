"""Telemetry data types for measurement values.

This module provides types for representing individual telemetry measurements
and batches of measurements from instrument sources. These types are used for
JSON-based telemetry transport (as opposed to the binary streaming protocol).

Classes:
    ValueQuality: Quality indicator for measurement values.
    TelemetryValue: A single measurement with metadata.
    TelemetryMessage: A batch of telemetry values from a source.

Example:
    >>> value = TelemetryValue(
    ...     channel=ChannelId("voltage"),
    ...     value=3.3,
    ...     unit="V",
    ...     source_timestamp=Timestamp.now()
    ... )
    >>> message = TelemetryMessage(
    ...     source=SourceId("psu"),
    ...     values=(value,),
    ...     sequence=1
    ... )
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from hwtest_core.types.common import ChannelId, SourceId, Timestamp


class ValueQuality(Enum):
    """Quality indicator for telemetry measurement values.

    Indicates the trustworthiness of a measurement value.

    Attributes:
        GOOD: Value is valid and current.
        UNCERTAIN: Value may be inaccurate (e.g., sensor warming up).
        BAD: Value is known to be invalid (e.g., sensor fault).
        STALE: Value is outdated (e.g., communication timeout).
    """

    GOOD = "good"
    UNCERTAIN = "uncertain"
    BAD = "bad"
    STALE = "stale"


@dataclass(frozen=True)
class TelemetryValue:
    """A single measurement value with associated metadata.

    Represents one measurement from an instrument channel, including
    timestamps for when the measurement was taken and published.

    Attributes:
        channel: Identifier of the measurement channel.
        value: The numeric measurement value.
        unit: Unit of measurement (e.g., "V", "A", "degC").
        source_timestamp: When the measurement was taken at the source.
        publish_timestamp: When the value was published (optional).
        quality: Indicator of value trustworthiness.
    """

    channel: ChannelId
    value: float
    unit: str
    source_timestamp: Timestamp
    publish_timestamp: Timestamp | None = None
    quality: ValueQuality = ValueQuality.GOOD

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary.

        Returns:
            Dictionary with all value fields and timestamps.
        """
        return {
            "channel": self.channel,
            "value": self.value,
            "unit": self.unit,
            "source_timestamp": self.source_timestamp.unix_ns,
            "source_timestamp_source": self.source_timestamp.source,
            "publish_timestamp": (
                self.publish_timestamp.unix_ns if self.publish_timestamp else None
            ),
            "publish_timestamp_source": (
                self.publish_timestamp.source if self.publish_timestamp else None
            ),
            "quality": self.quality.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetryValue:
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with value fields.

        Returns:
            A TelemetryValue instance.
        """
        publish_timestamp = None
        if data.get("publish_timestamp") is not None:
            publish_timestamp = Timestamp(
                unix_ns=data["publish_timestamp"],
                source=data.get("publish_timestamp_source", "local"),
            )

        return cls(
            channel=ChannelId(data["channel"]),
            value=float(data["value"]),
            unit=data["unit"],
            source_timestamp=Timestamp(
                unix_ns=data["source_timestamp"],
                source=data.get("source_timestamp_source", "local"),
            ),
            publish_timestamp=publish_timestamp,
            quality=ValueQuality(data.get("quality", "good")),
        )


@dataclass(frozen=True)
class TelemetryMessage:
    """A batch of telemetry values from a single source.

    Groups multiple measurement values from the same instrument source
    into a single message for efficient transport. Includes a sequence
    number for detecting dropped messages.

    Attributes:
        source: Identifier of the instrument source.
        values: Tuple of telemetry values in this batch.
        sequence: Monotonically increasing sequence number.
    """

    source: SourceId
    values: tuple[TelemetryValue, ...]
    sequence: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary.

        Returns:
            Dictionary with source, values array, and sequence.
        """
        return {
            "source": self.source,
            "values": [v.to_dict() for v in self.values],
            "sequence": self.sequence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetryMessage:
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with message fields.

        Returns:
            A TelemetryMessage instance.
        """
        return cls(
            source=SourceId(data["source"]),
            values=tuple(TelemetryValue.from_dict(v) for v in data["values"]),
            sequence=int(data["sequence"]),
        )

    def to_bytes(self) -> bytes:
        """Serialize to JSON bytes for network transmission.

        Returns:
            UTF-8 encoded JSON representation.
        """
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> TelemetryMessage:
        """Deserialize from JSON bytes.

        Args:
            data: UTF-8 encoded JSON representation.

        Returns:
            A TelemetryMessage instance.
        """
        return cls.from_dict(json.loads(data.decode("utf-8")))
