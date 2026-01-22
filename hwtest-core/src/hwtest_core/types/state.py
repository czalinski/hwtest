"""Environmental state types."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from hwtest_core.types.common import StateId, Timestamp


@dataclass(frozen=True)
class EnvironmentalState:
    """Represents a discrete environmental condition."""

    state_id: StateId
    name: str
    description: str
    is_transition: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "state_id": self.state_id,
            "name": self.name,
            "description": self.description,
            "is_transition": self.is_transition,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnvironmentalState:
        """Create from dictionary."""
        return cls(
            state_id=StateId(data["state_id"]),
            name=data["name"],
            description=data["description"],
            is_transition=data.get("is_transition", False),
            metadata=data.get("metadata", {}),
        )

    def to_bytes(self) -> bytes:
        """Serialize to JSON bytes."""
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> EnvironmentalState:
        """Deserialize from JSON bytes."""
        return cls.from_dict(json.loads(data.decode("utf-8")))


@dataclass(frozen=True)
class StateTransition:
    """Records a change in environmental state."""

    from_state: StateId | None
    to_state: StateId
    timestamp: Timestamp
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "from_state": self.from_state,
            "to_state": self.to_state,
            "timestamp": self.timestamp.unix_ns,
            "timestamp_source": self.timestamp.source,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateTransition:
        """Create from dictionary."""
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
        """Serialize to JSON bytes."""
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> StateTransition:
        """Deserialize from JSON bytes."""
        return cls.from_dict(json.loads(data.decode("utf-8")))
