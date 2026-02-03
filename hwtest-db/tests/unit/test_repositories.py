"""Unit tests for database repositories."""

import pytest

from hwtest_db import (
    Database,
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
    Unit,
    UnitFailure,
    UnitType,
    TestCaseRepository,
    TestRunRepository,
    UnitRepository,
)


@pytest.fixture
async def db():
    """Create an in-memory database with schema."""
    async with Database(":memory:", create=True) as connection:
        yield connection


class TestUnitRepository:
    """Tests for UnitRepository."""

    @pytest.mark.asyncio
    async def test_create_and_get_unit_type(self, db) -> None:
        """Test creating and retrieving a unit type."""
        repo = UnitRepository(db)

        unit_type = UnitType(id=None, name="Widget", description="A test widget")
        unit_type_id = await repo.create_unit_type(unit_type)

        retrieved = await repo.get_unit_type(unit_type_id)
        assert retrieved is not None
        assert retrieved.id == unit_type_id
        assert retrieved.name == "Widget"
        assert retrieved.description == "A test widget"

    @pytest.mark.asyncio
    async def test_get_unit_type_by_name(self, db) -> None:
        """Test retrieving a unit type by name."""
        repo = UnitRepository(db)

        unit_type = UnitType(id=None, name="Gadget", description=None)
        await repo.create_unit_type(unit_type)

        retrieved = await repo.get_unit_type_by_name("Gadget")
        assert retrieved is not None
        assert retrieved.name == "Gadget"

    @pytest.mark.asyncio
    async def test_list_unit_types(self, db) -> None:
        """Test listing all unit types."""
        repo = UnitRepository(db)

        await repo.create_unit_type(UnitType(id=None, name="Type1"))
        await repo.create_unit_type(UnitType(id=None, name="Type2"))

        types = await repo.list_unit_types()
        assert len(types) == 2
        names = {t.name for t in types}
        assert names == {"Type1", "Type2"}

    @pytest.mark.asyncio
    async def test_create_and_get_design_revision(self, db) -> None:
        """Test creating and retrieving a design revision."""
        repo = UnitRepository(db)

        unit_type_id = await repo.create_unit_type(UnitType(id=None, name="Widget"))
        revision = DesignRevision(id=None, unit_type_id=unit_type_id, revision="A")
        revision_id = await repo.create_design_revision(revision)

        retrieved = await repo.get_design_revision(revision_id)
        assert retrieved is not None
        assert retrieved.id == revision_id
        assert retrieved.unit_type_id == unit_type_id
        assert retrieved.revision == "A"
        assert retrieved.created_at is not None

    @pytest.mark.asyncio
    async def test_list_design_revisions(self, db) -> None:
        """Test listing design revisions for a unit type."""
        repo = UnitRepository(db)

        unit_type_id = await repo.create_unit_type(UnitType(id=None, name="Widget"))
        await repo.create_design_revision(
            DesignRevision(id=None, unit_type_id=unit_type_id, revision="A")
        )
        await repo.create_design_revision(
            DesignRevision(id=None, unit_type_id=unit_type_id, revision="B")
        )

        revisions = await repo.list_design_revisions(unit_type_id)
        assert len(revisions) == 2
        revision_names = [r.revision for r in revisions]
        assert "A" in revision_names
        assert "B" in revision_names

    @pytest.mark.asyncio
    async def test_create_and_get_unit(self, db) -> None:
        """Test creating and retrieving a unit."""
        repo = UnitRepository(db)

        unit_type_id = await repo.create_unit_type(UnitType(id=None, name="Widget"))
        revision_id = await repo.create_design_revision(
            DesignRevision(id=None, unit_type_id=unit_type_id, revision="A")
        )

        unit = Unit(id=None, serial_number="SN001", design_revision_id=revision_id)
        unit_id = await repo.create_unit(unit)

        retrieved = await repo.get_unit(unit_id)
        assert retrieved is not None
        assert retrieved.id == unit_id
        assert retrieved.serial_number == "SN001"
        assert retrieved.design_revision_id == revision_id

    @pytest.mark.asyncio
    async def test_get_unit_by_serial(self, db) -> None:
        """Test retrieving a unit by serial number."""
        repo = UnitRepository(db)

        unit_type_id = await repo.create_unit_type(UnitType(id=None, name="Widget"))
        revision_id = await repo.create_design_revision(
            DesignRevision(id=None, unit_type_id=unit_type_id, revision="A")
        )
        await repo.create_unit(
            Unit(id=None, serial_number="SN12345", design_revision_id=revision_id)
        )

        retrieved = await repo.get_unit_by_serial("SN12345")
        assert retrieved is not None
        assert retrieved.serial_number == "SN12345"


class TestTestCaseRepository:
    """Tests for TestCaseRepository."""

    @pytest.mark.asyncio
    async def test_create_and_get_test_case(self, db) -> None:
        """Test creating and retrieving a test case."""
        unit_repo = UnitRepository(db)
        tc_repo = TestCaseRepository(db)

        unit_type_id = await unit_repo.create_unit_type(UnitType(id=None, name="Widget"))
        test_case = TestCase(
            id=None,
            name="Thermal Cycle",
            unit_type_id=unit_type_id,
            description="Standard thermal test",
        )
        test_case_id = await tc_repo.create_test_case(test_case)

        retrieved = await tc_repo.get_test_case(test_case_id)
        assert retrieved is not None
        assert retrieved.name == "Thermal Cycle"
        assert retrieved.unit_type_id == unit_type_id

    @pytest.mark.asyncio
    async def test_create_environmental_state(self, db) -> None:
        """Test creating environmental states."""
        unit_repo = UnitRepository(db)
        tc_repo = TestCaseRepository(db)

        unit_type_id = await unit_repo.create_unit_type(UnitType(id=None, name="Widget"))
        test_case_id = await tc_repo.create_test_case(
            TestCase(id=None, name="Test", unit_type_id=unit_type_id)
        )

        state_id = await tc_repo.create_environmental_state(
            EnvironmentalState(id=None, test_case_id=test_case_id, name="hot")
        )

        retrieved = await tc_repo.get_environmental_state(state_id)
        assert retrieved is not None
        assert retrieved.name == "hot"

    @pytest.mark.asyncio
    async def test_list_environmental_states(self, db) -> None:
        """Test listing environmental states for a test case."""
        unit_repo = UnitRepository(db)
        tc_repo = TestCaseRepository(db)

        unit_type_id = await unit_repo.create_unit_type(UnitType(id=None, name="Widget"))
        test_case_id = await tc_repo.create_test_case(
            TestCase(id=None, name="Test", unit_type_id=unit_type_id)
        )

        await tc_repo.create_environmental_state(
            EnvironmentalState(id=None, test_case_id=test_case_id, name="cold")
        )
        await tc_repo.create_environmental_state(
            EnvironmentalState(id=None, test_case_id=test_case_id, name="hot")
        )

        states = await tc_repo.list_environmental_states(test_case_id)
        assert len(states) == 2
        state_names = {s.name for s in states}
        assert state_names == {"cold", "hot"}

    @pytest.mark.asyncio
    async def test_create_requirement(self, db) -> None:
        """Test creating requirements."""
        unit_repo = UnitRepository(db)
        tc_repo = TestCaseRepository(db)

        unit_type_id = await unit_repo.create_unit_type(UnitType(id=None, name="Widget"))
        test_case_id = await tc_repo.create_test_case(
            TestCase(id=None, name="Test", unit_type_id=unit_type_id)
        )

        req_id = await tc_repo.create_requirement(
            Requirement(
                id=None,
                test_case_id=test_case_id,
                name="voltage_check",
                source=RequirementSource.MONITOR,
            )
        )

        retrieved = await tc_repo.get_requirement(req_id)
        assert retrieved is not None
        assert retrieved.name == "voltage_check"
        assert retrieved.source == RequirementSource.MONITOR


class TestTestRunRepository:
    """Tests for TestRunRepository."""

    @pytest.fixture
    async def setup_data(self, db):
        """Set up test data for test run tests."""
        unit_repo = UnitRepository(db)
        tc_repo = TestCaseRepository(db)

        unit_type_id = await unit_repo.create_unit_type(UnitType(id=None, name="Widget"))
        revision_id = await unit_repo.create_design_revision(
            DesignRevision(id=None, unit_type_id=unit_type_id, revision="A")
        )
        unit_id = await unit_repo.create_unit(
            Unit(id=None, serial_number="SN001", design_revision_id=revision_id)
        )
        test_case_id = await tc_repo.create_test_case(
            TestCase(id=None, name="Thermal Cycle", unit_type_id=unit_type_id)
        )
        state_id = await tc_repo.create_environmental_state(
            EnvironmentalState(id=None, test_case_id=test_case_id, name="hot")
        )
        req_id = await tc_repo.create_requirement(
            Requirement(
                id=None,
                test_case_id=test_case_id,
                name="voltage",
                source=RequirementSource.MONITOR,
            )
        )

        return {
            "unit_type_id": unit_type_id,
            "revision_id": revision_id,
            "unit_id": unit_id,
            "test_case_id": test_case_id,
            "state_id": state_id,
            "req_id": req_id,
        }

    @pytest.mark.asyncio
    async def test_create_and_get_test_run(self, db, setup_data) -> None:
        """Test creating and retrieving a test run."""
        repo = TestRunRepository(db)

        test_run = TestRun(
            id=None,
            test_case_id=setup_data["test_case_id"],
            run_type=RunType.HALT,
        )
        run_id = await repo.create_test_run(test_run)

        retrieved = await repo.get_test_run(run_id)
        assert retrieved is not None
        assert retrieved.id == run_id
        assert retrieved.test_case_id == setup_data["test_case_id"]
        assert retrieved.run_type == RunType.HALT
        assert retrieved.status == RunStatus.RUNNING
        assert retrieved.started_at is not None

    @pytest.mark.asyncio
    async def test_complete_test_run(self, db, setup_data) -> None:
        """Test completing a test run."""
        repo = TestRunRepository(db)

        run_id = await repo.create_test_run(
            TestRun(id=None, test_case_id=setup_data["test_case_id"], run_type=RunType.HASS)
        )

        await repo.complete_test_run(run_id)

        retrieved = await repo.get_test_run(run_id)
        assert retrieved is not None
        assert retrieved.status == RunStatus.COMPLETED
        assert retrieved.finished_at is not None

    @pytest.mark.asyncio
    async def test_add_unit_to_run(self, db, setup_data) -> None:
        """Test adding units to a test run."""
        repo = TestRunRepository(db)

        run_id = await repo.create_test_run(
            TestRun(id=None, test_case_id=setup_data["test_case_id"], run_type=RunType.HALT)
        )

        await repo.add_unit_to_run(
            TestRunUnit(test_run_id=run_id, unit_id=setup_data["unit_id"], slot_number=1)
        )

        units = await repo.list_units_in_run(run_id)
        assert len(units) == 1
        assert units[0].unit_id == setup_data["unit_id"]
        assert units[0].slot_number == 1

    @pytest.mark.asyncio
    async def test_record_system_failure(self, db, setup_data) -> None:
        """Test recording a system failure."""
        repo = TestRunRepository(db)

        run_id = await repo.create_test_run(
            TestRun(id=None, test_case_id=setup_data["test_case_id"], run_type=RunType.HALT)
        )

        failure_id = await repo.record_system_failure(
            SystemFailure(
                id=None,
                test_run_id=run_id,
                pareto_code="SYS-001",
                description="Temperature controller communication failure",
            )
        )

        failures = await repo.list_system_failures(run_id)
        assert len(failures) == 1
        assert failures[0].id == failure_id
        assert failures[0].pareto_code == "SYS-001"

    @pytest.mark.asyncio
    async def test_record_unit_failure(self, db, setup_data) -> None:
        """Test recording a unit failure."""
        repo = TestRunRepository(db)

        run_id = await repo.create_test_run(
            TestRun(id=None, test_case_id=setup_data["test_case_id"], run_type=RunType.HALT)
        )
        await repo.add_unit_to_run(
            TestRunUnit(test_run_id=run_id, unit_id=setup_data["unit_id"], slot_number=1)
        )

        failure_id = await repo.record_unit_failure(
            UnitFailure(
                id=None,
                test_run_id=run_id,
                unit_id=setup_data["unit_id"],
                requirement_id=setup_data["req_id"],
                environmental_state_id=setup_data["state_id"],
                measured_value=3.8,
                bound_description="high > 3.5V",
                description="Voltage exceeded limit",
            )
        )

        failures = await repo.list_unit_failures(run_id)
        assert len(failures) == 1
        assert failures[0].id == failure_id
        assert failures[0].measured_value == 3.8

    @pytest.mark.asyncio
    async def test_duplicate_unit_failure_ignored(self, db, setup_data) -> None:
        """Test that duplicate unit failures are silently ignored."""
        repo = TestRunRepository(db)

        run_id = await repo.create_test_run(
            TestRun(id=None, test_case_id=setup_data["test_case_id"], run_type=RunType.HALT)
        )
        await repo.add_unit_to_run(
            TestRunUnit(test_run_id=run_id, unit_id=setup_data["unit_id"], slot_number=1)
        )

        failure = UnitFailure(
            id=None,
            test_run_id=run_id,
            unit_id=setup_data["unit_id"],
            requirement_id=setup_data["req_id"],
            environmental_state_id=setup_data["state_id"],
            measured_value=3.8,
            bound_description="high > 3.5V",
        )

        # First failure should succeed
        first_id = await repo.record_unit_failure(failure)
        assert first_id > 0

        # Second failure (same key) should return -1
        second_id = await repo.record_unit_failure(failure)
        assert second_id == -1

        # Should still only have one failure
        failures = await repo.list_unit_failures(run_id)
        assert len(failures) == 1

    @pytest.mark.asyncio
    async def test_unit_outcomes_pass(self, db, setup_data) -> None:
        """Test that unit outcome is 'pass' when no failures."""
        repo = TestRunRepository(db)

        run_id = await repo.create_test_run(
            TestRun(id=None, test_case_id=setup_data["test_case_id"], run_type=RunType.HALT)
        )
        await repo.add_unit_to_run(
            TestRunUnit(test_run_id=run_id, unit_id=setup_data["unit_id"], slot_number=1)
        )
        await repo.complete_test_run(run_id)

        outcomes = await repo.get_unit_outcomes(run_id)
        assert len(outcomes) == 1
        assert outcomes[0].outcome == TestOutcome.PASS

    @pytest.mark.asyncio
    async def test_unit_outcomes_fail(self, db, setup_data) -> None:
        """Test that unit outcome is 'fail' when unit failure exists."""
        repo = TestRunRepository(db)

        run_id = await repo.create_test_run(
            TestRun(id=None, test_case_id=setup_data["test_case_id"], run_type=RunType.HALT)
        )
        await repo.add_unit_to_run(
            TestRunUnit(test_run_id=run_id, unit_id=setup_data["unit_id"], slot_number=1)
        )
        await repo.record_unit_failure(
            UnitFailure(
                id=None,
                test_run_id=run_id,
                unit_id=setup_data["unit_id"],
                requirement_id=setup_data["req_id"],
                environmental_state_id=setup_data["state_id"],
                measured_value=3.8,
                bound_description="high > 3.5V",
            )
        )
        await repo.complete_test_run(run_id)

        outcomes = await repo.get_unit_outcomes(run_id)
        assert len(outcomes) == 1
        assert outcomes[0].outcome == TestOutcome.FAIL

    @pytest.mark.asyncio
    async def test_unit_outcomes_indeterminate_system_failure(self, db, setup_data) -> None:
        """Test that unit outcome is 'indeterminate' when system failure exists."""
        repo = TestRunRepository(db)

        run_id = await repo.create_test_run(
            TestRun(id=None, test_case_id=setup_data["test_case_id"], run_type=RunType.HALT)
        )
        await repo.add_unit_to_run(
            TestRunUnit(test_run_id=run_id, unit_id=setup_data["unit_id"], slot_number=1)
        )
        await repo.record_system_failure(
            SystemFailure(
                id=None,
                test_run_id=run_id,
                pareto_code="SYS-001",
                description="System error",
            )
        )
        await repo.terminate_test_run(run_id)

        outcomes = await repo.get_unit_outcomes(run_id)
        assert len(outcomes) == 1
        assert outcomes[0].outcome == TestOutcome.INDETERMINATE
