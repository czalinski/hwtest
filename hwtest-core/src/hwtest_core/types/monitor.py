"""Monitor result types for telemetry evaluation.

This module provides types for representing the results of threshold evaluations
performed by telemetry monitors. Monitors continuously evaluate measurement data
against state-dependent thresholds and produce MonitorResult records.

Classes:
    MonitorVerdict: Enumeration of possible evaluation outcomes.
    ThresholdViolation: Details of a single threshold violation.
    MonitorResult: Complete result of a monitor evaluation cycle.

Example:
    >>> result = MonitorResult(
    ...     monitor_id=MonitorId("voltage_monitor"),
    ...     verdict=MonitorVerdict.PASS,
    ...     timestamp=Timestamp.now(),
    ...     state_id=StateId("ambient")
    ... )
    >>> result.passed
    True
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from hwtest_core.types.common import ChannelId, MonitorId, StateId, Timestamp
from hwtest_core.types.threshold import Threshold


class MonitorVerdict(Enum):
    """Result of a monitor evaluation cycle.

    Attributes:
        PASS: All evaluated values were within thresholds.
        FAIL: One or more values exceeded thresholds.
        SKIP: Evaluation was skipped (e.g., during state transition).
        ERROR: Evaluation failed due to an error (e.g., missing data).
    """

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    ERROR = "error"


@dataclass(frozen=True)
class ThresholdViolation:
    """Details of a single threshold violation.

    Records which channel violated its threshold, the offending value,
    the threshold definition, and an optional descriptive message.

    Attributes:
        channel: The channel that violated its threshold.
        value: The measured value that caused the violation.
        threshold: The threshold definition that was violated.
        message: Optional descriptive message about the violation.
    """

    channel: ChannelId
    value: float
    threshold: Threshold
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary.

        Returns:
            Dictionary with violation details.
        """
        return {
            "channel": self.channel,
            "value": self.value,
            "threshold": self.threshold.to_dict(),
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ThresholdViolation:
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with violation details.

        Returns:
            A ThresholdViolation instance.
        """
        return cls(
            channel=ChannelId(data["channel"]),
            value=float(data["value"]),
            threshold=Threshold.from_dict(data["threshold"]),
            message=data.get("message", ""),
        )


@dataclass(frozen=True)
class MonitorResult:
    """Complete result of a monitor evaluation cycle.

    Captures the outcome of evaluating a set of telemetry values against
    thresholds for a specific environmental state.

    Attributes:
        monitor_id: Identifier of the monitor that produced this result.
        verdict: The evaluation outcome (PASS, FAIL, SKIP, or ERROR).
        timestamp: When this evaluation occurred.
        state_id: The environmental state during evaluation.
        violations: Tuple of threshold violations (empty if PASS).
        message: Optional descriptive message.
    """

    monitor_id: MonitorId
    verdict: MonitorVerdict
    timestamp: Timestamp
    state_id: StateId
    violations: tuple[ThresholdViolation, ...] = ()
    message: str = ""

    @property
    def passed(self) -> bool:
        """Check if the evaluation passed.

        Returns:
            True if verdict is PASS.
        """
        return self.verdict == MonitorVerdict.PASS

    @property
    def failed(self) -> bool:
        """Check if the evaluation failed.

        Returns:
            True if verdict is FAIL.
        """
        return self.verdict == MonitorVerdict.FAIL

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary.

        Returns:
            Dictionary with all result fields.
        """
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
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with result fields.

        Returns:
            A MonitorResult instance.
        """
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
        """Serialize to JSON bytes for network transmission.

        Returns:
            UTF-8 encoded JSON representation.
        """
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> MonitorResult:
        """Deserialize from JSON bytes.

        Args:
            data: UTF-8 encoded JSON representation.

        Returns:
            A MonitorResult instance.
        """
        return cls.from_dict(json.loads(data.decode("utf-8")))
