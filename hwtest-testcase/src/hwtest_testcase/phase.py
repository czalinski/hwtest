"""Test phase definitions."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Awaitable

from hwtest_core.types.common import StateId, Timestamp
from hwtest_core.types.state import EnvironmentalState

if TYPE_CHECKING:
    from hwtest_testcase.context import TestContext

logger = logging.getLogger(__name__)


class PhaseStatus(Enum):
    """Status of a test phase."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PhaseResult:
    """Result of executing a test phase."""

    phase_name: str
    status: PhaseStatus
    start_time: Timestamp
    end_time: Timestamp
    state_id: StateId
    message: str = ""
    errors: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """Return True if phase completed successfully."""
        return self.status == PhaseStatus.COMPLETED

    @property
    def duration_ns(self) -> int:
        """Return phase duration in nanoseconds."""
        return self.end_time.unix_ns - self.start_time.unix_ns

    @property
    def duration_seconds(self) -> float:
        """Return phase duration in seconds."""
        return self.duration_ns / 1_000_000_000

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "phase_name": self.phase_name,
            "status": self.status.value,
            "start_time": self.start_time.unix_ns,
            "end_time": self.end_time.unix_ns,
            "state_id": self.state_id,
            "message": self.message,
            "errors": list(self.errors),
        }


# Type alias for phase action callback
PhaseAction = Callable[["TestContext"], Awaitable[None]]


@dataclass
class TestPhase:
    """Definition of a test phase.

    A phase represents a distinct period during test execution with a
    specific environmental state. Phases can include pre-conditions,
    main execution logic, and post-conditions.

    Example:
        ambient_phase = TestPhase(
            name="ambient",
            state=ambient_state,
            duration_seconds=60.0,
            description="Initial ambient temperature stabilization",
        )
    """

    name: str
    state: EnvironmentalState
    duration_seconds: float = 0.0
    description: str = ""
    pre_action: PhaseAction | None = None
    action: PhaseAction | None = None
    post_action: PhaseAction | None = None
    skip_if: Callable[["TestContext"], bool] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def state_id(self) -> StateId:
        """Return the state ID for this phase."""
        return self.state.state_id

    async def execute(self, context: TestContext) -> PhaseResult:
        """Execute the phase.

        Args:
            context: Test execution context.

        Returns:
            PhaseResult describing the execution outcome.
        """
        start_time = Timestamp.now()

        # Check skip condition
        if self.skip_if is not None and self.skip_if(context):
            return PhaseResult(
                phase_name=self.name,
                status=PhaseStatus.SKIPPED,
                start_time=start_time,
                end_time=Timestamp.now(),
                state_id=self.state_id,
                message="Skipped by condition",
            )

        errors: list[str] = []

        try:
            # Set state in context
            context.set_state(self.state)

            # Run pre-action
            if self.pre_action is not None:
                logger.debug("Running pre-action for phase %s", self.name)
                await self.pre_action(context)

            # Run main action
            if self.action is not None:
                logger.debug("Running action for phase %s", self.name)
                await self.action(context)

            # Run post-action
            if self.post_action is not None:
                logger.debug("Running post-action for phase %s", self.name)
                await self.post_action(context)

            status = PhaseStatus.COMPLETED
            message = "Phase completed successfully"

        except Exception as e:  # pylint: disable=broad-except
            logger.error("Phase %s failed: %s", self.name, e)
            errors.append(str(e))
            status = PhaseStatus.FAILED
            message = f"Phase failed: {e}"

        return PhaseResult(
            phase_name=self.name,
            status=status,
            start_time=start_time,
            end_time=Timestamp.now(),
            state_id=self.state_id,
            message=message,
            errors=tuple(errors),
        )
