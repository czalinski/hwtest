"""Repository classes for database operations."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    import aiosqlite


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse SQLite timestamp string to datetime."""
    if value is None:
        return None
    # SQLite stores as "YYYY-MM-DD HH:MM:SS"
    return datetime.fromisoformat(value)


class UnitRepository:
    """Repository for unit types, design revisions, and units."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    # --- Unit Types ---

    async def create_unit_type(self, unit_type: UnitType) -> int:
        """Create a unit type and return its ID."""
        cursor = await self._db.execute(
            "INSERT INTO unit_type (name, description) VALUES (?, ?)",
            (unit_type.name, unit_type.description),
        )
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_unit_type(self, unit_type_id: int) -> UnitType | None:
        """Get a unit type by ID."""
        cursor = await self._db.execute(
            "SELECT id, name, description FROM unit_type WHERE id = ?",
            (unit_type_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return UnitType(id=row["id"], name=row["name"], description=row["description"])

    async def get_unit_type_by_name(self, name: str) -> UnitType | None:
        """Get a unit type by name."""
        cursor = await self._db.execute(
            "SELECT id, name, description FROM unit_type WHERE name = ?",
            (name,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return UnitType(id=row["id"], name=row["name"], description=row["description"])

    async def list_unit_types(self) -> list[UnitType]:
        """List all unit types."""
        cursor = await self._db.execute("SELECT id, name, description FROM unit_type")
        rows = await cursor.fetchall()
        return [
            UnitType(id=row["id"], name=row["name"], description=row["description"]) for row in rows
        ]

    # --- Design Revisions ---

    async def create_design_revision(self, revision: DesignRevision) -> int:
        """Create a design revision and return its ID."""
        cursor = await self._db.execute(
            "INSERT INTO design_revision (unit_type_id, revision) VALUES (?, ?)",
            (revision.unit_type_id, revision.revision),
        )
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_design_revision(self, revision_id: int) -> DesignRevision | None:
        """Get a design revision by ID."""
        cursor = await self._db.execute(
            "SELECT id, unit_type_id, revision, created_at FROM design_revision WHERE id = ?",
            (revision_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return DesignRevision(
            id=row["id"],
            unit_type_id=row["unit_type_id"],
            revision=row["revision"],
            created_at=_parse_datetime(row["created_at"]),
        )

    async def get_design_revision_by_name(
        self, unit_type_id: int, revision: str
    ) -> DesignRevision | None:
        """Get a design revision by unit type and revision string."""
        cursor = await self._db.execute(
            "SELECT id, unit_type_id, revision, created_at FROM design_revision "
            "WHERE unit_type_id = ? AND revision = ?",
            (unit_type_id, revision),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return DesignRevision(
            id=row["id"],
            unit_type_id=row["unit_type_id"],
            revision=row["revision"],
            created_at=_parse_datetime(row["created_at"]),
        )

    async def list_design_revisions(self, unit_type_id: int) -> list[DesignRevision]:
        """List all design revisions for a unit type."""
        cursor = await self._db.execute(
            "SELECT id, unit_type_id, revision, created_at FROM design_revision "
            "WHERE unit_type_id = ? ORDER BY created_at",
            (unit_type_id,),
        )
        rows = await cursor.fetchall()
        return [
            DesignRevision(
                id=row["id"],
                unit_type_id=row["unit_type_id"],
                revision=row["revision"],
                created_at=_parse_datetime(row["created_at"]),
            )
            for row in rows
        ]

    # --- Units ---

    async def create_unit(self, unit: Unit) -> int:
        """Create a unit and return its ID."""
        cursor = await self._db.execute(
            "INSERT INTO unit (serial_number, design_revision_id) VALUES (?, ?)",
            (unit.serial_number, unit.design_revision_id),
        )
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_unit(self, unit_id: int) -> Unit | None:
        """Get a unit by ID."""
        cursor = await self._db.execute(
            "SELECT id, serial_number, design_revision_id, created_at FROM unit WHERE id = ?",
            (unit_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return Unit(
            id=row["id"],
            serial_number=row["serial_number"],
            design_revision_id=row["design_revision_id"],
            created_at=_parse_datetime(row["created_at"]),
        )

    async def get_unit_by_serial(self, serial_number: str) -> Unit | None:
        """Get a unit by serial number."""
        cursor = await self._db.execute(
            "SELECT id, serial_number, design_revision_id, created_at FROM unit "
            "WHERE serial_number = ?",
            (serial_number,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return Unit(
            id=row["id"],
            serial_number=row["serial_number"],
            design_revision_id=row["design_revision_id"],
            created_at=_parse_datetime(row["created_at"]),
        )


class TestCaseRepository:
    """Repository for test cases, environmental states, and requirements."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    # --- Test Cases ---

    async def create_test_case(self, test_case: TestCase) -> int:
        """Create a test case and return its ID."""
        cursor = await self._db.execute(
            "INSERT INTO test_case (name, unit_type_id, description) VALUES (?, ?, ?)",
            (test_case.name, test_case.unit_type_id, test_case.description),
        )
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_test_case(self, test_case_id: int) -> TestCase | None:
        """Get a test case by ID."""
        cursor = await self._db.execute(
            "SELECT id, name, unit_type_id, description FROM test_case WHERE id = ?",
            (test_case_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return TestCase(
            id=row["id"],
            name=row["name"],
            unit_type_id=row["unit_type_id"],
            description=row["description"],
        )

    async def get_test_case_by_name(self, unit_type_id: int, name: str) -> TestCase | None:
        """Get a test case by unit type and name."""
        cursor = await self._db.execute(
            "SELECT id, name, unit_type_id, description FROM test_case "
            "WHERE unit_type_id = ? AND name = ?",
            (unit_type_id, name),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return TestCase(
            id=row["id"],
            name=row["name"],
            unit_type_id=row["unit_type_id"],
            description=row["description"],
        )

    async def list_test_cases(self, unit_type_id: int) -> list[TestCase]:
        """List all test cases for a unit type."""
        cursor = await self._db.execute(
            "SELECT id, name, unit_type_id, description FROM test_case WHERE unit_type_id = ?",
            (unit_type_id,),
        )
        rows = await cursor.fetchall()
        return [
            TestCase(
                id=row["id"],
                name=row["name"],
                unit_type_id=row["unit_type_id"],
                description=row["description"],
            )
            for row in rows
        ]

    # --- Environmental States ---

    async def create_environmental_state(self, state: EnvironmentalState) -> int:
        """Create an environmental state and return its ID."""
        cursor = await self._db.execute(
            "INSERT INTO environmental_state (test_case_id, name) VALUES (?, ?)",
            (state.test_case_id, state.name),
        )
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_environmental_state(self, state_id: int) -> EnvironmentalState | None:
        """Get an environmental state by ID."""
        cursor = await self._db.execute(
            "SELECT id, test_case_id, name FROM environmental_state WHERE id = ?",
            (state_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return EnvironmentalState(
            id=row["id"],
            test_case_id=row["test_case_id"],
            name=row["name"],
        )

    async def list_environmental_states(self, test_case_id: int) -> list[EnvironmentalState]:
        """List all environmental states for a test case."""
        cursor = await self._db.execute(
            "SELECT id, test_case_id, name FROM environmental_state WHERE test_case_id = ?",
            (test_case_id,),
        )
        rows = await cursor.fetchall()
        return [
            EnvironmentalState(
                id=row["id"],
                test_case_id=row["test_case_id"],
                name=row["name"],
            )
            for row in rows
        ]

    # --- Requirements ---

    async def create_requirement(self, requirement: Requirement) -> int:
        """Create a requirement and return its ID."""
        cursor = await self._db.execute(
            "INSERT INTO requirement (test_case_id, name, source) VALUES (?, ?, ?)",
            (requirement.test_case_id, requirement.name, requirement.source.value),
        )
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_requirement(self, requirement_id: int) -> Requirement | None:
        """Get a requirement by ID."""
        cursor = await self._db.execute(
            "SELECT id, test_case_id, name, source FROM requirement WHERE id = ?",
            (requirement_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return Requirement(
            id=row["id"],
            test_case_id=row["test_case_id"],
            name=row["name"],
            source=RequirementSource(row["source"]),
        )

    async def list_requirements(self, test_case_id: int) -> list[Requirement]:
        """List all requirements for a test case."""
        cursor = await self._db.execute(
            "SELECT id, test_case_id, name, source FROM requirement WHERE test_case_id = ?",
            (test_case_id,),
        )
        rows = await cursor.fetchall()
        return [
            Requirement(
                id=row["id"],
                test_case_id=row["test_case_id"],
                name=row["name"],
                source=RequirementSource(row["source"]),
            )
            for row in rows
        ]


class TestRunRepository:
    """Repository for test runs, units under test, and failures."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    # --- Test Runs ---

    async def create_test_run(self, test_run: TestRun) -> int:
        """Create a test run and return its ID."""
        cursor = await self._db.execute(
            "INSERT INTO test_run (test_case_id, run_type, status) VALUES (?, ?, ?)",
            (test_run.test_case_id, test_run.run_type.value, test_run.status.value),
        )
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_test_run(self, test_run_id: int) -> TestRun | None:
        """Get a test run by ID."""
        cursor = await self._db.execute(
            "SELECT id, test_case_id, run_type, started_at, finished_at, status "
            "FROM test_run WHERE id = ?",
            (test_run_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return TestRun(
            id=row["id"],
            test_case_id=row["test_case_id"],
            run_type=RunType(row["run_type"]),
            started_at=_parse_datetime(row["started_at"]),
            finished_at=_parse_datetime(row["finished_at"]),
            status=RunStatus(row["status"]),
        )

    async def update_test_run_status(
        self, test_run_id: int, status: RunStatus, finished_at: datetime | None = None
    ) -> None:
        """Update a test run's status and optionally its finish time."""
        if finished_at is not None:
            await self._db.execute(
                "UPDATE test_run SET status = ?, finished_at = ? WHERE id = ?",
                (status.value, finished_at.isoformat(sep=" "), test_run_id),
            )
        else:
            await self._db.execute(
                "UPDATE test_run SET status = ? WHERE id = ?",
                (status.value, test_run_id),
            )
        await self._db.commit()

    async def complete_test_run(self, test_run_id: int) -> None:
        """Mark a test run as completed."""
        await self.update_test_run_status(test_run_id, RunStatus.COMPLETED, datetime.now())

    async def terminate_test_run(self, test_run_id: int) -> None:
        """Mark a test run as terminated."""
        await self.update_test_run_status(test_run_id, RunStatus.TERMINATED, datetime.now())

    async def list_test_runs(
        self, test_case_id: int | None = None, limit: int = 100
    ) -> list[TestRun]:
        """List test runs, optionally filtered by test case."""
        if test_case_id is not None:
            cursor = await self._db.execute(
                "SELECT id, test_case_id, run_type, started_at, finished_at, status "
                "FROM test_run WHERE test_case_id = ? ORDER BY started_at DESC LIMIT ?",
                (test_case_id, limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT id, test_case_id, run_type, started_at, finished_at, status "
                "FROM test_run ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [
            TestRun(
                id=row["id"],
                test_case_id=row["test_case_id"],
                run_type=RunType(row["run_type"]),
                started_at=_parse_datetime(row["started_at"]),
                finished_at=_parse_datetime(row["finished_at"]),
                status=RunStatus(row["status"]),
            )
            for row in rows
        ]

    # --- Test Run Units ---

    async def add_unit_to_run(self, test_run_unit: TestRunUnit) -> None:
        """Add a unit to a test run."""
        await self._db.execute(
            "INSERT INTO test_run_unit (test_run_id, unit_id, slot_number) VALUES (?, ?, ?)",
            (test_run_unit.test_run_id, test_run_unit.unit_id, test_run_unit.slot_number),
        )
        await self._db.commit()

    async def list_units_in_run(self, test_run_id: int) -> list[TestRunUnit]:
        """List all units in a test run."""
        cursor = await self._db.execute(
            "SELECT test_run_id, unit_id, slot_number FROM test_run_unit "
            "WHERE test_run_id = ? ORDER BY slot_number",
            (test_run_id,),
        )
        rows = await cursor.fetchall()
        return [
            TestRunUnit(
                test_run_id=row["test_run_id"],
                unit_id=row["unit_id"],
                slot_number=row["slot_number"],
            )
            for row in rows
        ]

    # --- System Failures ---

    async def record_system_failure(self, failure: SystemFailure) -> int:
        """Record a system failure and return its ID."""
        cursor = await self._db.execute(
            "INSERT INTO system_failure (test_run_id, pareto_code, description) VALUES (?, ?, ?)",
            (failure.test_run_id, failure.pareto_code, failure.description),
        )
        await self._db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def list_system_failures(self, test_run_id: int) -> list[SystemFailure]:
        """List all system failures for a test run."""
        cursor = await self._db.execute(
            "SELECT id, test_run_id, occurred_at, pareto_code, description "
            "FROM system_failure WHERE test_run_id = ? ORDER BY occurred_at",
            (test_run_id,),
        )
        rows = await cursor.fetchall()
        return [
            SystemFailure(
                id=row["id"],
                test_run_id=row["test_run_id"],
                occurred_at=_parse_datetime(row["occurred_at"]),
                pareto_code=row["pareto_code"],
                description=row["description"],
            )
            for row in rows
        ]

    # --- Unit Failures ---

    async def record_unit_failure(self, failure: UnitFailure) -> int:
        """Record a unit failure and return its ID.

        Only the first failure per (run, unit, requirement, state) is recorded.
        Subsequent failures are silently ignored (returns -1).
        """
        try:
            cursor = await self._db.execute(
                "INSERT INTO unit_failure "
                "(test_run_id, unit_id, requirement_id, environmental_state_id, "
                "measured_value, bound_description, description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    failure.test_run_id,
                    failure.unit_id,
                    failure.requirement_id,
                    failure.environmental_state_id,
                    failure.measured_value,
                    failure.bound_description,
                    failure.description,
                ),
            )
            await self._db.commit()
            return cursor.lastrowid  # type: ignore[return-value]
        except Exception:  # pylint: disable=broad-exception-caught
            # Unique constraint violation - failure already recorded
            return -1

    async def list_unit_failures(
        self, test_run_id: int, unit_id: int | None = None
    ) -> list[UnitFailure]:
        """List unit failures for a test run, optionally filtered by unit."""
        if unit_id is not None:
            cursor = await self._db.execute(
                "SELECT id, test_run_id, unit_id, requirement_id, environmental_state_id, "
                "occurred_at, measured_value, bound_description, description "
                "FROM unit_failure WHERE test_run_id = ? AND unit_id = ? ORDER BY occurred_at",
                (test_run_id, unit_id),
            )
        else:
            cursor = await self._db.execute(
                "SELECT id, test_run_id, unit_id, requirement_id, environmental_state_id, "
                "occurred_at, measured_value, bound_description, description "
                "FROM unit_failure WHERE test_run_id = ? ORDER BY occurred_at",
                (test_run_id,),
            )
        rows = await cursor.fetchall()
        return [
            UnitFailure(
                id=row["id"],
                test_run_id=row["test_run_id"],
                unit_id=row["unit_id"],
                requirement_id=row["requirement_id"],
                environmental_state_id=row["environmental_state_id"],
                occurred_at=_parse_datetime(row["occurred_at"]),
                measured_value=row["measured_value"],
                bound_description=row["bound_description"],
                description=row["description"],
            )
            for row in rows
        ]

    # --- Outcomes ---

    async def get_unit_outcomes(self, test_run_id: int) -> list[TestRunUnitOutcome]:
        """Get the computed outcome for all units in a test run."""
        cursor = await self._db.execute(
            "SELECT test_run_id, unit_id, slot_number, outcome "
            "FROM test_run_unit_outcome WHERE test_run_id = ? ORDER BY slot_number",
            (test_run_id,),
        )
        rows = await cursor.fetchall()
        return [
            TestRunUnitOutcome(
                test_run_id=row["test_run_id"],
                unit_id=row["unit_id"],
                slot_number=row["slot_number"],
                outcome=TestOutcome(row["outcome"]),
            )
            for row in rows
        ]
