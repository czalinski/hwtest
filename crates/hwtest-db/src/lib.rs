//! Test results database (SQLite).
//!
//! The schema is `sql/test_results_schema.sql` at the repository root: unit
//! types, design revisions and units; test cases, states and requirements;
//! test runs with their units and failures; and the `test_run_unit_outcome`
//! view (pass / fail / indeterminate per unit and run).
//!
//! ```no_run
//! use hwtest_db::{Database, RunType};
//! let db = Database::open("results.db").unwrap();       // creates the schema if needed
//! let ut = db.create_unit_type("Widget", Some("Test widget")).unwrap();
//! let tc = db.create_test_case("voltage_echo", ut, None).unwrap();
//! let run = db.create_test_run(tc, RunType::Hass).unwrap();
//! db.complete_test_run(run).unwrap();
//! ```

use std::path::Path;

use rusqlite::{params, Connection, OptionalExtension, Row};
use serde::{Deserialize, Serialize};

/// The schema, as shipped in `sql/test_results_schema.sql`.
pub const SCHEMA_SQL: &str = include_str!("../../../sql/test_results_schema.sql");

/// Database error.
#[derive(Debug, thiserror::Error)]
pub enum DbError {
    /// SQLite error (constraint violations, I/O, ...).
    #[error("database: {0}")]
    Sqlite(#[from] rusqlite::Error),
    /// A stored value is not valid for its column's enum.
    #[error("invalid stored value {value:?} for {column}")]
    InvalidValue {
        /// Column.
        column: &'static str,
        /// Stored text.
        value: String,
    },
}

/// Convenience alias.
pub type Result<T> = std::result::Result<T, DbError>;

macro_rules! text_enum {
    ($(#[$m:meta])* $name:ident, $col:literal { $($(#[$vm:meta])* $v:ident = $s:literal),* $(,)? }) => {
        $(#[$m])*
        #[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
        #[serde(rename_all = "snake_case")]
        pub enum $name { $($(#[$vm])* $v),* }
        impl $name {
            /// Stored text.
            pub fn as_str(self) -> &'static str { match self { $($name::$v => $s),* } }
            /// Parses stored text.
            ///
            /// # Errors
            /// [`DbError::InvalidValue`] for unknown text.
            pub fn parse(s: &str) -> Result<Self> {
                match s { $($s => Ok($name::$v),)* _ => Err(DbError::InvalidValue { column: $col, value: s.to_string() }) }
            }
        }
    };
}

text_enum!(
    /// Kind of test run.
    RunType, "test_run.run_type" {
        /// Highly accelerated stress screening.
        Hass = "hass",
        /// Highly accelerated life test.
        Halt = "halt",
        /// Single functional pass.
        Functional = "functional",
        /// Manual / exploratory.
        AdHoc = "ad_hoc",
    }
);
text_enum!(
    /// Run lifecycle status.
    RunStatus, "test_run.status" {
        /// In progress.
        Running = "running",
        /// Finished normally.
        Completed = "completed",
        /// Stopped early.
        Terminated = "terminated",
    }
);
text_enum!(
    /// Where a requirement is checked.
    RequirementSource, "requirement.source" {
        /// Continuously, by a monitor.
        Monitor = "monitor",
        /// At a specific moment.
        PointCheck = "point_check",
    }
);
text_enum!(
    /// Per-unit outcome of a run.
    TestOutcome, "test_run_unit_outcome.outcome" {
        /// No failures, run completed.
        Pass = "pass",
        /// At least one unit failure.
        Fail = "fail",
        /// System failure or run not completed.
        Indeterminate = "indeterminate",
    }
);

/// A product type.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UnitType {
    /// Row id.
    pub id: i64,
    /// Unique name.
    pub name: String,
    /// Description.
    pub description: Option<String>,
}

/// A hardware revision of a unit type.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DesignRevision {
    /// Row id.
    pub id: i64,
    /// Unit type.
    pub unit_type_id: i64,
    /// Revision label.
    pub revision: String,
    /// Creation time (SQLite `CURRENT_TIMESTAMP`, UTC).
    pub created_at: String,
}

/// A physical unit.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Unit {
    /// Row id.
    pub id: i64,
    /// Unique serial number.
    pub serial_number: String,
    /// Design revision.
    pub design_revision_id: i64,
    /// Creation time.
    pub created_at: String,
}

/// A test case for a unit type.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TestCase {
    /// Row id.
    pub id: i64,
    /// Name (unique per unit type).
    pub name: String,
    /// Unit type.
    pub unit_type_id: i64,
    /// Description.
    pub description: Option<String>,
}

/// An environmental state of a test case.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EnvironmentalState {
    /// Row id.
    pub id: i64,
    /// Test case.
    pub test_case_id: i64,
    /// State name.
    pub name: String,
}

/// A requirement of a test case.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Requirement {
    /// Row id.
    pub id: i64,
    /// Test case.
    pub test_case_id: i64,
    /// Name.
    pub name: String,
    /// How it is checked.
    pub source: RequirementSource,
}

/// One execution of a test case.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TestRun {
    /// Row id.
    pub id: i64,
    /// Test case.
    pub test_case_id: i64,
    /// Run type.
    pub run_type: RunType,
    /// Start time.
    pub started_at: String,
    /// Finish time.
    pub finished_at: Option<String>,
    /// Status.
    pub status: RunStatus,
}

/// A unit in a slot of a run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TestRunUnit {
    /// Run.
    pub test_run_id: i64,
    /// Unit.
    pub unit_id: i64,
    /// Slot (1-based in the schema).
    pub slot_number: i64,
}

/// A test-system failure during a run (makes outcomes indeterminate).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SystemFailure {
    /// Row id.
    pub id: i64,
    /// Run.
    pub test_run_id: i64,
    /// When.
    pub occurred_at: String,
    /// Pareto category.
    pub pareto_code: String,
    /// Description.
    pub description: String,
}

/// A unit failing a requirement in a state.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UnitFailure {
    /// Row id.
    pub id: i64,
    /// Run.
    pub test_run_id: i64,
    /// Unit.
    pub unit_id: i64,
    /// Requirement.
    pub requirement_id: i64,
    /// State.
    pub environmental_state_id: i64,
    /// When.
    pub occurred_at: String,
    /// Measured value.
    pub measured_value: f64,
    /// Bound that was violated, as text.
    pub bound_description: String,
    /// Description.
    pub description: Option<String>,
}

/// Computed outcome of a unit in a run.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TestRunUnitOutcome {
    /// Run.
    pub test_run_id: i64,
    /// Unit.
    pub unit_id: i64,
    /// Slot.
    pub slot_number: i64,
    /// Outcome.
    pub outcome: TestOutcome,
}

/// A results database connection.
pub struct Database {
    conn: Connection,
}

fn run_from(row: &Row<'_>) -> rusqlite::Result<(i64, i64, String, String, Option<String>, String)> {
    Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?, row.get(5)?))
}

impl Database {
    /// Opens (creating the file and schema if needed) a database file.
    ///
    /// # Errors
    /// [`DbError::Sqlite`] if the file cannot be opened or the schema applied.
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        Self::init(Connection::open(path)?)
    }

    /// A fresh in-memory database with the schema.
    ///
    /// # Errors
    /// [`DbError::Sqlite`] if the schema cannot be applied.
    pub fn in_memory() -> Result<Self> {
        Self::init(Connection::open_in_memory()?)
    }

    fn init(conn: Connection) -> Result<Self> {
        conn.pragma_update(None, "foreign_keys", "ON")?;
        conn.busy_timeout(std::time::Duration::from_secs(5))?;
        let has_schema: bool = conn
            .query_row(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='test_run'",
                [],
                |_| Ok(true),
            )
            .optional()?
            .unwrap_or(false);
        if !has_schema {
            conn.execute_batch(SCHEMA_SQL)?;
        }
        Ok(Self { conn })
    }

    /// The underlying connection (for ad-hoc queries).
    pub fn connection(&self) -> &Connection {
        &self.conn
    }

    // --- units ---------------------------------------------------------

    /// Creates a unit type; returns its id.
    ///
    /// # Errors
    /// [`DbError::Sqlite`] (e.g. duplicate name).
    pub fn create_unit_type(&self, name: &str, description: Option<&str>) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO unit_type (name, description) VALUES (?1, ?2)",
            params![name, description],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Looks up a unit type by id.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn get_unit_type(&self, id: i64) -> Result<Option<UnitType>> {
        self.unit_type_where("id = ?1", params![id])
    }

    /// Looks up a unit type by name.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn get_unit_type_by_name(&self, name: &str) -> Result<Option<UnitType>> {
        self.unit_type_where("name = ?1", params![name])
    }

    fn unit_type_where(&self, cond: &str, p: impl rusqlite::Params) -> Result<Option<UnitType>> {
        Ok(self
            .conn
            .query_row(
                &format!("SELECT id, name, description FROM unit_type WHERE {cond}"),
                p,
                |r| Ok(UnitType { id: r.get(0)?, name: r.get(1)?, description: r.get(2)? }),
            )
            .optional()?)
    }

    /// All unit types by name.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn list_unit_types(&self) -> Result<Vec<UnitType>> {
        let mut st =
            self.conn.prepare("SELECT id, name, description FROM unit_type ORDER BY name")?;
        let rows = st.query_map([], |r| {
            Ok(UnitType { id: r.get(0)?, name: r.get(1)?, description: r.get(2)? })
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// Creates a design revision; returns its id.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn create_design_revision(&self, unit_type_id: i64, revision: &str) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO design_revision (unit_type_id, revision) VALUES (?1, ?2)",
            params![unit_type_id, revision],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Looks up a revision by unit type and label.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn get_design_revision_by_name(
        &self,
        unit_type_id: i64,
        revision: &str,
    ) -> Result<Option<DesignRevision>> {
        Ok(self
            .conn
            .query_row(
                "SELECT id, unit_type_id, revision, created_at FROM design_revision WHERE unit_type_id = ?1 AND revision = ?2",
                params![unit_type_id, revision],
                |r| Ok(DesignRevision { id: r.get(0)?, unit_type_id: r.get(1)?, revision: r.get(2)?, created_at: r.get(3)? }),
            )
            .optional()?)
    }

    /// Revisions of a unit type, oldest first.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn list_design_revisions(&self, unit_type_id: i64) -> Result<Vec<DesignRevision>> {
        let mut st = self.conn.prepare(
            "SELECT id, unit_type_id, revision, created_at FROM design_revision WHERE unit_type_id = ?1 ORDER BY created_at, id",
        )?;
        let rows = st.query_map(params![unit_type_id], |r| {
            Ok(DesignRevision {
                id: r.get(0)?,
                unit_type_id: r.get(1)?,
                revision: r.get(2)?,
                created_at: r.get(3)?,
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// Creates a unit; returns its id.
    ///
    /// # Errors
    /// [`DbError::Sqlite`] (e.g. duplicate serial).
    pub fn create_unit(&self, serial_number: &str, design_revision_id: i64) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO unit (serial_number, design_revision_id) VALUES (?1, ?2)",
            params![serial_number, design_revision_id],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Looks up a unit by serial number.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn get_unit_by_serial(&self, serial_number: &str) -> Result<Option<Unit>> {
        Ok(self
            .conn
            .query_row(
                "SELECT id, serial_number, design_revision_id, created_at FROM unit WHERE serial_number = ?1",
                params![serial_number],
                |r| Ok(Unit { id: r.get(0)?, serial_number: r.get(1)?, design_revision_id: r.get(2)?, created_at: r.get(3)? }),
            )
            .optional()?)
    }

    // --- test cases ----------------------------------------------------

    /// Creates a test case; returns its id.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn create_test_case(
        &self,
        name: &str,
        unit_type_id: i64,
        description: Option<&str>,
    ) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO test_case (name, unit_type_id, description) VALUES (?1, ?2, ?3)",
            params![name, unit_type_id, description],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Looks up a test case by unit type and name.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn get_test_case_by_name(&self, unit_type_id: i64, name: &str) -> Result<Option<TestCase>> {
        Ok(self
            .conn
            .query_row(
                "SELECT id, name, unit_type_id, description FROM test_case WHERE unit_type_id = ?1 AND name = ?2",
                params![unit_type_id, name],
                |r| Ok(TestCase { id: r.get(0)?, name: r.get(1)?, unit_type_id: r.get(2)?, description: r.get(3)? }),
            )
            .optional()?)
    }

    /// Returns the id of a test case, creating unit type and test case as needed.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn ensure_test_case(
        &self,
        unit_type: &str,
        name: &str,
        description: Option<&str>,
    ) -> Result<i64> {
        let ut = match self.get_unit_type_by_name(unit_type)? {
            Some(u) => u.id,
            None => self.create_unit_type(unit_type, None)?,
        };
        match self.get_test_case_by_name(ut, name)? {
            Some(tc) => Ok(tc.id),
            None => self.create_test_case(name, ut, description),
        }
    }

    /// Creates an environmental state; returns its id.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn create_environmental_state(&self, test_case_id: i64, name: &str) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO environmental_state (test_case_id, name) VALUES (?1, ?2)",
            params![test_case_id, name],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// States of a test case.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn list_environmental_states(&self, test_case_id: i64) -> Result<Vec<EnvironmentalState>> {
        let mut st = self.conn.prepare("SELECT id, test_case_id, name FROM environmental_state WHERE test_case_id = ?1 ORDER BY id")?;
        let rows = st.query_map(params![test_case_id], |r| {
            Ok(EnvironmentalState { id: r.get(0)?, test_case_id: r.get(1)?, name: r.get(2)? })
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// Creates a requirement; returns its id.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn create_requirement(
        &self,
        test_case_id: i64,
        name: &str,
        source: RequirementSource,
    ) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO requirement (test_case_id, name, source) VALUES (?1, ?2, ?3)",
            params![test_case_id, name, source.as_str()],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Requirements of a test case.
    ///
    /// # Errors
    /// [`DbError::Sqlite`] or [`DbError::InvalidValue`].
    pub fn list_requirements(&self, test_case_id: i64) -> Result<Vec<Requirement>> {
        let mut st = self.conn.prepare("SELECT id, test_case_id, name, source FROM requirement WHERE test_case_id = ?1 ORDER BY id")?;
        let rows = st.query_map(params![test_case_id], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, i64>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, String>(3)?,
            ))
        })?;
        rows.map(|row| {
            let (id, test_case_id, name, source) = row?;
            Ok(Requirement { id, test_case_id, name, source: RequirementSource::parse(&source)? })
        })
        .collect()
    }

    // --- runs ----------------------------------------------------------

    /// Starts a test run (status `running`); returns its id.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn create_test_run(&self, test_case_id: i64, run_type: RunType) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO test_run (test_case_id, run_type) VALUES (?1, ?2)",
            params![test_case_id, run_type.as_str()],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// Looks up a run.
    ///
    /// # Errors
    /// [`DbError::Sqlite`] or [`DbError::InvalidValue`].
    pub fn get_test_run(&self, id: i64) -> Result<Option<TestRun>> {
        let row = self
            .conn
            .query_row(
                "SELECT id, test_case_id, run_type, started_at, finished_at, status FROM test_run WHERE id = ?1",
                params![id],
                run_from,
            )
            .optional()?;
        row.map(Self::to_run).transpose()
    }

    fn to_run(
        (id, test_case_id, run_type, started_at, finished_at, status): (
            i64,
            i64,
            String,
            String,
            Option<String>,
            String,
        ),
    ) -> Result<TestRun> {
        Ok(TestRun {
            id,
            test_case_id,
            run_type: RunType::parse(&run_type)?,
            started_at,
            finished_at,
            status: RunStatus::parse(&status)?,
        })
    }

    /// Sets a run's status (and finish time for completed/terminated).
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn update_test_run_status(&self, id: i64, status: RunStatus) -> Result<()> {
        if status == RunStatus::Running {
            self.conn.execute(
                "UPDATE test_run SET status = ?1, finished_at = NULL WHERE id = ?2",
                params![status.as_str(), id],
            )?;
        } else {
            self.conn.execute(
                "UPDATE test_run SET status = ?1, finished_at = CURRENT_TIMESTAMP WHERE id = ?2",
                params![status.as_str(), id],
            )?;
        }
        Ok(())
    }

    /// Marks a run completed.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn complete_test_run(&self, id: i64) -> Result<()> {
        self.update_test_run_status(id, RunStatus::Completed)
    }

    /// Marks a run terminated.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn terminate_test_run(&self, id: i64) -> Result<()> {
        self.update_test_run_status(id, RunStatus::Terminated)
    }

    /// Most recent runs, optionally for one test case.
    ///
    /// # Errors
    /// [`DbError::Sqlite`] or [`DbError::InvalidValue`].
    pub fn list_test_runs(&self, test_case_id: Option<i64>, limit: i64) -> Result<Vec<TestRun>> {
        let mut st = self.conn.prepare(
            "SELECT id, test_case_id, run_type, started_at, finished_at, status FROM test_run \
             WHERE (?1 IS NULL OR test_case_id = ?1) ORDER BY started_at DESC, id DESC LIMIT ?2",
        )?;
        let rows = st.query_map(params![test_case_id, limit], run_from)?;
        rows.map(|r| Self::to_run(r?)).collect()
    }

    /// Puts a unit in a slot of a run.
    ///
    /// # Errors
    /// [`DbError::Sqlite`] (slot must be >= 1; unit/slot unique per run).
    pub fn add_unit_to_run(&self, test_run_id: i64, unit_id: i64, slot_number: i64) -> Result<()> {
        self.conn.execute(
            "INSERT INTO test_run_unit (test_run_id, unit_id, slot_number) VALUES (?1, ?2, ?3)",
            params![test_run_id, unit_id, slot_number],
        )?;
        Ok(())
    }

    /// Units in a run by slot.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn list_units_in_run(&self, test_run_id: i64) -> Result<Vec<TestRunUnit>> {
        let mut st = self.conn.prepare("SELECT test_run_id, unit_id, slot_number FROM test_run_unit WHERE test_run_id = ?1 ORDER BY slot_number")?;
        let rows = st.query_map(params![test_run_id], |r| {
            Ok(TestRunUnit { test_run_id: r.get(0)?, unit_id: r.get(1)?, slot_number: r.get(2)? })
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// Records a system failure; returns its id.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn record_system_failure(
        &self,
        test_run_id: i64,
        pareto_code: &str,
        description: &str,
    ) -> Result<i64> {
        self.conn.execute(
            "INSERT INTO system_failure (test_run_id, pareto_code, description) VALUES (?1, ?2, ?3)",
            params![test_run_id, pareto_code, description],
        )?;
        Ok(self.conn.last_insert_rowid())
    }

    /// System failures of a run.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn list_system_failures(&self, test_run_id: i64) -> Result<Vec<SystemFailure>> {
        let mut st = self.conn.prepare(
            "SELECT id, test_run_id, occurred_at, pareto_code, description FROM system_failure WHERE test_run_id = ?1 ORDER BY occurred_at, id",
        )?;
        let rows = st.query_map(params![test_run_id], |r| {
            Ok(SystemFailure {
                id: r.get(0)?,
                test_run_id: r.get(1)?,
                occurred_at: r.get(2)?,
                pareto_code: r.get(3)?,
                description: r.get(4)?,
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// Records the first failure of a requirement for a unit in a state.
    /// Returns `None` if that failure was already recorded.
    ///
    /// # Errors
    /// [`DbError::Sqlite`] for errors other than the duplicate.
    #[allow(clippy::too_many_arguments)]
    pub fn record_unit_failure(
        &self,
        test_run_id: i64,
        unit_id: i64,
        requirement_id: i64,
        environmental_state_id: i64,
        measured_value: f64,
        bound_description: &str,
        description: Option<&str>,
    ) -> Result<Option<i64>> {
        let n = self.conn.execute(
            "INSERT OR IGNORE INTO unit_failure (test_run_id, unit_id, requirement_id, environmental_state_id, \
             measured_value, bound_description, description) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![test_run_id, unit_id, requirement_id, environmental_state_id, measured_value, bound_description, description],
        )?;
        Ok((n == 1).then(|| self.conn.last_insert_rowid()))
    }

    /// Unit failures of a run, optionally for one unit.
    ///
    /// # Errors
    /// [`DbError::Sqlite`].
    pub fn list_unit_failures(
        &self,
        test_run_id: i64,
        unit_id: Option<i64>,
    ) -> Result<Vec<UnitFailure>> {
        let mut st = self.conn.prepare(
            "SELECT id, test_run_id, unit_id, requirement_id, environmental_state_id, occurred_at, measured_value, \
             bound_description, description FROM unit_failure WHERE test_run_id = ?1 AND (?2 IS NULL OR unit_id = ?2) \
             ORDER BY occurred_at, id",
        )?;
        let rows = st.query_map(params![test_run_id, unit_id], |r| {
            Ok(UnitFailure {
                id: r.get(0)?,
                test_run_id: r.get(1)?,
                unit_id: r.get(2)?,
                requirement_id: r.get(3)?,
                environmental_state_id: r.get(4)?,
                occurred_at: r.get(5)?,
                measured_value: r.get(6)?,
                bound_description: r.get(7)?,
                description: r.get(8)?,
            })
        })?;
        Ok(rows.collect::<rusqlite::Result<_>>()?)
    }

    /// Outcome per unit of a run.
    ///
    /// # Errors
    /// [`DbError::Sqlite`] or [`DbError::InvalidValue`].
    pub fn get_unit_outcomes(&self, test_run_id: i64) -> Result<Vec<TestRunUnitOutcome>> {
        let mut st = self.conn.prepare(
            "SELECT test_run_id, unit_id, slot_number, outcome FROM test_run_unit_outcome WHERE test_run_id = ?1 ORDER BY slot_number",
        )?;
        let rows = st.query_map(params![test_run_id], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, i64>(1)?,
                r.get::<_, i64>(2)?,
                r.get::<_, String>(3)?,
            ))
        })?;
        rows.map(|row| {
            let (test_run_id, unit_id, slot_number, outcome) = row?;
            Ok(TestRunUnitOutcome {
                test_run_id,
                unit_id,
                slot_number,
                outcome: TestOutcome::parse(&outcome)?,
            })
        })
        .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn setup() -> (Database, i64, i64, i64, i64) {
        let db = Database::in_memory().unwrap();
        let ut = db.create_unit_type("Widget", Some("Test widget")).unwrap();
        let rev = db.create_design_revision(ut, "A").unwrap();
        let u1 = db.create_unit("SN1", rev).unwrap();
        let u2 = db.create_unit("SN2", rev).unwrap();
        (db, ut, rev, u1, u2)
    }

    #[test]
    fn outcomes_follow_the_view_rules() {
        let (db, ut, _, u1, u2) = setup();
        let tc = db.create_test_case("echo", ut, None).unwrap();
        let st = db.create_environmental_state(tc, "hot").unwrap();
        let req = db.create_requirement(tc, "echo_voltage", RequirementSource::Monitor).unwrap();
        let run = db.create_test_run(tc, RunType::Hass).unwrap();
        db.add_unit_to_run(run, u1, 1).unwrap();
        db.add_unit_to_run(run, u2, 2).unwrap();
        // Running -> indeterminate.
        assert!(db
            .get_unit_outcomes(run)
            .unwrap()
            .iter()
            .all(|o| o.outcome == TestOutcome::Indeterminate));
        assert!(db
            .record_unit_failure(run, u1, req, st, 4.4, "within_range [4.0, 0.3]", None)
            .unwrap()
            .is_some());
        assert!(
            db.record_unit_failure(run, u1, req, st, 4.5, "within_range [4.0, 0.3]", None)
                .unwrap()
                .is_none(),
            "first failure only"
        );
        db.complete_test_run(run).unwrap();
        let outcomes: Vec<_> = db
            .get_unit_outcomes(run)
            .unwrap()
            .into_iter()
            .map(|o| (o.slot_number, o.outcome))
            .collect();
        assert_eq!(outcomes, vec![(1, TestOutcome::Fail), (2, TestOutcome::Pass)]);
        db.record_system_failure(run, "RACK_DAQ", "MCC 118 timeout").unwrap();
        assert_eq!(db.get_unit_outcomes(run).unwrap()[1].outcome, TestOutcome::Indeterminate);
        let r = db.get_test_run(run).unwrap().unwrap();
        assert_eq!((r.status, r.run_type), (RunStatus::Completed, RunType::Hass));
        assert!(r.finished_at.is_some());
        assert_eq!(db.list_unit_failures(run, Some(u1)).unwrap()[0].measured_value, 4.4);
    }

    #[test]
    fn constraints_enforced() {
        let (db, ut, _, u1, _) = setup();
        assert!(db.create_unit_type("Widget", None).is_err());
        let tc = db.create_test_case("echo", ut, None).unwrap();
        let run = db.create_test_run(tc, RunType::Functional).unwrap();
        assert!(db.add_unit_to_run(run, u1, 0).is_err(), "slot numbers start at 1");
        assert!(db.create_test_run(999, RunType::Halt).is_err(), "foreign keys on");
    }

    #[test]
    fn lookups_and_reopen() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("r.db");
        let tc = {
            let db = Database::open(&path).unwrap();
            db.ensure_test_case("Widget", "echo", Some("d")).unwrap()
        };
        let db = Database::open(&path).unwrap(); // schema not re-applied
        assert_eq!(db.ensure_test_case("Widget", "echo", None).unwrap(), tc);
        let run = db.create_test_run(tc, RunType::AdHoc).unwrap();
        db.terminate_test_run(run).unwrap();
        let runs = db.list_test_runs(Some(tc), 10).unwrap();
        assert_eq!((runs.len(), runs[0].status), (1, RunStatus::Terminated));
        assert_eq!(db.list_test_runs(None, 10).unwrap().len(), 1);
        assert_eq!(db.list_unit_types().unwrap()[0].name, "Widget");
        let ut = db.get_unit_type_by_name("Widget").unwrap().unwrap().id;
        let rev = db.create_design_revision(ut, "B").unwrap();
        db.create_unit("SN9", rev).unwrap();
        assert_eq!(db.get_unit_by_serial("SN9").unwrap().unwrap().design_revision_id, rev);
        assert_eq!(db.get_design_revision_by_name(ut, "B").unwrap().unwrap().id, rev);
        assert_eq!(db.list_design_revisions(ut).unwrap().len(), 1);
        let req = db.create_requirement(tc, "r", RequirementSource::PointCheck).unwrap();
        assert_eq!(db.list_requirements(tc).unwrap()[0].id, req);
        assert_eq!(db.list_environmental_states(tc).unwrap(), [] as [EnvironmentalState; 0]);
    }
}
