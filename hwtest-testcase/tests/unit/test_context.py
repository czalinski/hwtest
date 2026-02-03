"""Unit tests for test context."""

import pytest

from hwtest_core.types.common import StateId
from hwtest_core.types.state import EnvironmentalState

from hwtest_testcase.context import TestContext


class TestTestContext:
    """Tests for TestContext."""

    def test_initial_state(self) -> None:
        """Test initial context state."""
        context = TestContext(test_id="test_001")

        assert context.test_id == "test_001"
        assert context.start_time is None
        assert context.end_time is None
        assert context.current_state is None
        assert context.state_id is None

    def test_start_stop(self) -> None:
        """Test starting and stopping context."""
        context = TestContext(test_id="test_001")

        context.start()
        assert context.start_time is not None
        assert context.end_time is None

        context.stop()
        assert context.end_time is not None
        assert context.duration_ns is not None
        assert context.duration_seconds is not None

    def test_duration_not_started(self) -> None:
        """Test duration returns None when not started."""
        context = TestContext(test_id="test_001")
        assert context.duration_ns is None
        assert context.duration_seconds is None

    def test_set_state(self) -> None:
        """Test setting environmental state."""
        context = TestContext(test_id="test_001")

        state = EnvironmentalState(
            state_id=StateId("ambient"),
            name="ambient",
            description="Ambient temperature",
        )
        context.set_state(state)

        assert context.current_state == state
        assert context.state_id == StateId("ambient")

    def test_artifacts(self) -> None:
        """Test artifact management."""
        context = TestContext(test_id="test_001")

        context.add_artifact("log_file", "/path/to/log.csv")
        context.add_artifact("report", "/path/to/report.html")

        assert context.get_artifact("log_file") == "/path/to/log.csv"
        assert context.get_artifact("report") == "/path/to/report.html"
        assert context.get_artifact("nonexistent") is None

    def test_resources(self) -> None:
        """Test resource management."""
        context = TestContext(test_id="test_001")

        # Store a resource
        context.set_resource("psu", {"voltage": 3.3})

        assert context.has_resource("psu")
        assert not context.has_resource("other")

        resource = context.get_resource("psu")
        assert resource == {"voltage": 3.3}

    def test_resource_not_found(self) -> None:
        """Test accessing nonexistent resource raises KeyError."""
        context = TestContext(test_id="test_001")

        with pytest.raises(KeyError):
            context.get_resource("nonexistent")

    def test_metadata(self) -> None:
        """Test metadata storage."""
        context = TestContext(
            test_id="test_001",
            description="Test description",
            metadata={"key": "value"},
        )

        assert context.description == "Test description"
        assert context.metadata == {"key": "value"}

    def test_to_dict(self) -> None:
        """Test serialization to dictionary."""
        context = TestContext(test_id="test_001")
        context.start()
        context.add_artifact("log", "/path/to/log")
        context.stop()

        data = context.to_dict()

        assert data["test_id"] == "test_001"
        assert data["start_time"] is not None
        assert data["end_time"] is not None
        assert data["artifacts"] == {"log": "/path/to/log"}
