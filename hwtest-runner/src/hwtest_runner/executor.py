"""Test execution engine for hwtest-runner.

The executor runs test cases in background asyncio tasks, providing
status updates and cancellation support. Only one test runs at a time.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator

import httpx

from hwtest_core.types.common import DataType, SourceId, StateId, Timestamp
from hwtest_core.types.monitor import MonitorResult, MonitorVerdict
from hwtest_core.types.state import EnvironmentalState
from hwtest_core.types.streaming import StreamData, StreamField, StreamSchema
from hwtest_rack import Rack
from hwtest_rack.config import RackConfig
from hwtest_rack.instance import RackInstanceConfig, load_instance_config
from hwtest_testcase import Monitor, MonitorState, TestDefinition, load_definition

from hwtest_runner.config import StationConfig, TestCaseEntry
from hwtest_runner.models import RunState, RunStatus

logger = logging.getLogger(__name__)


# Telemetry schema for voltage echo measurements
TELEMETRY_SCHEMA = StreamSchema(
    source_id=SourceId("voltage_echo_test"),
    fields=(
        StreamField("target_voltage", DataType.F64, "V"),
        StreamField("rack_dac_voltage", DataType.F64, "V"),
        StreamField("uut_adc_voltage", DataType.F64, "V"),
        StreamField("uut_dac_voltage", DataType.F64, "V"),
        StreamField("rack_adc_voltage", DataType.F64, "V"),
    ),
)


@dataclass
class VoltageReadings:
    """Container for all voltage readings in an echo cycle."""

    target_voltage: float
    rack_dac_voltage: float
    uut_adc_voltage: float
    uut_dac_voltage: float
    rack_adc_voltage: float
    state_name: str

    def as_tuple(self) -> tuple[float, ...]:
        """Return readings as a tuple for StreamData."""
        return (
            self.target_voltage,
            self.rack_dac_voltage,
            self.uut_adc_voltage,
            self.uut_dac_voltage,
            self.rack_adc_voltage,
        )


@dataclass
class RunStatistics:
    """Statistics for test execution."""

    cycles_completed: int = 0
    states_tested: int = 0
    passes: int = 0
    failures: int = 0
    skips: int = 0
    errors: int = 0

    def record(self, result: MonitorResult) -> None:
        """Record a monitor result."""
        self.states_tested += 1
        if result.verdict == MonitorVerdict.PASS:
            self.passes += 1
        elif result.verdict == MonitorVerdict.FAIL:
            self.failures += 1
        elif result.verdict == MonitorVerdict.SKIP:
            self.skips += 1
        elif result.verdict == MonitorVerdict.ERROR:
            self.errors += 1

    def to_dict(self) -> dict[str, int]:
        """Return stats as a dictionary."""
        return {
            "cycles_completed": self.cycles_completed,
            "states_tested": self.states_tested,
            "passes": self.passes,
            "failures": self.failures,
            "skips": self.skips,
            "errors": self.errors,
        }

    def summary(self) -> str:
        """Return a summary string."""
        return (
            f"Cycles: {self.cycles_completed}, "
            f"States: {self.states_tested}, "
            f"Pass: {self.passes}, "
            f"Fail: {self.failures}, "
            f"Skip: {self.skips}, "
            f"Error: {self.errors}"
        )


# Search paths for test definition YAML files
DEFINITION_SEARCH_PATHS = [
    Path(__file__).parent.parent.parent.parent / "hwtest-intg" / "configs",
]

# Search paths for rack configuration YAML files
RACK_CONFIG_SEARCH_PATHS = [
    Path(__file__).parent.parent.parent.parent / "hwtest-intg" / "src" / "hwtest_intg" / "configs",
    Path(__file__).parent.parent.parent.parent / "hwtest-intg" / "configs",
]


def _find_rack_config(rack_id: str) -> Path | None:
    """Find a rack configuration file by ID."""
    filenames = [f"{rack_id}.yaml", f"{rack_id}.yml"]
    for search_dir in RACK_CONFIG_SEARCH_PATHS:
        if not search_dir.is_dir():
            continue
        for filename in filenames:
            candidate = search_dir / filename
            if candidate.is_file():
                return candidate
    return None


def _create_environmental_state(state: MonitorState) -> EnvironmentalState:
    """Create an EnvironmentalState from a MonitorState."""
    return EnvironmentalState(
        state_id=StateId(state.id),
        name=state.name,
        description=state.description or f"{state.name} ({state.target_voltage}V)",
        metadata={"target_voltage": state.target_voltage, **state.parameters},
    )


class TestExecutor:
    """Runs test cases. Only one at a time.

    The executor manages the lifecycle of a single test run, providing
    status updates and cooperative cancellation.

    Args:
        station: Station configuration.
        rack: Initialized Rack instance.
        rack_instance: Rack instance configuration with calibration data.
        definition_search_paths: Additional paths to search for test definitions.
        rack_config_search_paths: Additional paths to search for rack configs.
    """

    def __init__(
        self,
        station: StationConfig,
        rack: Rack,
        rack_instance: RackInstanceConfig,
        definition_search_paths: list[Path] | None = None,
        rack_config_search_paths: list[Path] | None = None,
    ) -> None:
        self._station = station
        self._rack = rack
        self._rack_instance = rack_instance
        self._definition_search_paths = definition_search_paths or list(DEFINITION_SEARCH_PATHS)
        self._rack_config_search_paths = rack_config_search_paths or list(RACK_CONFIG_SEARCH_PATHS)

        self._state: RunState = RunState.IDLE
        self._cancel_requested = False
        self._task: asyncio.Task[None] | None = None
        self._stats = RunStatistics()
        self._test_case_id: str | None = None
        self._mode: str | None = None
        self._current_state_name: str | None = None
        self._started_at: datetime | None = None
        self._message: str = "Idle"
        self._lock = asyncio.Lock()

    def get_status(self) -> RunStatus:
        """Return current run status (non-blocking)."""
        return RunStatus(
            state=self._state,
            test_case_id=self._test_case_id,
            mode=self._mode,
            current_state=self._current_state_name,
            cycle=self._stats.cycles_completed,
            stats=self._stats.to_dict(),
            started_at=self._started_at.isoformat() if self._started_at else None,
            message=self._message,
        )

    async def start(self, test_case_id: str, mode: str) -> None:
        """Start a test run in a background asyncio task.

        Args:
            test_case_id: ID of the test case to run.
            mode: Execution mode (functional, hass, halt).

        Raises:
            ValueError: If test case not found or mode not valid.
            RuntimeError: If a test is already running.
        """
        async with self._lock:
            if self._state != RunState.IDLE:
                raise RuntimeError(f"Cannot start: executor is {self._state.value}")

            # Validate test case
            entry = self._station.get_test_case(test_case_id)
            if entry is None:
                raise ValueError(f"Test case not found: {test_case_id}")

            if mode not in entry.modes:
                raise ValueError(
                    f"Mode '{mode}' not available for '{test_case_id}'. "
                    f"Available: {entry.modes}"
                )

            # Reset state
            self._state = RunState.RUNNING
            self._cancel_requested = False
            self._stats = RunStatistics()
            self._test_case_id = test_case_id
            self._mode = mode
            self._current_state_name = None
            self._started_at = datetime.now(timezone.utc)
            self._message = f"Starting {entry.name} in {mode} mode"

            # Launch background task
            self._task = asyncio.create_task(self._run(entry, mode))

    async def stop(self) -> None:
        """Request cancellation of current run."""
        if self._state == RunState.RUNNING:
            self._state = RunState.STOPPING
            self._cancel_requested = True
            self._message = "Stop requested, finishing current cycle..."
            logger.info("Stop requested for %s", self._test_case_id)

    async def wait(self) -> None:
        """Wait for the current run to complete."""
        if self._task is not None:
            await self._task

    async def _run(self, entry: TestCaseEntry, mode: str) -> None:
        """Execute a test run (runs in background task)."""
        try:
            # Load test definition
            self._message = "Loading test definition..."
            definition = load_definition(
                entry.definition,
                search_paths=self._definition_search_paths,
            )
            logger.info("Loaded test definition: %s", definition.source_path)

            # Build monitor
            monitor_def = definition.monitors.get("echo_voltage_monitor")
            if monitor_def is None:
                raise ValueError("No 'echo_voltage_monitor' in test definition")
            monitor = Monitor(monitor_def=monitor_def)

            # Build environmental states
            states = [
                _create_environmental_state(s) for s in definition.get_states_in_sequence()
            ]

            # Run the test
            async with httpx.AsyncClient(
                base_url=self._station.uut.url, timeout=10.0
            ) as uut_http:
                self._message = f"Running {entry.name}"

                if mode == "functional":
                    failure = await self._run_single_pass(
                        uut_http, monitor, states, definition
                    )
                    if failure:
                        self._message = f"FAIL: {failure.message}"
                    else:
                        self._message = f"PASS ({self._stats.summary()})"
                else:
                    # Continuous mode (hass / halt)
                    while not self._cancel_requested:
                        failure = await self._run_single_pass(
                            uut_http, monitor, states, definition
                        )
                        if failure:
                            self._message = (
                                f"FAIL on cycle {self._stats.cycles_completed}: "
                                f"{failure.message}"
                            )
                            break

                        self._message = (
                            f"Cycle {self._stats.cycles_completed} complete. "
                            f"{self._stats.summary()}"
                        )

                    if self._cancel_requested and not self._stats.failures:
                        self._message = (
                            f"Stopped after {self._stats.cycles_completed} cycles. "
                            f"{self._stats.summary()}"
                        )

        except Exception as exc:
            logger.exception("Test run failed with exception")
            self._message = f"ERROR: {exc}"
            self._stats.errors += 1
        finally:
            # Reset DAC outputs
            try:
                self._rack.write_analog("rack_dac", 0.0)
            except Exception:
                pass
            try:
                async with httpx.AsyncClient(
                    base_url=self._station.uut.url, timeout=5.0
                ) as client:
                    await client.post("/dac/write", json={"channel": 0, "voltage": 0.0})
            except Exception:
                pass

            self._state = RunState.IDLE
            self._current_state_name = None
            logger.info("Run complete: %s", self._message)

    async def _run_single_pass(
        self,
        uut_http: httpx.AsyncClient,
        monitor: Monitor,
        states: list[EnvironmentalState],
        definition: TestDefinition,
    ) -> MonitorResult | None:
        """Run a single pass through all states.

        Returns the first failing result, or None if all passed.
        """
        for i, state in enumerate(states):
            if self._cancel_requested:
                return None

            self._current_state_name = state.name

            if i > 0:
                logger.info("State transition: %s -> %s", states[i - 1].name, state.name)

            logger.info(
                "Testing state: %s (target: %sV)",
                state.name,
                state.metadata.get("target_voltage"),
            )

            readings = await self._run_echo_cycle(uut_http, state, definition)

            result = monitor.evaluate({"echo_voltage": readings.rack_adc_voltage}, state)
            self._stats.record(result)

            if result.passed:
                logger.info("  PASS: %s", result.message)
            elif result.failed:
                logger.error("  FAIL: %s", result.message)
                return result
            else:
                logger.warning("  %s: %s", result.verdict.name, result.message)

        self._stats.cycles_completed += 1
        return None

    async def _run_echo_cycle(
        self,
        uut_http: httpx.AsyncClient,
        state: EnvironmentalState,
        definition: TestDefinition,
    ) -> VoltageReadings:
        """Run one echo cycle for a given state."""
        target_voltage = state.metadata.get("target_voltage", 0.0)
        settling_time = definition.case_parameters.get("settling_time_seconds", 0.025)
        uut_adc_scale = self._rack_instance.get_calibration("uut_adc_scale_factor", 1.0)
        mcc118_scale = self._rack_instance.get_calibration("mcc118_scale_factor", 1.0)

        # Step 1: Set output voltage on rack DAC
        self._rack.write_analog("rack_dac", target_voltage)
        await asyncio.sleep(settling_time)

        # Step 2: Read voltage on UUT ADC (apply calibration)
        resp = await uut_http.get("/adc/0")
        resp.raise_for_status()
        uut_adc_raw: float = resp.json()["voltage"]
        uut_adc_voltage = uut_adc_raw * uut_adc_scale

        # Step 3: Write calibrated voltage to UUT DAC (echo)
        resp = await uut_http.post("/dac/write", json={"channel": 0, "voltage": uut_adc_voltage})
        resp.raise_for_status()
        await asyncio.sleep(settling_time)

        # Step 4: Read echoed voltage on rack ADC
        rack_adc_raw = self._rack.read_analog("rack_adc")
        measured_voltage = rack_adc_raw * mcc118_scale

        return VoltageReadings(
            target_voltage=target_voltage,
            rack_dac_voltage=target_voltage,
            uut_adc_voltage=uut_adc_voltage,
            uut_dac_voltage=uut_adc_voltage,
            rack_adc_voltage=measured_voltage,
            state_name=state.name,
        )
