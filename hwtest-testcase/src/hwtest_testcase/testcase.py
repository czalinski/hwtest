"""Test case base class."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from hwtest_core.types.common import Timestamp

from hwtest_testcase.context import TestContext
from hwtest_testcase.phase import PhaseResult, PhaseStatus, TestPhase

logger = logging.getLogger(__name__)


class TestStatus(Enum):
    """Overall test case status."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    ABORTED = "aborted"


@dataclass(frozen=True)
class TestCaseResult:
    """Result of executing a test case."""

    test_id: str
    test_name: str
    status: TestStatus
    start_time: Timestamp
    end_time: Timestamp
    phase_results: tuple[PhaseResult, ...]
    message: str = ""
    errors: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        """Return True if test passed."""
        return self.status == TestStatus.PASSED

    @property
    def failed(self) -> bool:
        """Return True if test failed."""
        return self.status in (TestStatus.FAILED, TestStatus.ERROR)

    @property
    def duration_ns(self) -> int:
        """Return test duration in nanoseconds."""
        return self.end_time.unix_ns - self.start_time.unix_ns

    @property
    def duration_seconds(self) -> float:
        """Return test duration in seconds."""
        return self.duration_ns / 1_000_000_000

    @property
    def phases_passed(self) -> int:
        """Return number of phases that passed."""
        return sum(1 for p in self.phase_results if p.passed)

    @property
    def phases_failed(self) -> int:
        """Return number of phases that failed."""
        return sum(1 for p in self.phase_results if p.status == PhaseStatus.FAILED)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "test_id": self.test_id,
            "test_name": self.test_name,
            "status": self.status.value,
            "start_time": self.start_time.unix_ns,
            "end_time": self.end_time.unix_ns,
            "phase_results": [p.to_dict() for p in self.phase_results],
            "message": self.message,
            "errors": list(self.errors),
            "duration_seconds": self.duration_seconds,
            "phases_passed": self.phases_passed,
            "phases_failed": self.phases_failed,
        }


class TestCase(ABC):
    """Base class for test cases.

    Subclasses implement specific test logic by overriding:
    - setup(): Initialize instruments and resources
    - execute(): Run the test phases
    - teardown(): Clean up resources

    Example:
        class VoltageStressTest(TestCase):
            name = "Voltage Stress Test"

            async def setup(self) -> None:
                psu = await self.get_psu()
                await psu.set_voltage(3.3)

            async def execute(self) -> None:
                await self.run_phase(self.ambient_phase)
                await self.run_phase(self.stress_phase)

            async def teardown(self) -> None:
                psu = await self.get_psu()
                await psu.set_output(False)
    """

    name: str = "Unnamed Test"
    description: str = ""

    def __init__(self, test_id: str, **metadata: Any) -> None:
        """Initialize the test case.

        Args:
            test_id: Unique identifier for this test execution.
            **metadata: Additional metadata for the test.
        """
        self._test_id = test_id
        self._context = TestContext(
            test_id=test_id,
            description=self.description,
            metadata=dict(metadata),
        )
        self._phases: list[TestPhase] = []
        self._phase_results: list[PhaseResult] = []
        self._status = TestStatus.PENDING
        self._abort_requested = False

    @property
    def test_id(self) -> str:
        """Return the test ID."""
        return self._test_id

    @property
    def context(self) -> TestContext:
        """Return the test context."""
        return self._context

    @property
    def status(self) -> TestStatus:
        """Return the current test status."""
        return self._status

    @property
    def phases(self) -> list[TestPhase]:
        """Return the list of phases."""
        return self._phases

    @property
    def phase_results(self) -> list[PhaseResult]:
        """Return results of executed phases."""
        return self._phase_results

    def add_phase(self, phase: TestPhase) -> None:
        """Add a phase to the test.

        Args:
            phase: The phase to add.
        """
        self._phases.append(phase)

    def request_abort(self) -> None:
        """Request test abortion.

        The test will stop at the next safe point.
        """
        self._abort_requested = True
        logger.warning("Abort requested for test %s", self._test_id)

    @abstractmethod
    async def setup(self) -> None:
        """Set up the test environment.

        Override to initialize instruments, connections, and other
        resources needed for the test.
        """

    @abstractmethod
    async def execute(self) -> None:
        """Execute the test.

        Override to implement the test logic, typically by running
        through a series of phases.
        """

    @abstractmethod
    async def teardown(self) -> None:
        """Clean up after the test.

        Override to release resources, close connections, and
        perform any necessary cleanup.
        """

    async def run_phase(self, phase: TestPhase) -> PhaseResult:
        """Execute a single phase.

        Args:
            phase: The phase to execute.

        Returns:
            PhaseResult describing the outcome.

        Raises:
            RuntimeError: If abort has been requested.
        """
        if self._abort_requested:
            raise RuntimeError("Test aborted")

        logger.info("Starting phase: %s", phase.name)
        result = await phase.execute(self._context)
        self._phase_results.append(result)

        if result.passed:
            logger.info("Phase %s completed successfully", phase.name)
        else:
            logger.error("Phase %s failed: %s", phase.name, result.message)

        return result

    async def run(self) -> TestCaseResult:
        """Run the complete test case.

        Returns:
            TestCaseResult describing the overall outcome.
        """
        start_time = Timestamp.now()
        self._status = TestStatus.RUNNING
        self._context.start()
        errors: list[str] = []

        try:
            # Setup
            logger.info("Setting up test %s", self._test_id)
            await self.setup()

            # Execute
            logger.info("Executing test %s", self._test_id)
            await self.execute()

            # Determine status based on phase results
            if any(p.status == PhaseStatus.FAILED for p in self._phase_results):
                self._status = TestStatus.FAILED
                message = "One or more phases failed"
            else:
                self._status = TestStatus.PASSED
                message = "Test completed successfully"

        except Exception as e:  # pylint: disable=broad-except
            logger.error("Test %s error: %s", self._test_id, e)
            if self._abort_requested:
                self._status = TestStatus.ABORTED
                message = "Test aborted"
            else:
                self._status = TestStatus.ERROR
                message = f"Test error: {e}"
            errors.append(str(e))

        finally:
            # Always run teardown
            try:
                logger.info("Tearing down test %s", self._test_id)
                await self.teardown()
            except Exception as e:  # pylint: disable=broad-except
                logger.error("Teardown error: %s", e)
                errors.append(f"Teardown error: {e}")

            self._context.stop()

        return TestCaseResult(
            test_id=self._test_id,
            test_name=self.name,
            status=self._status,
            start_time=start_time,
            end_time=Timestamp.now(),
            phase_results=tuple(self._phase_results),
            message=message,
            errors=tuple(errors),
        )
