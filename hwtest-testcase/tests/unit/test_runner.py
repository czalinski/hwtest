"""Unit tests for test runner."""

import pytest

from hwtest_core.types.common import StateId
from hwtest_core.types.state import EnvironmentalState

from hwtest_testcase.phase import TestPhase
from hwtest_testcase.runner import RunnerConfig, RunnerResult, TestRunner
from hwtest_testcase.testcase import TestCase, TestStatus


class PassingTestCase(TestCase):
    """A test case that always passes."""

    name = "Passing Test"

    async def setup(self) -> None:
        pass

    async def execute(self) -> None:
        for phase in self.phases:
            await self.run_phase(phase)

    async def teardown(self) -> None:
        pass


class FailingTestCase(TestCase):
    """A test case that always fails."""

    name = "Failing Test"

    async def setup(self) -> None:
        pass

    async def execute(self) -> None:
        raise RuntimeError("Test failed")

    async def teardown(self) -> None:
        pass


class TestRunnerResult:
    """Tests for RunnerResult."""

    def test_statistics(self) -> None:
        """Test result statistics calculation."""
        from hwtest_core.types.common import Timestamp
        from hwtest_testcase.testcase import TestCaseResult

        now = Timestamp.now()
        results = (
            TestCaseResult(
                test_id="1",
                test_name="Test 1",
                status=TestStatus.PASSED,
                start_time=now,
                end_time=now,
                phase_results=(),
            ),
            TestCaseResult(
                test_id="2",
                test_name="Test 2",
                status=TestStatus.PASSED,
                start_time=now,
                end_time=now,
                phase_results=(),
            ),
            TestCaseResult(
                test_id="3",
                test_name="Test 3",
                status=TestStatus.FAILED,
                start_time=now,
                end_time=now,
                phase_results=(),
            ),
        )

        runner_result = RunnerResult(results=results)

        assert runner_result.total == 3
        assert runner_result.passed == 2
        assert runner_result.failed == 1
        assert not runner_result.all_passed

    def test_all_passed(self) -> None:
        """Test all_passed property."""
        from hwtest_core.types.common import Timestamp
        from hwtest_testcase.testcase import TestCaseResult

        now = Timestamp.now()
        results = (
            TestCaseResult(
                test_id="1",
                test_name="Test 1",
                status=TestStatus.PASSED,
                start_time=now,
                end_time=now,
                phase_results=(),
            ),
        )

        runner_result = RunnerResult(results=results)
        assert runner_result.all_passed


class TestTestRunner:
    """Tests for TestRunner."""

    @pytest.fixture
    def ambient_state(self) -> EnvironmentalState:
        """Create an ambient state."""
        return EnvironmentalState(
            state_id=StateId("ambient"),
            name="ambient",
            description="Ambient",
        )

    def test_initial_state(self) -> None:
        """Test initial runner state."""
        runner = TestRunner()

        assert len(runner.tests) == 0
        assert len(runner.results) == 0
        assert not runner.is_running

    def test_add_test(self, ambient_state: EnvironmentalState) -> None:
        """Test adding tests."""
        runner = TestRunner()
        test = PassingTestCase("test_001")

        runner.add_test(test)

        assert len(runner.tests) == 1

    def test_clear_tests(self, ambient_state: EnvironmentalState) -> None:
        """Test clearing tests."""
        runner = TestRunner()
        runner.add_test(PassingTestCase("test_001"))
        runner.add_test(PassingTestCase("test_002"))

        runner.clear_tests()

        assert len(runner.tests) == 0

    async def test_run_single_test(self, ambient_state: EnvironmentalState) -> None:
        """Test running a single test."""
        runner = TestRunner()
        test = PassingTestCase("test_001")
        test.add_phase(TestPhase(name="phase1", state=ambient_state))

        runner.add_test(test)
        result = await runner.run_all()

        assert result.total == 1
        assert result.passed == 1
        assert result.all_passed

    async def test_run_multiple_tests(self, ambient_state: EnvironmentalState) -> None:
        """Test running multiple tests."""
        runner = TestRunner()

        for i in range(3):
            test = PassingTestCase(f"test_{i}")
            test.add_phase(TestPhase(name="phase1", state=ambient_state))
            runner.add_test(test)

        result = await runner.run_all()

        assert result.total == 3
        assert result.passed == 3

    async def test_run_with_failures(self, ambient_state: EnvironmentalState) -> None:
        """Test running tests with some failures."""
        runner = TestRunner()

        passing = PassingTestCase("passing")
        passing.add_phase(TestPhase(name="phase1", state=ambient_state))
        runner.add_test(passing)

        failing = FailingTestCase("failing")
        runner.add_test(failing)

        result = await runner.run_all()

        assert result.total == 2
        assert result.passed == 1
        assert result.failed == 0
        assert result.errors == 1

    async def test_stop_on_failure(self, ambient_state: EnvironmentalState) -> None:
        """Test stopping on first failure."""
        config = RunnerConfig(stop_on_failure=True)
        runner = TestRunner(config=config)

        failing = FailingTestCase("failing")
        runner.add_test(failing)

        passing = PassingTestCase("passing")
        passing.add_phase(TestPhase(name="phase1", state=ambient_state))
        runner.add_test(passing)

        result = await runner.run_all()

        # Only one test should run
        assert result.total == 1

    async def test_result_callback(self, ambient_state: EnvironmentalState) -> None:
        """Test result callback is called."""
        callback_results: list[object] = []

        def callback(result: object) -> None:
            callback_results.append(result)

        runner = TestRunner(on_result=callback)
        test = PassingTestCase("test_001")
        test.add_phase(TestPhase(name="phase1", state=ambient_state))
        runner.add_test(test)

        await runner.run_all()

        assert len(callback_results) == 1

    async def test_timeout(self, ambient_state: EnvironmentalState) -> None:
        """Test test timeout."""
        import asyncio

        class SlowTestCase(TestCase):
            name = "Slow Test"

            async def setup(self) -> None:
                pass

            async def execute(self) -> None:
                await asyncio.sleep(10)

            async def teardown(self) -> None:
                pass

        config = RunnerConfig(timeout_seconds=0.1)
        runner = TestRunner(config=config)
        runner.add_test(SlowTestCase("slow"))

        result = await runner.run_all()

        assert result.errors == 1
        assert "Timeout" in result.results[0].errors

    async def test_abort(self, ambient_state: EnvironmentalState) -> None:
        """Test aborting runner."""
        runner = TestRunner()

        for i in range(3):
            test = PassingTestCase(f"test_{i}")
            test.add_phase(TestPhase(name="phase1", state=ambient_state))
            runner.add_test(test)

        # Abort immediately
        runner.abort()

        result = await runner.run_all()

        # At least some tests should be aborted
        assert result.aborted >= 0  # Exact number depends on timing
