"""Tests for state types."""

import pytest

from hwtest_core.types.common import StateId, Timestamp
from hwtest_core.types.state import EnvironmentalState, StateTransition


class TestEnvironmentalState:
    """Tests for EnvironmentalState."""

    def test_create(self) -> None:
        """Test creating a state."""
        state = EnvironmentalState(
            state_id=StateId("room_temp"),
            name="Room Temperature",
            description="Normal room temperature conditions",
        )
        assert state.state_id == "room_temp"
        assert state.name == "Room Temperature"
        assert state.description == "Normal room temperature conditions"
        assert state.is_transition is False
        assert state.metadata == {}

    def test_create_transition_state(self) -> None:
        """Test creating a transition state."""
        state = EnvironmentalState(
            state_id=StateId("transition_hot"),
            name="Transition to Hot",
            description="Ramping temperature up",
            is_transition=True,
        )
        assert state.is_transition is True

    def test_create_with_metadata(self) -> None:
        """Test creating with metadata."""
        state = EnvironmentalState(
            state_id=StateId("hot"),
            name="Hot",
            description="High temperature stress",
            metadata={"target_temp": 85.0, "chamber": "A"},
        )
        assert state.metadata["target_temp"] == 85.0
        assert state.metadata["chamber"] == "A"

    def test_to_dict(self) -> None:
        """Test converting to dictionary."""
        state = EnvironmentalState(
            state_id=StateId("cold"),
            name="Cold",
            description="Low temperature",
            is_transition=False,
            metadata={"temp": -40},
        )
        d = state.to_dict()
        assert d["state_id"] == "cold"
        assert d["name"] == "Cold"
        assert d["description"] == "Low temperature"
        assert d["is_transition"] is False
        assert d["metadata"]["temp"] == -40

    def test_from_dict(self) -> None:
        """Test creating from dictionary."""
        d = {
            "state_id": "vibration",
            "name": "Vibration Test",
            "description": "10G vibration",
            "is_transition": False,
            "metadata": {"g_force": 10},
        }
        state = EnvironmentalState.from_dict(d)
        assert state.state_id == "vibration"
        assert state.name == "Vibration Test"
        assert state.metadata["g_force"] == 10

    def test_to_bytes(self) -> None:
        """Test serializing to bytes."""
        state = EnvironmentalState(
            state_id=StateId("test"),
            name="Test",
            description="Test state",
        )
        data = state.to_bytes()
        assert isinstance(data, bytes)
        assert b"test" in data

    def test_roundtrip(self) -> None:
        """Test bytes roundtrip."""
        original = EnvironmentalState(
            state_id=StateId("stress"),
            name="Thermal Stress",
            description="Combined thermal stress",
            is_transition=True,
            metadata={"temp": 125, "duration": 3600},
        )
        data = original.to_bytes()
        restored = EnvironmentalState.from_bytes(data)

        assert restored.state_id == original.state_id
        assert restored.name == original.name
        assert restored.description == original.description
        assert restored.is_transition == original.is_transition
        assert restored.metadata == dict(original.metadata)

    def test_immutable(self) -> None:
        """Test that EnvironmentalState is immutable."""
        state = EnvironmentalState(
            state_id=StateId("test"),
            name="Test",
            description="Test",
        )
        with pytest.raises(AttributeError):
            state.name = "Changed"  # type: ignore[misc]


class TestStateTransition:
    """Tests for StateTransition."""

    def test_create(self) -> None:
        """Test creating a transition."""
        ts = Timestamp(unix_ns=1000000000)
        transition = StateTransition(
            from_state=StateId("room"),
            to_state=StateId("hot"),
            timestamp=ts,
            reason="Starting thermal stress",
        )
        assert transition.from_state == "room"
        assert transition.to_state == "hot"
        assert transition.timestamp.unix_ns == 1000000000
        assert transition.reason == "Starting thermal stress"

    def test_create_initial(self) -> None:
        """Test creating initial transition (no from_state)."""
        ts = Timestamp.now()
        transition = StateTransition(
            from_state=None,
            to_state=StateId("initial"),
            timestamp=ts,
        )
        assert transition.from_state is None
        assert transition.to_state == "initial"
        assert transition.reason == ""

    def test_to_dict(self) -> None:
        """Test converting to dictionary."""
        ts = Timestamp(unix_ns=2000000000, source="ptp")
        transition = StateTransition(
            from_state=StateId("a"),
            to_state=StateId("b"),
            timestamp=ts,
            reason="Test",
        )
        d = transition.to_dict()
        assert d["from_state"] == "a"
        assert d["to_state"] == "b"
        assert d["timestamp"] == 2000000000
        assert d["timestamp_source"] == "ptp"
        assert d["reason"] == "Test"

    def test_to_dict_no_from_state(self) -> None:
        """Test to_dict with None from_state."""
        ts = Timestamp.now()
        transition = StateTransition(
            from_state=None,
            to_state=StateId("start"),
            timestamp=ts,
        )
        d = transition.to_dict()
        assert d["from_state"] is None

    def test_from_dict(self) -> None:
        """Test creating from dictionary."""
        d = {
            "from_state": "state1",
            "to_state": "state2",
            "timestamp": 3000000000,
            "timestamp_source": "local",
            "reason": "Scheduled",
        }
        transition = StateTransition.from_dict(d)
        assert transition.from_state == "state1"
        assert transition.to_state == "state2"
        assert transition.timestamp.unix_ns == 3000000000
        assert transition.reason == "Scheduled"

    def test_from_dict_null_from_state(self) -> None:
        """Test from_dict with null from_state."""
        d = {
            "from_state": None,
            "to_state": "initial",
            "timestamp": 1000000000,
        }
        transition = StateTransition.from_dict(d)
        assert transition.from_state is None

    def test_roundtrip(self) -> None:
        """Test bytes roundtrip."""
        ts = Timestamp(unix_ns=5000000000, source="ntp")
        original = StateTransition(
            from_state=StateId("cold"),
            to_state=StateId("room"),
            timestamp=ts,
            reason="Completed cold soak",
        )
        data = original.to_bytes()
        restored = StateTransition.from_bytes(data)

        assert restored.from_state == original.from_state
        assert restored.to_state == original.to_state
        assert restored.timestamp.unix_ns == original.timestamp.unix_ns
        assert restored.timestamp.source == original.timestamp.source
        assert restored.reason == original.reason
