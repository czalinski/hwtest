"""Test case runner."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from hwtest_testcase.testcase import TestCase, TestCaseResult, TestStatus

logger = logging.getLogger(__name__)


# Type for result callback
ResultCallback = Callable[[TestCaseResult], None]


@dataclass
class RunnerConfig:
    """Configuration for the test runner."""

    max_concurrent: int = 1
    stop_on_failure: bool = False
    timeout_seconds: float | None = None


@dataclass
class RunnerResult:
    """Result of running multiple test cases."""

    results: tuple[TestCaseResult, ...]
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    aborted: int = 0

    def __post_init__(self) -> None:
        """Calculate statistics."""
        self.total = len(self.results)
        self.passed = sum(1 for r in self.results if r.status == TestStatus.PASSED)
        self.failed = sum(1 for r in self.results if r.status == TestStatus.FAILED)
        self.errors = sum(1 for r in self.results if r.status == TestStatus.ERROR)
        self.aborted = sum(1 for r in self.results if r.status == TestStatus.ABORTED)

    @property
    def all_passed(self) -> bool:
        """Return True if all tests passed."""
        return self.passed == self.total

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "results": [r.to_dict() for r in self.results],
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "errors": self.errors,
            "aborted": self.aborted,
            "all_passed": self.all_passed,
        }


class TestRunner:
    """Runs test cases with configurable behavior.

    The runner supports:
    - Sequential or concurrent test execution
    - Configurable failure handling (stop or continue)
    - Timeout support
    - Result callbacks

    Example:
        runner = TestRunner(config=RunnerConfig(stop_on_failure=True))

        # Add tests
        runner.add_test(VoltageStressTest("test_001"))
        runner.add_test(ThermalCycleTest("test_002"))

        # Run all tests
        result = await runner.run_all()

        print(f"Passed: {result.passed}/{result.total}")
    """

    def __init__(
        self,
        config: RunnerConfig | None = None,
        on_result: ResultCallback | None = None,
    ) -> None:
        """Initialize the runner.

        Args:
            config: Runner configuration.
            on_result: Optional callback called after each test completes.
        """
        self._config = config or RunnerConfig()
        self._on_result = on_result
        self._tests: list[TestCase] = []
        self._results: list[TestCaseResult] = []
        self._running = False
        self._abort_all = False

    @property
    def tests(self) -> list[TestCase]:
        """Return the list of tests."""
        return self._tests

    @property
    def results(self) -> list[TestCaseResult]:
        """Return the list of results."""
        return self._results

    @property
    def is_running(self) -> bool:
        """Return True if runner is executing tests."""
        return self._running

    def add_test(self, test: TestCase) -> None:
        """Add a test case to run.

        Args:
            test: The test case to add.
        """
        self._tests.append(test)

    def clear_tests(self) -> None:
        """Remove all tests."""
        self._tests.clear()

    def abort(self) -> None:
        """Abort all running and pending tests."""
        self._abort_all = True
        for test in self._tests:
            test.request_abort()
        logger.warning("Abort all requested")

    async def run_test(self, test: TestCase) -> TestCaseResult:
        """Run a single test case.

        Args:
            test: The test case to run.

        Returns:
            TestCaseResult describing the outcome.
        """
        logger.info("Running test: %s (%s)", test.name, test.test_id)

        try:
            if self._config.timeout_seconds is not None:
                result = await asyncio.wait_for(test.run(), timeout=self._config.timeout_seconds)
            else:
                result = await test.run()
        except asyncio.TimeoutError:
            logger.error("Test %s timed out", test.test_id)
            from hwtest_core.types.common import Timestamp

            result = TestCaseResult(
                test_id=test.test_id,
                test_name=test.name,
                status=TestStatus.ERROR,
                start_time=test.context.start_time or Timestamp.now(),
                end_time=Timestamp.now(),
                phase_results=tuple(test.phase_results),
                message="Test timed out",
                errors=("Timeout",),
            )

        self._results.append(result)

        if self._on_result is not None:
            self._on_result(result)

        return result

    async def run_all(self) -> RunnerResult:
        """Run all test cases.

        Returns:
            RunnerResult with aggregated statistics.
        """
        self._running = True
        self._abort_all = False
        self._results.clear()

        try:
            if self._config.max_concurrent == 1:
                await self._run_sequential()
            else:
                await self._run_concurrent()
        finally:
            self._running = False

        return RunnerResult(results=tuple(self._results))

    async def _run_sequential(self) -> None:
        """Run tests sequentially."""
        for test in self._tests:
            if self._abort_all:
                logger.info("Skipping remaining tests due to abort")
                break

            result = await self.run_test(test)

            if self._config.stop_on_failure and result.failed:
                logger.info("Stopping due to failure (stop_on_failure=True)")
                break

    async def _run_concurrent(self) -> None:
        """Run tests concurrently with limited concurrency."""
        semaphore = asyncio.Semaphore(self._config.max_concurrent)

        async def run_with_semaphore(test: TestCase) -> TestCaseResult:
            async with semaphore:
                if self._abort_all:
                    from hwtest_core.types.common import Timestamp

                    return TestCaseResult(
                        test_id=test.test_id,
                        test_name=test.name,
                        status=TestStatus.ABORTED,
                        start_time=Timestamp.now(),
                        end_time=Timestamp.now(),
                        phase_results=(),
                        message="Aborted before execution",
                    )
                return await self.run_test(test)

        tasks = [run_with_semaphore(test) for test in self._tests]
        await asyncio.gather(*tasks, return_exceptions=True)
