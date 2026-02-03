"""Test execution context."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from hwtest_core.types.common import StateId, Timestamp
from hwtest_core.types.state import EnvironmentalState

logger = logging.getLogger(__name__)


@dataclass
class TestContext:
    """Context shared across a test execution.

    The context provides:
    - Access to the current environmental state
    - Storage for test artifacts and results
    - Timing information
    - Shared resources (instruments, connections, etc.)

    Example:
        context = TestContext(test_id="voltage_stress_001")
        context.set_state(ambient_state)
        context.add_artifact("log_file", "/path/to/log.csv")
    """

    test_id: str
    description: str = ""
    start_time: Timestamp | None = None
    end_time: Timestamp | None = None
    current_state: EnvironmentalState | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    _resources: dict[str, Any] = field(default_factory=dict, repr=False)

    def start(self) -> None:
        """Mark test as started."""
        self.start_time = Timestamp.now()
        logger.info("Test %s started at %s", self.test_id, self.start_time)

    def stop(self) -> None:
        """Mark test as stopped."""
        self.end_time = Timestamp.now()
        logger.info("Test %s stopped at %s", self.test_id, self.end_time)

    @property
    def duration_ns(self) -> int | None:
        """Return test duration in nanoseconds, or None if not finished."""
        if self.start_time is None or self.end_time is None:
            return None
        return self.end_time.unix_ns - self.start_time.unix_ns

    @property
    def duration_seconds(self) -> float | None:
        """Return test duration in seconds, or None if not finished."""
        duration = self.duration_ns
        if duration is None:
            return None
        return duration / 1_000_000_000

    def set_state(self, state: EnvironmentalState) -> None:
        """Set the current environmental state.

        Args:
            state: The new environmental state.
        """
        self.current_state = state
        logger.debug("Context state changed to %s", state.state_id)

    @property
    def state_id(self) -> StateId | None:
        """Return the current state ID, or None if no state set."""
        if self.current_state is None:
            return None
        return self.current_state.state_id

    def add_artifact(self, name: str, path: str) -> None:
        """Add a test artifact.

        Args:
            name: Artifact name/identifier.
            path: Path to the artifact file.
        """
        self.artifacts[name] = path
        logger.debug("Added artifact: %s -> %s", name, path)

    def get_artifact(self, name: str) -> str | None:
        """Get an artifact path by name.

        Args:
            name: Artifact name.

        Returns:
            Path to the artifact, or None if not found.
        """
        return self.artifacts.get(name)

    def set_resource(self, name: str, resource: Any) -> None:
        """Store a shared resource.

        Args:
            name: Resource name.
            resource: The resource object.
        """
        self._resources[name] = resource

    def get_resource(self, name: str) -> Any:
        """Get a shared resource by name.

        Args:
            name: Resource name.

        Returns:
            The resource object.

        Raises:
            KeyError: If resource not found.
        """
        return self._resources[name]

    def has_resource(self, name: str) -> bool:
        """Check if a resource exists.

        Args:
            name: Resource name.

        Returns:
            True if resource exists.
        """
        return name in self._resources

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "test_id": self.test_id,
            "description": self.description,
            "start_time": self.start_time.unix_ns if self.start_time else None,
            "end_time": self.end_time.unix_ns if self.end_time else None,
            "current_state": self.current_state.to_dict() if self.current_state else None,
            "metadata": self.metadata,
            "artifacts": self.artifacts,
        }
