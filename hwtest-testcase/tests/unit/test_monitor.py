"""Tests for the bounds monitor (Rust-backed)."""

import math

import pytest

from hwtest_core.types.monitor import MonitorResult, MonitorVerdict
from hwtest_core.types.state import EnvironmentalState
from hwtest_core.types.threshold import BoundType
from hwtest_testcase import Monitor, TestDefinition

DEFINITION = TestDefinition.from_yaml_str("""
test_case: {id: demo}
monitor_states: {low: {name: Low}, high: {name: High}, idle: {}}
monitors:
  echo:
    kwargs.0: {channel: v}
    configuration:
      default: {v: {special: any}}
      low: {v: {within_range: [1.0, 0.3]}, i: {less_than: 2.0}}
      high: {v: {within_baseline: [4.0, 0.5, 0.1]}}
  custom:
    module: lab.monitors
    class: Fancy
""")


@pytest.fixture
def monitor() -> Monitor:
    return Monitor(DEFINITION.get_monitor("echo"))


def state(state_id: str, transition: bool = False) -> EnvironmentalState:
    return (
        DEFINITION.get_state(state_id).to_environmental_state()
        if not transition
        else (EnvironmentalState(state_id, "Transition", "", is_transition=True))
    )


class TestEvaluate:
    def test_pass(self, monitor: Monitor) -> None:
        result = monitor.evaluate({"v": 1.1, "i": 0.5, "unrelated": 9.0}, state("low"))
        assert result.verdict == MonitorVerdict.PASS
        assert result.message == "All 2 field(s) within bounds for Low"
        assert result.monitor_id == "echo" and result.state_id == "low"

    def test_fail_produces_serializable_violations(self, monitor: Monitor) -> None:
        # Regression: the Python implementation raised TypeError building violations.
        result = monitor.evaluate({"v": 2.0, "i": 3.0}, state("low"))
        assert result.verdict == MonitorVerdict.FAIL
        assert [v.channel for v in result.violations] == ["v", "i"]
        v, i = result.violations
        assert v.message == "v=2.0000 outside [0.7000, 1.3000]"
        assert (v.threshold.low.value, v.threshold.high.value) == pytest.approx((0.7, 1.3))
        assert i.threshold.low is None
        assert i.threshold.high.bound_type == BoundType.EXCLUSIVE
        assert MonitorResult.from_bytes(result.to_bytes()) == result

    def test_default_any_skips(self, monitor: Monitor) -> None:
        result = monitor.evaluate({"v": 99.0}, state("idle"))
        assert result.verdict == MonitorVerdict.SKIP
        assert result.message == "No fields to check (skipped 1)"

    def test_transition_skips(self, monitor: Monitor) -> None:
        result = monitor.evaluate({"v": 99.0}, state("t", transition=True))
        assert result.verdict == MonitorVerdict.SKIP

    def test_nan_fails(self, monitor: Monitor) -> None:
        assert monitor.evaluate_single("v", math.nan, state("low")).failed

    def test_baseline_locks_and_resets(self, monitor: Monitor) -> None:
        assert monitor.evaluate_single("v", 4.2, state("high")).passed  # locks at 4.2
        assert monitor.evaluate_single("v", 4.25, state("high")).passed
        assert monitor.evaluate_single("v", 4.4, state("high")).failed  # outside 4.2 ± 0.1
        monitor.reset()
        assert monitor.evaluate_single("v", 4.4, state("high")).passed  # re-locks at 4.4


class TestMonitorInfo:
    def test_properties(self, monitor: Monitor) -> None:
        assert (monitor.slot_number, monitor.is_uut_monitor, monitor.is_system_monitor) == (
            0,
            True,
            False,
        )
        assert Monitor(DEFINITION.get_monitor("echo"), monitor_id="slot0").monitor_id == "slot0"

    def test_bounds_info(self, monitor: Monitor) -> None:
        info = monitor.get_bounds_info(state("low"))
        assert info["v"] == {
            "bound_type": "within_range",
            "value": [1.0, 0.3],
            "interval": pytest.approx((0.7, 1.3)),
        }
        assert info["i"]["interval"] == (-math.inf, 2.0)

    def test_python_monitor_is_not_a_bounds_monitor(self) -> None:
        with pytest.raises(TypeError, match="lab.monitors.Fancy"):
            Monitor(DEFINITION.get_monitor("custom"))
