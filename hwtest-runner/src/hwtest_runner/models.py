"""Pydantic models for the hwtest-runner REST API.

This module defines request and response models for the test runner web service.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class RunState(str, Enum):
    """State of the test executor.

    Attributes:
        IDLE: No test is running.
        RUNNING: A test is currently executing.
        STOPPING: A stop has been requested; waiting for current cycle to finish.
    """

    IDLE = "idle"
    RUNNING = "running"
    STOPPING = "stopping"


class TestCaseModel(BaseModel):
    """A test case available for execution.

    Attributes:
        id: Unique test case identifier.
        name: Human-readable display name.
        modes: Available execution modes.
    """

    id: str
    name: str
    modes: list[str]


class RunRequest(BaseModel):
    """Request to start a test run.

    Attributes:
        test_case_id: ID of the test case to run.
        mode: Execution mode (functional, hass, halt).
    """

    test_case_id: str
    mode: str = "functional"


class RunStatus(BaseModel):
    """Current status of a test run.

    Attributes:
        state: Current executor state.
        test_case_id: ID of the running test case (if any).
        mode: Execution mode of the current run.
        current_state: Current environmental state name.
        cycle: Number of completed cycles.
        stats: Execution statistics (passes, failures, etc.).
        started_at: ISO timestamp when the run started.
        message: Human-readable status message.
    """

    state: RunState
    test_case_id: str | None = None
    mode: str | None = None
    current_state: str | None = None
    cycle: int = 0
    stats: dict[str, int] = {}
    started_at: str | None = None
    message: str = ""


class StationStatus(BaseModel):
    """Full station status including run state and available test cases.

    Attributes:
        station_id: Unique station identifier.
        description: Station description.
        rack_state: Current state of the rack (ready, error, etc.).
        run: Current run status.
        test_cases: Available test cases.
    """

    station_id: str
    description: str
    rack_state: str
    run: RunStatus
    test_cases: list[TestCaseModel]
