"""Unit tests for test cases."""

import pytest

from hwtest_core.types.common import StateId
from hwtest_core.types.state import EnvironmentalState

from hwtest_testcase.context import TestContext
from hwtest_testcase.phase import PhaseStatus, TestPhase
from hwtest_testcase.testcase import TestCase, TestCaseResult, TestStatus


class SimpleTestCase(TestCase):
    """A simple test case for testing."""

    name = "Simple Test"
    description = "A test case for unit testing"

    async def setup(self) -> None:
        """Setup the test."""
        self.context.set_resource("setup_called", True)

    async def execute(self) -> None:
        """Execute the test."""
        for phase in self.phases:
            await self.run_phase(phase)

    async def teardown(self) -> None:
        """Teardown the test."""
        self.context.set_resource("teardown_called", True)


class FailingSetupTestCase(TestCase):
    """A test case that fails during setup."""

    name = "Failing Setup Test"

    async def setup(self) -> None:
        """Setup that fails."""
        raise RuntimeError("Setup failed")

    async def execute(self) -> None:
        """Execute the test."""
        pass

    async def teardown(self) -> None:
        """Teardown the test."""
        self.context.set_resource("teardown_called", True)


class TestTestCase:
    """Tests for TestCase."""

    @pytest.fixture
    def ambient_state(self) -> EnvironmentalState:
        """Create an ambient state."""
        return EnvironmentalState(
            state_id=StateId("ambient"),
            name="ambient",
            description="Ambient temperature",
        )

    @pytest.fixture
    def stress_state(self) -> EnvironmentalState:
        """Create a stress state."""
        return EnvironmentalState(
            state_id=StateId("stress"),
            name="stress",
            description="Stress condition",
        )

    def test_initial_state(self) -> None:
        """Test initial test case state."""
        test = SimpleTestCase("test_001")

        assert test.test_id == "test_001"
        assert test.name == "Simple Test"
        assert test.status == TestStatus.PENDING
        assert len(test.phases) == 0
        assert len(test.phase_results) == 0

    def test_add_phase(self, ambient_state: EnvironmentalState) -> None:
        """Test adding phases."""
        test = SimpleTestCase("test_001")
        phase = TestPhase(name="phase1", state=ambient_state)

        test.add_phase(phase)

        assert len(test.phases) == 1
        assert test.phases[0] == phase

    async def test_run_success(self, ambient_state: EnvironmentalState) -> None:
        """Test successful test run."""
        test = SimpleTestCase("test_001")
        test.add_phase(TestPhase(name="phase1", state=ambient_state))

        result = await test.run()

        assert result.status == TestStatus.PASSED
        assert result.passed
        assert not result.failed
        assert result.phases_passed == 1
        assert result.phases_failed == 0
        assert test.context.has_resource("setup_called")
        assert test.context.has_resource("teardown_called")

    async def test_run_multiple_phases(
        self, ambient_state: EnvironmentalState, stress_state: EnvironmentalState
    ) -> None:
        """Test running multiple phases."""
        test = SimpleTestCase("test_001")
        test.add_phase(TestPhase(name="ambient", state=ambient_state))
        test.add_phase(TestPhase(name="stress", state=stress_state))

        result = await test.run()

        assert result.status == TestStatus.PASSED
        assert len(result.phase_results) == 2
        assert result.phases_passed == 2

    async def test_run_with_phase_failure(self, ambient_state: EnvironmentalState) -> None:
        """Test run with a failing phase."""

        async def failing_action(ctx: TestContext) -> None:
            raise RuntimeError("Phase failed")

        test = SimpleTestCase("test_001")
        test.add_phase(TestPhase(name="failing", state=ambient_state, action=failing_action))

        result = await test.run()

        assert result.status == TestStatus.FAILED
        assert result.failed
        assert result.phases_failed == 1

    async def test_setup_failure(self) -> None:
        """Test that setup failure is handled."""
        test = FailingSetupTestCase("test_001")

        result = await test.run()

        assert result.status == TestStatus.ERROR
        assert "Setup failed" in result.message
        # Teardown should still be called
        assert test.context.has_resource("teardown_called")

    async def test_request_abort(self, ambient_state: EnvironmentalState) -> None:
        """Test abort request."""
        test = SimpleTestCase("test_001")
        test.add_phase(TestPhase(name="phase1", state=ambient_state))

        # Request abort before running
        test.request_abort()

        result = await test.run()

        # Should be aborted during phase execution
        assert result.status == TestStatus.ABORTED

    def test_context_access(self) -> None:
        """Test accessing test context."""
        test = SimpleTestCase("test_001", custom_key="custom_value")

        assert test.context.test_id == "test_001"
        assert test.context.metadata.get("custom_key") == "custom_value"


class TestTestCaseResult:
    """Tests for TestCaseResult."""

    def test_duration(self) -> None:
        """Test duration calculation."""
        from hwtest_core.types.common import Timestamp

        start = Timestamp(unix_ns=1000000000)
        end = Timestamp(unix_ns=3000000000)

        result = TestCaseResult(
            test_id="test_001",
            test_name="Test",
            status=TestStatus.PASSED,
            start_time=start,
            end_time=end,
            phase_results=(),
        )

        assert result.duration_ns == 2000000000
        assert result.duration_seconds == 2.0

    def test_passed_failed_properties(self) -> None:
        """Test passed and failed properties."""
        from hwtest_core.types.common import Timestamp

        now = Timestamp.now()

        passed_result = TestCaseResult(
            test_id="test_001",
            test_name="Test",
            status=TestStatus.PASSED,
            start_time=now,
            end_time=now,
            phase_results=(),
        )
        assert passed_result.passed
        assert not passed_result.failed

        failed_result = TestCaseResult(
            test_id="test_001",
            test_name="Test",
            status=TestStatus.FAILED,
            start_time=now,
            end_time=now,
            phase_results=(),
        )
        assert not failed_result.passed
        assert failed_result.failed

        error_result = TestCaseResult(
            test_id="test_001",
            test_name="Test",
            status=TestStatus.ERROR,
            start_time=now,
            end_time=now,
            phase_results=(),
        )
        assert error_result.failed

    def test_to_dict(self) -> None:
        """Test serialization."""
        from hwtest_core.types.common import Timestamp

        now = Timestamp.now()

        result = TestCaseResult(
            test_id="test_001",
            test_name="Test",
            status=TestStatus.PASSED,
            start_time=now,
            end_time=now,
            phase_results=(),
            message="Success",
        )

        data = result.to_dict()
        assert data["test_id"] == "test_001"
        assert data["status"] == "passed"
        assert data["message"] == "Success"
