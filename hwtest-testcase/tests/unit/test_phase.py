"""Unit tests for test phases."""

import pytest

from hwtest_core.types.common import StateId
from hwtest_core.types.state import EnvironmentalState

from hwtest_testcase.context import TestContext
from hwtest_testcase.phase import PhaseResult, PhaseStatus, TestPhase


class TestPhaseResult:
    """Tests for PhaseResult."""

    def test_passed_property(self) -> None:
        """Test passed property."""
        from hwtest_core.types.common import Timestamp

        result = PhaseResult(
            phase_name="test",
            status=PhaseStatus.COMPLETED,
            start_time=Timestamp.now(),
            end_time=Timestamp.now(),
            state_id=StateId("ambient"),
        )
        assert result.passed

        failed_result = PhaseResult(
            phase_name="test",
            status=PhaseStatus.FAILED,
            start_time=Timestamp.now(),
            end_time=Timestamp.now(),
            state_id=StateId("ambient"),
        )
        assert not failed_result.passed

    def test_duration(self) -> None:
        """Test duration calculation."""
        from hwtest_core.types.common import Timestamp

        start = Timestamp(unix_ns=1000000000)
        end = Timestamp(unix_ns=2000000000)

        result = PhaseResult(
            phase_name="test",
            status=PhaseStatus.COMPLETED,
            start_time=start,
            end_time=end,
            state_id=StateId("ambient"),
        )

        assert result.duration_ns == 1000000000
        assert result.duration_seconds == 1.0

    def test_to_dict(self) -> None:
        """Test serialization."""
        from hwtest_core.types.common import Timestamp

        result = PhaseResult(
            phase_name="test",
            status=PhaseStatus.COMPLETED,
            start_time=Timestamp.now(),
            end_time=Timestamp.now(),
            state_id=StateId("ambient"),
            message="Success",
            errors=("error1",),
        )

        data = result.to_dict()
        assert data["phase_name"] == "test"
        assert data["status"] == "completed"
        assert data["message"] == "Success"
        assert data["errors"] == ["error1"]


class TestTestPhase:
    """Tests for TestPhase."""

    @pytest.fixture
    def ambient_state(self) -> EnvironmentalState:
        """Create an ambient state."""
        return EnvironmentalState(
            state_id=StateId("ambient"),
            name="ambient",
            description="Ambient temperature",
        )

    @pytest.fixture
    def context(self) -> TestContext:
        """Create a test context."""
        return TestContext(test_id="test_001")

    def test_state_id_property(self, ambient_state: EnvironmentalState) -> None:
        """Test state_id property."""
        phase = TestPhase(name="test", state=ambient_state)
        assert phase.state_id == StateId("ambient")

    async def test_execute_success(
        self, ambient_state: EnvironmentalState, context: TestContext
    ) -> None:
        """Test successful phase execution."""
        action_called = False

        async def action(ctx: TestContext) -> None:
            nonlocal action_called
            action_called = True

        phase = TestPhase(name="test", state=ambient_state, action=action)
        result = await phase.execute(context)

        assert result.status == PhaseStatus.COMPLETED
        assert result.passed
        assert action_called
        assert context.current_state == ambient_state

    async def test_execute_with_all_actions(
        self, ambient_state: EnvironmentalState, context: TestContext
    ) -> None:
        """Test execution with pre, main, and post actions."""
        call_order: list[str] = []

        async def pre_action(ctx: TestContext) -> None:
            call_order.append("pre")

        async def main_action(ctx: TestContext) -> None:
            call_order.append("main")

        async def post_action(ctx: TestContext) -> None:
            call_order.append("post")

        phase = TestPhase(
            name="test",
            state=ambient_state,
            pre_action=pre_action,
            action=main_action,
            post_action=post_action,
        )
        await phase.execute(context)

        assert call_order == ["pre", "main", "post"]

    async def test_execute_failure(
        self, ambient_state: EnvironmentalState, context: TestContext
    ) -> None:
        """Test phase execution with failure."""

        async def failing_action(ctx: TestContext) -> None:
            raise RuntimeError("Test failure")

        phase = TestPhase(name="test", state=ambient_state, action=failing_action)
        result = await phase.execute(context)

        assert result.status == PhaseStatus.FAILED
        assert not result.passed
        assert "Test failure" in result.message
        assert len(result.errors) == 1

    async def test_execute_skip(
        self, ambient_state: EnvironmentalState, context: TestContext
    ) -> None:
        """Test phase skipped by condition."""

        def skip_condition(ctx: TestContext) -> bool:
            return True

        phase = TestPhase(
            name="test",
            state=ambient_state,
            skip_if=skip_condition,
        )
        result = await phase.execute(context)

        assert result.status == PhaseStatus.SKIPPED

    async def test_execute_no_skip(
        self, ambient_state: EnvironmentalState, context: TestContext
    ) -> None:
        """Test phase not skipped when condition is false."""

        def skip_condition(ctx: TestContext) -> bool:
            return False

        phase = TestPhase(
            name="test",
            state=ambient_state,
            skip_if=skip_condition,
        )
        result = await phase.execute(context)

        assert result.status == PhaseStatus.COMPLETED

    async def test_execute_no_action(
        self, ambient_state: EnvironmentalState, context: TestContext
    ) -> None:
        """Test phase with no action completes successfully."""
        phase = TestPhase(name="test", state=ambient_state)
        result = await phase.execute(context)

        assert result.status == PhaseStatus.COMPLETED
