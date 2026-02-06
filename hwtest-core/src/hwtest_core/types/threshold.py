"""Threshold types for telemetry monitoring.

This module provides threshold definitions for validating measurement values
against acceptable bounds. Thresholds can be organized by environmental state,
allowing different pass/fail criteria for different test conditions.

Classes:
    BoundType: Enumeration of boundary inclusion types.
    ThresholdBound: A single boundary value with inclusion type.
    Threshold: Low and/or high bounds for a measurement channel.
    StateThresholds: Collection of thresholds for a specific environmental state.

Example:
    >>> from hwtest_core.types import ChannelId, StateId
    >>> threshold = Threshold(
    ...     channel=ChannelId("voltage"),
    ...     low=ThresholdBound(value=4.5),
    ...     high=ThresholdBound(value=5.5)
    ... )
    >>> threshold.check(5.0)
    True
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from hwtest_core.types.common import ChannelId, StateId


class BoundType(Enum):
    """Type of threshold boundary inclusion.

    Determines whether a boundary value itself is considered within bounds.

    Attributes:
        INCLUSIVE: Boundary value is considered within bounds (<=, >=).
        EXCLUSIVE: Boundary value is considered out of bounds (<, >).
    """

    INCLUSIVE = "inclusive"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True)
class ThresholdBound:
    """A single boundary value for a threshold.

    Represents either a low or high bound with configurable inclusion behavior.

    Attributes:
        value: The boundary value.
        bound_type: Whether the boundary is inclusive or exclusive.
    """

    value: float
    bound_type: BoundType = BoundType.INCLUSIVE

    def check_low(self, test_value: float) -> bool:
        """Check if a value satisfies this as a lower bound.

        Args:
            test_value: The value to check.

        Returns:
            True if test_value >= value (inclusive) or > value (exclusive).
        """
        if self.bound_type == BoundType.INCLUSIVE:
            return test_value >= self.value
        return test_value > self.value

    def check_high(self, test_value: float) -> bool:
        """Check if a value satisfies this as an upper bound.

        Args:
            test_value: The value to check.

        Returns:
            True if test_value <= value (inclusive) or < value (exclusive).
        """
        if self.bound_type == BoundType.INCLUSIVE:
            return test_value <= self.value
        return test_value < self.value

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary.

        Returns:
            Dictionary with "value" and "bound_type" keys.
        """
        return {
            "value": self.value,
            "bound_type": self.bound_type.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThresholdBound:
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with "value" and optional "bound_type" keys.

        Returns:
            A ThresholdBound instance.
        """
        return cls(
            value=float(data["value"]),
            bound_type=BoundType(data.get("bound_type", "inclusive")),
        )


@dataclass(frozen=True)
class Threshold:
    """Defines acceptable range for a measurement channel.

    A threshold specifies optional low and high bounds for a measurement.
    Either or both bounds may be specified; None means no constraint.

    Attributes:
        channel: The measurement channel this threshold applies to.
        low: Lower bound, or None for no lower limit.
        high: Upper bound, or None for no upper limit.

    Example:
        >>> threshold = Threshold(
        ...     channel=ChannelId("temperature"),
        ...     low=ThresholdBound(value=-40.0),
        ...     high=ThresholdBound(value=85.0)
        ... )
        >>> threshold.check(25.0)
        True
    """

    channel: ChannelId
    low: ThresholdBound | None = None
    high: ThresholdBound | None = None

    def check(self, value: float) -> bool:
        """Check if a value is within the threshold bounds.

        Args:
            value: The measurement value to check.

        Returns:
            True if value satisfies both low and high bounds (if defined).
        """
        if self.low is not None and not self.low.check_low(value):
            return False
        if self.high is not None and not self.high.check_high(value):
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary.

        Returns:
            Dictionary with "channel", "low", and "high" keys.
        """
        return {
            "channel": self.channel,
            "low": self.low.to_dict() if self.low else None,
            "high": self.high.to_dict() if self.high else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Threshold:
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with "channel" and optional "low"/"high" keys.

        Returns:
            A Threshold instance.
        """
        return cls(
            channel=ChannelId(data["channel"]),
            low=ThresholdBound.from_dict(data["low"]) if data.get("low") else None,
            high=ThresholdBound.from_dict(data["high"]) if data.get("high") else None,
        )


@dataclass(frozen=True)
class StateThresholds:
    """Collection of thresholds for a specific environmental state.

    Organizes per-channel thresholds that apply during a particular state.
    Different environmental conditions (e.g., ambient vs. thermal stress)
    may have different acceptable measurement ranges.

    Attributes:
        state_id: Identifier for the environmental state.
        thresholds: Mapping from channel ID to threshold definition.

    Example:
        >>> thresholds = StateThresholds(
        ...     state_id=StateId("ambient"),
        ...     thresholds={
        ...         ChannelId("voltage"): Threshold(
        ...             channel=ChannelId("voltage"),
        ...             low=ThresholdBound(4.5),
        ...             high=ThresholdBound(5.5)
        ...         )
        ...     }
        ... )
    """

    state_id: StateId
    thresholds: Mapping[ChannelId, Threshold]

    def get_threshold(self, channel: ChannelId) -> Threshold | None:
        """Get the threshold for a specific channel.

        Args:
            channel: The channel ID to look up.

        Returns:
            The Threshold for the channel, or None if not defined.
        """
        return self.thresholds.get(channel)

    def check_value(self, channel: ChannelId, value: float) -> bool | None:
        """Check if a value is within threshold for a channel.

        Args:
            channel: The channel ID to check against.
            value: The measurement value to check.

        Returns:
            True if within threshold, False if out of threshold,
            None if no threshold is defined for the channel.
        """
        threshold = self.get_threshold(channel)
        if threshold is None:
            return None
        return threshold.check(value)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary.

        Returns:
            Dictionary with "state_id" and "thresholds" keys.
        """
        return {
            "state_id": self.state_id,
            "thresholds": {k: v.to_dict() for k, v in self.thresholds.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateThresholds:
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with "state_id" and "thresholds" keys.

        Returns:
            A StateThresholds instance.
        """
        return cls(
            state_id=StateId(data["state_id"]),
            thresholds={
                ChannelId(k): Threshold.from_dict(v) for k, v in data["thresholds"].items()
            },
        )

    def to_bytes(self) -> bytes:
        """Serialize to JSON bytes for network transmission.

        Returns:
            UTF-8 encoded JSON representation.
        """
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> StateThresholds:
        """Deserialize from JSON bytes.

        Args:
            data: UTF-8 encoded JSON representation.

        Returns:
            A StateThresholds instance.
        """
        return cls.from_dict(json.loads(data.decode("utf-8")))
