"""Tests for monitor types."""

import pytest

from hwtest_core.types.common import ChannelId, MonitorId, StateId, Timestamp
from hwtest_core.types.monitor import MonitorResult, MonitorVerdict, ThresholdViolation
from hwtest_core.types.threshold import Threshold, ThresholdBound


class TestMonitorVerdict:
    """Tests for MonitorVerdict enum."""

    def test_values(self) -> None:
        """Test enum values."""
        assert MonitorVerdict.PASS.value == "pass"
        assert MonitorVerdict.FAIL.value == "fail"
        assert MonitorVerdict.SKIP.value == "skip"
        assert MonitorVerdict.ERROR.value == "error"

    def test_from_string(self) -> None:
        """Test creating from string value."""
        assert MonitorVerdict("pass") == MonitorVerdict.PASS
        assert MonitorVerdict("fail") == MonitorVerdict.FAIL


class TestThresholdViolation:
    """Tests for ThresholdViolation."""

    def test_create(self) -> None:
        """Test creating a violation."""
        threshold = Threshold(
            channel=ChannelId("voltage"),
            low=ThresholdBound(3.0),
            high=ThresholdBound(3.6),
        )
        violation = ThresholdViolation(
            channel=ChannelId("voltage"),
            value=4.0,
            threshold=threshold,
            message="Voltage too high",
        )
        assert violation.channel == "voltage"
        assert violation.value == 4.0
        assert violation.threshold == threshold
        assert violation.message == "Voltage too high"

    def test_create_no_message(self) -> None:
        """Test creating without message."""
        threshold = Threshold(channel=ChannelId("ch"))
        violation = ThresholdViolation(
            channel=ChannelId("ch"),
            value=0.0,
            threshold=threshold,
        )
        assert violation.message == ""

    def test_to_dict(self) -> None:
        """Test converting to dictionary."""
        threshold = Threshold(
            channel=ChannelId("temp"),
            high=ThresholdBound(100.0),
        )
        violation = ThresholdViolation(
            channel=ChannelId("temp"),
            value=105.0,
            threshold=threshold,
            message="Overtemp",
        )
        d = violation.to_dict()
        assert d["channel"] == "temp"
        assert d["value"] == 105.0
        assert d["threshold"]["channel"] == "temp"
        assert d["message"] == "Overtemp"

    def test_from_dict(self) -> None:
        """Test creating from dictionary."""
        d = {
            "channel": "current",
            "value": 10.0,
            "threshold": {
                "channel": "current",
                "low": None,
                "high": {"value": 5.0, "bound_type": "inclusive"},
            },
            "message": "Overcurrent",
        }
        violation = ThresholdViolation.from_dict(d)
        assert violation.channel == "current"
        assert violation.value == 10.0
        assert violation.message == "Overcurrent"


class TestMonitorResult:
    """Tests for MonitorResult."""

    def test_create_pass(self) -> None:
        """Test creating a passing result."""
        ts = Timestamp(unix_ns=1000000000)
        result = MonitorResult(
            monitor_id=MonitorId("mon1"),
            verdict=MonitorVerdict.PASS,
            timestamp=ts,
            state_id=StateId("room"),
        )
        assert result.monitor_id == "mon1"
        assert result.verdict == MonitorVerdict.PASS
        assert result.passed is True
        assert result.failed is False
        assert result.violations == ()
        assert result.message == ""

    def test_create_fail(self) -> None:
        """Test creating a failing result."""
        ts = Timestamp.now()
        threshold = Threshold(
            channel=ChannelId("v"),
            high=ThresholdBound(5.0),
        )
        violation = ThresholdViolation(
            channel=ChannelId("v"),
            value=6.0,
            threshold=threshold,
        )
        result = MonitorResult(
            monitor_id=MonitorId("mon1"),
            verdict=MonitorVerdict.FAIL,
            timestamp=ts,
            state_id=StateId("test"),
            violations=(violation,),
            message="Threshold exceeded",
        )
        assert result.verdict == MonitorVerdict.FAIL
        assert result.passed is False
        assert result.failed is True
        assert len(result.violations) == 1
        assert result.message == "Threshold exceeded"

    def test_create_skip(self) -> None:
        """Test creating a skipped result (transition state)."""
        ts = Timestamp.now()
        result = MonitorResult(
            monitor_id=MonitorId("mon1"),
            verdict=MonitorVerdict.SKIP,
            timestamp=ts,
            state_id=StateId("transition"),
            message="Skipped during transition",
        )
        assert result.verdict == MonitorVerdict.SKIP
        assert result.passed is False
        assert result.failed is False

    def test_create_error(self) -> None:
        """Test creating an error result."""
        ts = Timestamp.now()
        result = MonitorResult(
            monitor_id=MonitorId("mon1"),
            verdict=MonitorVerdict.ERROR,
            timestamp=ts,
            state_id=StateId("unknown"),
            message="Failed to read sensor",
        )
        assert result.verdict == MonitorVerdict.ERROR
        assert result.passed is False
        assert result.failed is False

    def test_to_dict(self) -> None:
        """Test converting to dictionary."""
        ts = Timestamp(unix_ns=2000000000, source="ptp")
        result = MonitorResult(
            monitor_id=MonitorId("voltage_monitor"),
            verdict=MonitorVerdict.PASS,
            timestamp=ts,
            state_id=StateId("hot"),
        )
        d = result.to_dict()
        assert d["monitor_id"] == "voltage_monitor"
        assert d["verdict"] == "pass"
        assert d["timestamp"] == 2000000000
        assert d["timestamp_source"] == "ptp"
        assert d["state_id"] == "hot"
        assert d["violations"] == []
        assert d["message"] == ""

    def test_to_dict_with_violations(self) -> None:
        """Test to_dict with violations."""
        ts = Timestamp.now()
        threshold = Threshold(channel=ChannelId("x"), high=ThresholdBound(1.0))
        violation = ThresholdViolation(ChannelId("x"), 2.0, threshold)
        result = MonitorResult(
            monitor_id=MonitorId("m"),
            verdict=MonitorVerdict.FAIL,
            timestamp=ts,
            state_id=StateId("s"),
            violations=(violation,),
        )
        d = result.to_dict()
        assert len(d["violations"]) == 1
        assert d["violations"][0]["channel"] == "x"

    def test_from_dict(self) -> None:
        """Test creating from dictionary."""
        d = {
            "monitor_id": "test_mon",
            "verdict": "fail",
            "timestamp": 3000000000,
            "timestamp_source": "local",
            "state_id": "cold",
            "violations": [
                {
                    "channel": "temp",
                    "value": -50.0,
                    "threshold": {
                        "channel": "temp",
                        "low": {"value": -40.0, "bound_type": "inclusive"},
                        "high": None,
                    },
                    "message": "Too cold",
                }
            ],
            "message": "Temperature below limit",
        }
        result = MonitorResult.from_dict(d)
        assert result.monitor_id == "test_mon"
        assert result.verdict == MonitorVerdict.FAIL
        assert result.timestamp.unix_ns == 3000000000
        assert result.state_id == "cold"
        assert len(result.violations) == 1
        assert result.violations[0].value == -50.0
        assert result.message == "Temperature below limit"

    def test_roundtrip(self) -> None:
        """Test bytes roundtrip."""
        ts = Timestamp(unix_ns=5000000000, source="ntp")
        threshold = Threshold(
            channel=ChannelId("current"),
            high=ThresholdBound(2.0),
        )
        violation = ThresholdViolation(
            channel=ChannelId("current"),
            value=2.5,
            threshold=threshold,
            message="Overcurrent detected",
        )
        original = MonitorResult(
            monitor_id=MonitorId("current_monitor"),
            verdict=MonitorVerdict.FAIL,
            timestamp=ts,
            state_id=StateId("stress_test"),
            violations=(violation,),
            message="Multiple overcurrent events",
        )

        data = original.to_bytes()
        restored = MonitorResult.from_bytes(data)

        assert restored.monitor_id == original.monitor_id
        assert restored.verdict == original.verdict
        assert restored.timestamp.unix_ns == original.timestamp.unix_ns
        assert restored.timestamp.source == original.timestamp.source
        assert restored.state_id == original.state_id
        assert len(restored.violations) == 1
        assert restored.violations[0].value == pytest.approx(2.5)
        assert restored.message == original.message

    def test_immutable(self) -> None:
        """Test that MonitorResult is immutable."""
        ts = Timestamp.now()
        result = MonitorResult(
            monitor_id=MonitorId("m"),
            verdict=MonitorVerdict.PASS,
            timestamp=ts,
            state_id=StateId("s"),
        )
        with pytest.raises(AttributeError):
            result.verdict = MonitorVerdict.FAIL  # type: ignore[misc]
