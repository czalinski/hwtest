"""Monitor result types."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from hwtest_core.types.common import ChannelId, MonitorId, StateId, Timestamp
from hwtest_core.types.threshold import Threshold


class MonitorVerdict(Enum):
    """Result of a monitor evaluation."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


@dataclass(frozen=True)
class ThresholdViolation:
    """Details of a threshold violation."""

    channel: ChannelId
    value: float
    threshold: Threshold
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "channel": self.channel,
            "value": self.value,
            "threshold": self.threshold.to_dict(),
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThresholdViolation:
        """Create from dictionary."""
        return cls(
            channel=ChannelId(data["channel"]),
            value=float(data["value"]),
            threshold=Threshold.from_dict(data["threshold"]),
            message=data.get("message", ""),
        )


@dataclass(frozen=True)
class MonitorResult:
    """Result of a single monitor evaluation."""

    monitor_id: MonitorId
    verdict: MonitorVerdict
    timestamp: Timestamp
    state_id: StateId
    violations: tuple[ThresholdViolation, ...] = ()
    message: str = ""

    @property
    def passed(self) -> bool:
        """Return True if the verdict is PASS."""
        return self.verdict == MonitorVerdict.PASS

    @property
    def failed(self) -> bool:
        """Return True if the verdict is FAIL."""
        return self.verdict == MonitorVerdict.FAIL

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "monitor_id": self.monitor_id,
            "verdict": self.verdict.value,
            "timestamp": self.timestamp.unix_ns,
            "timestamp_source": self.timestamp.source,
            "state_id": self.state_id,
            "violations": [v.to_dict() for v in self.violations],
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MonitorResult:
        """Create from dictionary."""
        return cls(
            monitor_id=MonitorId(data["monitor_id"]),
            verdict=MonitorVerdict(data["verdict"]),
            timestamp=Timestamp(
                unix_ns=data["timestamp"],
                source=data.get("timestamp_source", "local"),
            ),
            state_id=StateId(data["state_id"]),
            violations=tuple(ThresholdViolation.from_dict(v) for v in data.get("violations", [])),
            message=data.get("message", ""),
        )

    def to_bytes(self) -> bytes:
        """Serialize to JSON bytes."""
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> MonitorResult:
        """Deserialize from JSON bytes."""
        return cls.from_dict(json.loads(data.decode("utf-8")))
