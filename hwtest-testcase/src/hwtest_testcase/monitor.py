"""Generic monitor for evaluating field values against state-dependent bounds.

This module provides a Monitor class that works similarly to loggers - it can
watch multiple topics/fields and evaluate their values against bounds defined
in the MonitorDef from YAML configuration.

Example usage:

    from hwtest_testcase import Monitor, MonitorDef, load_definition
    from hwtest_core.types.state import EnvironmentalState

    definition = load_definition("voltage_echo_monitor")
    monitor_def = definition.monitors["echo_voltage_monitor"]

    monitor = Monitor(monitor_def)

    # Evaluate field values against current state bounds
    result = monitor.evaluate(
        values={"echo_voltage": 2.45},
        state=current_state,
    )

    if result.passed:
        print("All fields within bounds")
    elif result.failed:
        for violation in result.violations:
            print(f"Violation: {violation.channel} = {violation.value}")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hwtest_core.types.common import ChannelId, MonitorId, Timestamp
from hwtest_core.types.monitor import MonitorResult, MonitorVerdict, ThresholdViolation
from hwtest_core.types.state import EnvironmentalState
from hwtest_core.types.threshold import Threshold

from hwtest_testcase.definition import BoundSpec, MonitorDef


def _bound_spec_to_threshold(bound: BoundSpec) -> Threshold:
    """Convert a BoundSpec to a Threshold for violation reporting.

    Args:
        bound: The bound specification.

    Returns:
        A Threshold object representing the bound.
    """
    interval = bound.to_interval()
    if interval:
        low, high = interval
        return Threshold(low=low, high=high)
    # For non-interval bounds, create a placeholder threshold
    return Threshold(low=float("-inf"), high=float("inf"))


@dataclass
class Monitor:
    """Generic monitor that evaluates field values against state-dependent bounds.

    Uses MonitorDef from YAML configuration to determine which fields to check
    and what bounds apply in each state. Similar to how loggers work, this class
    is generic and can handle any fields defined in the configuration.

    Attributes:
        monitor_def: The monitor definition from YAML with bounds configuration.
        monitor_id: Unique identifier for this monitor (defaults to monitor_def.name).
    """

    monitor_def: MonitorDef
    monitor_id: MonitorId | None = None

    def __post_init__(self) -> None:
        """Set default monitor_id from monitor_def.name if not provided."""
        if self.monitor_id is None:
            object.__setattr__(self, "monitor_id", MonitorId(self.monitor_def.name))

    def evaluate(
        self,
        values: dict[str, float],
        state: EnvironmentalState,
    ) -> MonitorResult:
        """Evaluate field values against the current state's bounds.

        Checks each field in the provided values dict against the bounds
        defined in the MonitorDef for the current state. Fields not defined
        in the configuration are ignored. Fields with 'special: any' bounds
        are skipped.

        Args:
            values: Dictionary mapping field names to measured values.
            state: The current environmental state.

        Returns:
            MonitorResult with PASS if all fields within bounds,
            FAIL if any field violates bounds, SKIP if in transition state,
            or ERROR if configuration issues detected.
        """
        timestamp = Timestamp.now()
        monitor_id = self.monitor_id or MonitorId(self.monitor_def.name)

        # Skip evaluation during state transitions
        if state.is_transition:
            return MonitorResult(
                monitor_id=monitor_id,
                verdict=MonitorVerdict.SKIP,
                timestamp=timestamp,
                state_id=state.state_id,
                message="Skipping evaluation during state transition",
            )

        violations: list[ThresholdViolation] = []
        fields_checked = 0
        fields_skipped = 0

        # Check each field that has bounds defined
        for field_name in self.monitor_def.get_all_fields():
            if field_name not in values:
                continue

            measured = values[field_name]
            bounds = self.monitor_def.get_bounds(state.state_id, field_name)

            if bounds is None:
                # No bounds defined - skip this field
                fields_skipped += 1
                continue

            if bounds.is_any:
                # special: any - skip checking this field
                fields_skipped += 1
                continue

            fields_checked += 1

            if not bounds.check(measured):
                interval = bounds.to_interval()
                if interval:
                    low, high = interval
                    msg = f"{field_name}={measured:.4f} outside [{low:.4f}, {high:.4f}]"
                else:
                    msg = f"{field_name}={measured:.4f} failed {bounds.bound_type} check"

                violations.append(
                    ThresholdViolation(
                        channel=ChannelId(field_name),
                        value=measured,
                        threshold=_bound_spec_to_threshold(bounds),
                        message=msg,
                    )
                )

        # Build result
        if violations:
            violation_msgs = [v.message for v in violations]
            return MonitorResult(
                monitor_id=monitor_id,
                verdict=MonitorVerdict.FAIL,
                timestamp=timestamp,
                state_id=state.state_id,
                violations=tuple(violations),
                message=f"Failed: {'; '.join(violation_msgs)}",
            )

        if fields_checked == 0:
            return MonitorResult(
                monitor_id=monitor_id,
                verdict=MonitorVerdict.SKIP,
                timestamp=timestamp,
                state_id=state.state_id,
                message=f"No fields to check (skipped {fields_skipped})",
            )

        return MonitorResult(
            monitor_id=monitor_id,
            verdict=MonitorVerdict.PASS,
            timestamp=timestamp,
            state_id=state.state_id,
            message=f"All {fields_checked} field(s) within bounds for {state.name}",
        )

    def evaluate_single(
        self,
        field_name: str,
        value: float,
        state: EnvironmentalState,
    ) -> MonitorResult:
        """Evaluate a single field value against bounds.

        Convenience method for checking a single field.

        Args:
            field_name: The field to check.
            value: The measured value.
            state: The current environmental state.

        Returns:
            MonitorResult for this single field evaluation.
        """
        return self.evaluate({field_name: value}, state)

    def get_bounds_info(self, state: EnvironmentalState) -> dict[str, dict[str, Any]]:
        """Get bounds information for all fields in a given state.

        Useful for logging or display purposes.

        Args:
            state: The environmental state to get bounds for.

        Returns:
            Dictionary mapping field names to bound info dicts with
            'bound_type', 'value', and 'interval' (if applicable).
        """
        result: dict[str, dict[str, Any]] = {}

        for field_name in self.monitor_def.get_all_fields():
            bounds = self.monitor_def.get_bounds(state.state_id, field_name)
            if bounds is None:
                continue

            info: dict[str, Any] = {
                "bound_type": bounds.bound_type,
                "value": bounds.value,
            }
            interval = bounds.to_interval()
            if interval:
                info["interval"] = interval

            result[field_name] = info

        return result
