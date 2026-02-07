"""Test case framework for hwtest HASS/HALT automation.

This package provides the infrastructure for creating and executing
hardware test cases with environmental state management and monitoring.

Example usage:

    from hwtest_testcase import TestDefinition, load_definition

    # Load test definition from YAML
    definition = load_definition("voltage_echo_monitor")

    # Access parameters
    settling_time = definition.case_parameters["settling_time_seconds"]

    # Get monitor configuration
    monitor = definition.monitors["echo_voltage_monitor"]
    bounds = monitor.get_bounds("minimum", "echo_voltage")

    # Check a value against bounds
    if bounds.check(measured_voltage):
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
