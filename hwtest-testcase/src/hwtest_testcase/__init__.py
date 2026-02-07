"""Test case framework for hwtest HASS/HALT automation.

This package provides the infrastructure for creating and executing
hardware test cases with environmental state management and monitoring.

Example usage:

    from hwtest_testcase import Monitor, load_definition

    # Load test definition from YAML
    definition = load_definition("voltage_echo_monitor")

    # Create monitor from definition
    monitor_def = definition.monitors["echo_voltage_monitor"]
    monitor = Monitor(monitor_def)

    # Evaluate field values against state-dependent bounds
    result = monitor.evaluate(
        values={"echo_voltage": measured_voltage},
        state=current_state,
    )

    if result.passed:
        print("PASS")
"""

from hwtest_testcase.context import TestContext
from hwtest_testcase.definition import (
    BoundSpec,
    MonitorDef,
    MonitorState,
    TestCaseInfo,
    TestDefinition,
    find_definition_file,
    load_definition,
)
from hwtest_testcase.monitor import Monitor
from hwtest_testcase.phase import TestPhase, PhaseResult, PhaseStatus
from hwtest_testcase.runner import TestRunner
from hwtest_testcase.testcase import TestCase, TestCaseResult, TestStatus

__all__ = [
    # Definition loading
    "BoundSpec",
    "MonitorDef",
    "MonitorState",
    "TestCaseInfo",
    "TestDefinition",
    "find_definition_file",
    "load_definition",
    # Monitoring
    "Monitor",
    # Test execution
    "PhaseResult",
    "PhaseStatus",
    "TestCase",
    "TestCaseResult",
    "TestContext",
    "TestPhase",
    "TestRunner",
    "TestStatus",
]
