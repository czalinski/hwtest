"""Environmental state types for test condition management.

This module provides types for representing and tracking environmental states
during HASS/HALT testing. States define discrete test conditions (e.g., ambient,
thermal stress, vibration) with associated metadata.

Classes:
    EnvironmentalState: A discrete environmental condition.
    StateTransition: Records a change from one state to another.

The state system supports "transition states" which indicate that the system
is moving between stable states. During transitions, threshold evaluation
is typically suspended to avoid false failures.

Example:
    >>> ambient = EnvironmentalState(
    ...     state_id=StateId("ambient"),
    ...     name="Ambient",
    ...     description="Room temperature, no stress"
    ... )
    >>> transition = StateTransition(
    ...     from_state=StateId("ambient"),
    ...     to_state=StateId("hot"),
    ...     timestamp=Timestamp.now(),
    ...     reason="Begin thermal stress"
    ... )
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from hwtest_core.types.common import StateId, Timestamp


@dataclass(frozen=True)
class EnvironmentalState:
    """A discrete environmental condition during testing.

    Represents a stable environmental state (e.g., "ambient", "thermal_stress")
    or a transition state indicating movement between stable states.

    Attributes:
        state_id: Unique identifier for this state.
        name: Human-readable state name.
        description: Detailed description of the state.
        is_transition: True if this is a transition state (evaluation suspended).
        metadata: Additional state-specific data (e.g., target temperature).
    """

    state_id: StateId
    name: str
    description: str
    is_transition: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary.

        Returns:
            Dictionary with all state fields.
        """
        return {
            "state_id": self.state_id,
            "name": self.name,
            "description": self.description,
            "is_transition": self.is_transition,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnvironmentalState:
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with state fields.

        Returns:
            An EnvironmentalState instance.
        """
        return cls(
            state_id=StateId(data["state_id"]),
            name=data["name"],
            description=data["description"],
            is_transition=data.get("is_transition", False),
            metadata=data.get("metadata", {}),
        )

    def to_bytes(self) -> bytes:
        """Serialize to JSON bytes for network transmission.

        Returns:
            UTF-8 encoded JSON representation.
        """
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> EnvironmentalState:
        """Deserialize from JSON bytes.

        Args:
            data: UTF-8 encoded JSON representation.

        Returns:
            An EnvironmentalState instance.
        """
        return cls.from_dict(json.loads(data.decode("utf-8")))


@dataclass(frozen=True)
class StateTransition:
    """Records a change from one environmental state to another.

    Captures the source state, destination state, timestamp, and reason
    for a state transition. Used for logging and state change notification.

    Attributes:
        from_state: The previous state ID, or None for initial state.
        to_state: The new state ID.
        timestamp: When the transition occurred.
        reason: Optional explanation for the transition.
    """

    from_state: StateId | None
    to_state: StateId
    timestamp: Timestamp
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dictionary.

        Returns:
            Dictionary with transition details.
        """
        return {
            "from_state": self.from_state,
            "to_state": self.to_state,
            "timestamp": self.timestamp.unix_ns,
            "timestamp_source": self.timestamp.source,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateTransition:
        """Deserialize from a dictionary.

        Args:
            data: Dictionary with transition details.

        Returns:
            A StateTransition instance.
        """
        return cls(
            from_state=StateId(data["from_state"]) if data.get("from_state") else None,
            to_state=StateId(data["to_state"]),
            timestamp=Timestamp(
                unix_ns=data["timestamp"],
                source=data.get("timestamp_source", "local"),
            ),
            reason=data.get("reason", ""),
        )

    def to_bytes(self) -> bytes:
        """Serialize to JSON bytes for network transmission.

        Returns:
            UTF-8 encoded JSON representation.
        """
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> StateTransition:
        """Deserialize from JSON bytes.

        Args:
            data: UTF-8 encoded JSON representation.

        Returns:
            A StateTransition instance.
        """
        return cls.from_dict(json.loads(data.decode("utf-8")))
