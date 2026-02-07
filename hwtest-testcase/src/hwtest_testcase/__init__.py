"""Test case framework for hwtest HASS/HALT automation.

This package provides the infrastructure for creating and executing
hardware test cases with environmental state management and monitoring.

Example usage:

    from hwtest_testcase import TestCase, TestPhase, TestContext

    class VoltageStressTest(TestCase):
        async def setup(self) -> None:
            # Configure instruments
            pass

        async def execute(self) -> None:
            # Run through test phases
            await self.run_phase(self.ambient_phase)
            await self.run_phase(self.stress_phase)

        async def teardown(self) -> None:
            # Cleanup
            pass
"""

from hwtest_testcase.context import TestContext
from hwtest_testcase.definition import (
    CalibrationDef,
    ChannelDef,
    ParametersDef,
    StateDef,
    TestCaseInfo,
    TestDefinition,
    ThresholdDef,
    find_definition_file,
    load_definition,
)
from hwtest_testcase.phase import TestPhase, PhaseResult, PhaseStatus
from hwtest_testcase.runner import TestRunner
from hwtest_testcase.testcase import TestCase, TestCaseResult, TestStatus

__all__ = [
    # Definition loading
    "CalibrationDef",
    "ChannelDef",
    "ParametersDef",
    "StateDef",
    "TestCaseInfo",
    "TestDefinition",
    "ThresholdDef",
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
