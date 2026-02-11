"""Web-based test execution service for hwtest.

This package provides a FastAPI-based web service for running hardware tests
from a browser. An operator can select a test case, choose a mode (functional,
HASS, HALT), and monitor execution in real time.

Example:
    hwtest-runner configs/pi5_bench_a_station.yaml --port 8000
"""

from hwtest_runner.config import (
    RackReference,
    StationConfig,
    TestCaseEntry,
    UutConfig,
    load_station_config,
)
from hwtest_runner.executor import TestExecutor
from hwtest_runner.models import RunRequest, RunState, RunStatus, StationStatus, TestCaseModel

__all__ = [
    # Config
    "RackReference",
    "StationConfig",
    "TestCaseEntry",
    "UutConfig",
    "load_station_config",
    # Executor
    "TestExecutor",
    # Models
    "RunRequest",
    "RunState",
    "RunStatus",
    "StationStatus",
    "TestCaseModel",
]
