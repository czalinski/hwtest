"""Test results database persistence for hwtest."""

from hwtest_db.connection import Database, create_database, open_database
from hwtest_db.models import (
    DesignRevision,
    EnvironmentalState,
    Requirement,
    RequirementSource,
    RunStatus,
    RunType,
    SystemFailure,
    TestCase,
    TestOutcome,
    TestRun,
    TestRunUnit,
    TestRunUnitOutcome,
    Unit,
    UnitFailure,
    UnitType,
)
from hwtest_db.repositories import TestCaseRepository, TestRunRepository, UnitRepository

__all__ = [
    # Connection management
    "Database",
    "create_database",
    "open_database",
    # Models
    "DesignRevision",
    "EnvironmentalState",
    "Requirement",
    "RequirementSource",
    "RunStatus",
    "RunType",
    "SystemFailure",
    "TestCase",
    "TestOutcome",
    "TestRun",
    "TestRunUnit",
    "TestRunUnitOutcome",
    "Unit",
    "UnitFailure",
    "UnitType",
    # Repositories
    "TestCaseRepository",
    "TestRunRepository",
    "UnitRepository",
]
