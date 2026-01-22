"""Threshold types for monitoring."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from hwtest_core.types.common import ChannelId, StateId


class BoundType(Enum):
    """Type of threshold boundary."""

    INCLUSIVE = "inclusive"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True)
class ThresholdBound:
    """A single boundary for a threshold."""

    value: float
    bound_type: BoundType = BoundType.INCLUSIVE

    def check_low(self, test_value: float) -> bool:
        """Check if test_value satisfies this as a lower bound."""
        if self.bound_type == BoundType.INCLUSIVE:
            return test_value >= self.value
        return test_value > self.value

    def check_high(self, test_value: float) -> bool:
        """Check if test_value satisfies this as an upper bound."""
        if self.bound_type == BoundType.INCLUSIVE:
            return test_value <= self.value
        return test_value < self.value

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "value": self.value,
            "bound_type": self.bound_type.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThresholdBound:
        """Create from dictionary."""
        return cls(
            value=float(data["value"]),
            bound_type=BoundType(data.get("bound_type", "inclusive")),
        )


@dataclass(frozen=True)
class Threshold:
    """Defines acceptable range for a measurement."""

    channel: ChannelId
    low: ThresholdBound | None = None
    high: ThresholdBound | None = None

    def check(self, value: float) -> bool:
        """Return True if value is within threshold bounds."""
        if self.low is not None and not self.low.check_low(value):
            return False
        if self.high is not None and not self.high.check_high(value):
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "channel": self.channel,
            "low": self.low.to_dict() if self.low else None,
            "high": self.high.to_dict() if self.high else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Threshold:
        """Create from dictionary."""
        return cls(
            channel=ChannelId(data["channel"]),
            low=ThresholdBound.from_dict(data["low"]) if data.get("low") else None,
            high=ThresholdBound.from_dict(data["high"]) if data.get("high") else None,
        )


@dataclass(frozen=True)
class StateThresholds:
    """Collection of thresholds for a specific environmental state."""

    state_id: StateId
    thresholds: Mapping[ChannelId, Threshold]

    def get_threshold(self, channel: ChannelId) -> Threshold | None:
        """Get threshold for a channel, or None if not defined."""
        return self.thresholds.get(channel)

    def check_value(self, channel: ChannelId, value: float) -> bool | None:
        """Check if value is within threshold for channel.

        Returns:
            True if within threshold, False if out of threshold,
            None if no threshold defined for channel.
        """
        threshold = self.get_threshold(channel)
        if threshold is None:
            return None
        return threshold.check(value)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "state_id": self.state_id,
            "thresholds": {k: v.to_dict() for k, v in self.thresholds.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateThresholds:
        """Create from dictionary."""
        return cls(
            state_id=StateId(data["state_id"]),
            thresholds={
                ChannelId(k): Threshold.from_dict(v) for k, v in data["thresholds"].items()
            },
        )

    def to_bytes(self) -> bytes:
        """Serialize to JSON bytes."""
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> StateThresholds:
        """Deserialize from JSON bytes."""
        return cls.from_dict(json.loads(data.decode("utf-8")))
