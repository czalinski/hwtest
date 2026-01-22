"""Telemetry data types."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from hwtest_core.types.common import ChannelId, SourceId, Timestamp


class ValueQuality(Enum):
    """Quality indicator for telemetry values."""

    GOOD = "good"
    UNCERTAIN = "uncertain"
    BAD = "bad"
    STALE = "stale"


@dataclass(frozen=True)
class TelemetryValue:
    """A single measurement value with metadata."""

    channel: ChannelId
    value: float
    unit: str
    source_timestamp: Timestamp
    publish_timestamp: Timestamp | None = None
    quality: ValueQuality = ValueQuality.GOOD

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
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
        """Create from dictionary."""
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
    """A batch of telemetry values from a single source."""

    source: SourceId
    values: tuple[TelemetryValue, ...]
    sequence: int

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "source": self.source,
            "values": [v.to_dict() for v in self.values],
            "sequence": self.sequence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetryMessage:
        """Create from dictionary."""
        return cls(
            source=SourceId(data["source"]),
            values=tuple(TelemetryValue.from_dict(v) for v in data["values"]),
            sequence=int(data["sequence"]),
        )

    def to_bytes(self) -> bytes:
        """Serialize to JSON bytes."""
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> TelemetryMessage:
        """Deserialize from JSON bytes."""
        return cls.from_dict(json.loads(data.decode("utf-8")))
