"""Tests for threshold types."""

import pytest

from hwtest_core.types.common import ChannelId, StateId
from hwtest_core.types.threshold import (
    BoundType,
    StateThresholds,
    Threshold,
    ThresholdBound,
)


class TestBoundType:
    """Tests for BoundType enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert BoundType.INCLUSIVE.value == "inclusive"
        assert BoundType.EXCLUSIVE.value == "exclusive"


class TestThresholdBound:
    """Tests for ThresholdBound."""

    def test_create(self) -> None:
        """Test creating a bound."""
        bound = ThresholdBound(value=10.0)
        assert bound.value == 10.0
        assert bound.bound_type == BoundType.INCLUSIVE

    def test_create_exclusive(self) -> None:
        """Test creating an exclusive bound."""
        bound = ThresholdBound(value=5.0, bound_type=BoundType.EXCLUSIVE)
        assert bound.bound_type == BoundType.EXCLUSIVE

    def test_check_low_inclusive(self) -> None:
        """Test checking lower bound (inclusive)."""
        bound = ThresholdBound(value=0.0, bound_type=BoundType.INCLUSIVE)
        assert bound.check_low(0.0) is True  # Equal, inclusive
        assert bound.check_low(1.0) is True  # Above
        assert bound.check_low(-1.0) is False  # Below

    def test_check_low_exclusive(self) -> None:
        """Test checking lower bound (exclusive)."""
        bound = ThresholdBound(value=0.0, bound_type=BoundType.EXCLUSIVE)
        assert bound.check_low(0.0) is False  # Equal, exclusive
        assert bound.check_low(0.001) is True  # Above
        assert bound.check_low(-0.001) is False  # Below

    def test_check_high_inclusive(self) -> None:
        """Test checking upper bound (inclusive)."""
        bound = ThresholdBound(value=100.0, bound_type=BoundType.INCLUSIVE)
        assert bound.check_high(100.0) is True  # Equal, inclusive
        assert bound.check_high(99.0) is True  # Below
        assert bound.check_high(101.0) is False  # Above

    def test_check_high_exclusive(self) -> None:
        """Test checking upper bound (exclusive)."""
        bound = ThresholdBound(value=100.0, bound_type=BoundType.EXCLUSIVE)
        assert bound.check_high(100.0) is False  # Equal, exclusive
        assert bound.check_high(99.999) is True  # Below
        assert bound.check_high(100.001) is False  # Above

    def test_to_dict(self) -> None:
        """Test converting to dictionary."""
        bound = ThresholdBound(value=50.0, bound_type=BoundType.EXCLUSIVE)
        d = bound.to_dict()
        assert d["value"] == 50.0
        assert d["bound_type"] == "exclusive"

    def test_from_dict(self) -> None:
        """Test creating from dictionary."""
        d = {"value": 25.0, "bound_type": "inclusive"}
        bound = ThresholdBound.from_dict(d)
        assert bound.value == 25.0
        assert bound.bound_type == BoundType.INCLUSIVE


class TestThreshold:
    """Tests for Threshold."""

    def test_create_range(self) -> None:
        """Test creating a threshold with both bounds."""
        threshold = Threshold(
            channel=ChannelId("voltage"),
            low=ThresholdBound(value=3.0),
            high=ThresholdBound(value=3.6),
        )
        assert threshold.channel == "voltage"
        assert threshold.low is not None
        assert threshold.high is not None

    def test_create_low_only(self) -> None:
        """Test creating with only lower bound."""
        threshold = Threshold(
            channel=ChannelId("temp"),
            low=ThresholdBound(value=-40.0),
            high=None,
        )
        assert threshold.low is not None
        assert threshold.high is None

    def test_create_high_only(self) -> None:
        """Test creating with only upper bound."""
        threshold = Threshold(
            channel=ChannelId("current"),
            low=None,
            high=ThresholdBound(value=2.0),
        )
        assert threshold.low is None
        assert threshold.high is not None

    def test_check_within_range(self) -> None:
        """Test checking values within range."""
        threshold = Threshold(
            channel=ChannelId("v"),
            low=ThresholdBound(value=3.0),
            high=ThresholdBound(value=3.6),
        )
        assert threshold.check(3.0) is True  # At low bound
        assert threshold.check(3.3) is True  # In middle
        assert threshold.check(3.6) is True  # At high bound

    def test_check_out_of_range(self) -> None:
        """Test checking values out of range."""
        threshold = Threshold(
            channel=ChannelId("v"),
            low=ThresholdBound(value=3.0),
            high=ThresholdBound(value=3.6),
        )
        assert threshold.check(2.9) is False  # Below low
        assert threshold.check(3.7) is False  # Above high

    def test_check_low_only(self) -> None:
        """Test checking with only lower bound."""
        threshold = Threshold(
            channel=ChannelId("t"),
            low=ThresholdBound(value=0.0),
        )
        assert threshold.check(-1.0) is False
        assert threshold.check(0.0) is True
        assert threshold.check(1000.0) is True  # No upper limit

    def test_check_high_only(self) -> None:
        """Test checking with only upper bound."""
        threshold = Threshold(
            channel=ChannelId("i"),
            high=ThresholdBound(value=5.0),
        )
        assert threshold.check(-1000.0) is True  # No lower limit
        assert threshold.check(5.0) is True
        assert threshold.check(5.1) is False

    def test_check_no_bounds(self) -> None:
        """Test checking with no bounds (always passes)."""
        threshold = Threshold(channel=ChannelId("any"))
        assert threshold.check(-999.0) is True
        assert threshold.check(0.0) is True
        assert threshold.check(999.0) is True

    def test_check_exclusive_bounds(self) -> None:
        """Test checking with exclusive bounds."""
        threshold = Threshold(
            channel=ChannelId("x"),
            low=ThresholdBound(value=0.0, bound_type=BoundType.EXCLUSIVE),
            high=ThresholdBound(value=10.0, bound_type=BoundType.EXCLUSIVE),
        )
        assert threshold.check(0.0) is False  # Low bound exclusive
        assert threshold.check(0.001) is True
        assert threshold.check(9.999) is True
        assert threshold.check(10.0) is False  # High bound exclusive

    def test_to_dict(self) -> None:
        """Test converting to dictionary."""
        threshold = Threshold(
            channel=ChannelId("ch"),
            low=ThresholdBound(value=1.0),
            high=ThresholdBound(value=2.0),
        )
        d = threshold.to_dict()
        assert d["channel"] == "ch"
        assert d["low"]["value"] == 1.0
        assert d["high"]["value"] == 2.0

    def test_from_dict(self) -> None:
        """Test creating from dictionary."""
        d = {
            "channel": "voltage",
            "low": {"value": 3.0, "bound_type": "inclusive"},
            "high": {"value": 3.6, "bound_type": "inclusive"},
        }
        threshold = Threshold.from_dict(d)
        assert threshold.channel == "voltage"
        assert threshold.low is not None
        assert threshold.low.value == 3.0
        assert threshold.high is not None
        assert threshold.high.value == 3.6


class TestStateThresholds:
    """Tests for StateThresholds."""

    @pytest.fixture
    def sample_thresholds(self) -> StateThresholds:
        """Create sample state thresholds."""
        return StateThresholds(
            state_id=StateId("room_temp"),
            thresholds={
                ChannelId("v3v3"): Threshold(
                    ChannelId("v3v3"),
                    ThresholdBound(3.0),
                    ThresholdBound(3.6),
                ),
                ChannelId("v5v"): Threshold(
                    ChannelId("v5v"),
                    ThresholdBound(4.75),
                    ThresholdBound(5.25),
                ),
            },
        )

    def test_create(self, sample_thresholds: StateThresholds) -> None:
        """Test creating state thresholds."""
        assert sample_thresholds.state_id == "room_temp"
        assert len(sample_thresholds.thresholds) == 2

    def test_get_threshold(self, sample_thresholds: StateThresholds) -> None:
        """Test getting threshold by channel."""
        threshold = sample_thresholds.get_threshold(ChannelId("v3v3"))
        assert threshold is not None
        assert threshold.channel == "v3v3"

        missing = sample_thresholds.get_threshold(ChannelId("nonexistent"))
        assert missing is None

    def test_check_value(self, sample_thresholds: StateThresholds) -> None:
        """Test checking value against threshold."""
        # In range
        assert sample_thresholds.check_value(ChannelId("v3v3"), 3.3) is True
        # Out of range
        assert sample_thresholds.check_value(ChannelId("v3v3"), 4.0) is False
        # No threshold defined
        assert sample_thresholds.check_value(ChannelId("unknown"), 999.0) is None

    def test_to_dict(self, sample_thresholds: StateThresholds) -> None:
        """Test converting to dictionary."""
        d = sample_thresholds.to_dict()
        assert d["state_id"] == "room_temp"
        assert "v3v3" in d["thresholds"]
        assert "v5v" in d["thresholds"]

    def test_from_dict(self) -> None:
        """Test creating from dictionary."""
        d = {
            "state_id": "hot",
            "thresholds": {
                "temp": {
                    "channel": "temp",
                    "low": {"value": 80.0, "bound_type": "inclusive"},
                    "high": {"value": 90.0, "bound_type": "inclusive"},
                }
            },
        }
        st = StateThresholds.from_dict(d)
        assert st.state_id == "hot"
        assert st.get_threshold(ChannelId("temp")) is not None

    def test_roundtrip(self, sample_thresholds: StateThresholds) -> None:
        """Test bytes roundtrip."""
        data = sample_thresholds.to_bytes()
        restored = StateThresholds.from_bytes(data)

        assert restored.state_id == sample_thresholds.state_id
        assert len(restored.thresholds) == len(sample_thresholds.thresholds)

        for channel_id in sample_thresholds.thresholds:
            orig = sample_thresholds.get_threshold(channel_id)
            rest = restored.get_threshold(channel_id)
            assert rest is not None
            assert orig is not None
            assert rest.channel == orig.channel
