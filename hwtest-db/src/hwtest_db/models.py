"""Database models for test results persistence.

These dataclasses map to the tables defined in schema/test_results_schema.sql.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal


class RunType(Enum):
    """Type of test run."""

    HASS = "hass"
    HALT = "halt"
    FUNCTIONAL = "functional"
    AD_HOC = "ad_hoc"


class RunStatus(Enum):
    """Status of a test run."""

    RUNNING = "running"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class RequirementSource(Enum):
    """Source type for a requirement."""

    MONITOR = "monitor"
    POINT_CHECK = "point_check"


class TestOutcome(Enum):
    """Outcome for a unit in a test run."""

    PASS = "pass"
    FAIL = "fail"
    INDETERMINATE = "indeterminate"


@dataclass
class UnitType:
    """A type of unit under test (e.g., a product model)."""

    id: int | None
    name: str
    description: str | None = None


@dataclass
class DesignRevision:
    """A specific design revision of a unit type."""

    id: int | None
    unit_type_id: int
    revision: str
    created_at: datetime | None = None


@dataclass
class Unit:
    """An individual unit identified by serial number."""

    id: int | None
    serial_number: str
    design_revision_id: int
    created_at: datetime | None = None


@dataclass
class TestCase:
    """A test case definition targeting a unit type."""

    id: int | None
    name: str
    unit_type_id: int
    description: str | None = None


@dataclass
class EnvironmentalState:
    """An environmental state defined for a test case."""

    id: int | None
    test_case_id: int
    name: str


@dataclass
class Requirement:
    """A requirement defined for a test case."""

    id: int | None
    test_case_id: int
    name: str
    source: RequirementSource


@dataclass
class TestRun:
    """A single execution of a test case."""

    id: int | None
    test_case_id: int
    run_type: RunType
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: RunStatus = RunStatus.RUNNING


@dataclass
class TestRunUnit:
    """A unit placed in a test fixture for a run."""

    test_run_id: int
    unit_id: int
    slot_number: int


@dataclass
class SystemFailure:
    """A system failure that terminated a test run."""

    id: int | None
    test_run_id: int
    pareto_code: str
    description: str
    occurred_at: datetime | None = None


@dataclass
class UnitFailure:
    """A requirement violation for a specific unit."""

    id: int | None
    test_run_id: int
    unit_id: int
    requirement_id: int
    environmental_state_id: int
    measured_value: float
    bound_description: str
    description: str | None = None
    occurred_at: datetime | None = None


@dataclass
class TestRunUnitOutcome:
    """Computed outcome for a unit in a test run (from view)."""

    test_run_id: int
    unit_id: int
    slot_number: int
    outcome: TestOutcome
