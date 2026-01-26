-- Test results database schema.
--
-- Tracks unit types, individual units, test cases, test runs,
-- and structured failure data for both system and unit failures.

-- Unit types and their design revisions.

CREATE TABLE unit_type (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE design_revision (
    id           INTEGER PRIMARY KEY,
    unit_type_id INTEGER NOT NULL REFERENCES unit_type(id),
    revision     TEXT NOT NULL,
    created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (unit_type_id, revision)
);

-- Individual units identified by serial number, each built to a specific revision.

CREATE TABLE unit (
    id                 INTEGER PRIMARY KEY,
    serial_number      TEXT NOT NULL UNIQUE,
    design_revision_id INTEGER NOT NULL REFERENCES design_revision(id),
    created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Test case definitions. Each test case targets a single unit type.

CREATE TABLE test_case (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    unit_type_id INTEGER NOT NULL REFERENCES unit_type(id),
    description  TEXT,
    UNIQUE (name, unit_type_id)
);

-- Which design revisions a test case applies to.
-- If no rows exist for a test_case, it applies to ALL revisions of that unit type.
CREATE TABLE test_case_revision (
    test_case_id       INTEGER NOT NULL REFERENCES test_case(id),
    design_revision_id INTEGER NOT NULL REFERENCES design_revision(id),
    PRIMARY KEY (test_case_id, design_revision_id)
);

-- Environmental states defined per test case.
-- If none are specified, the single state "ambient" is assumed.

CREATE TABLE environmental_state (
    id           INTEGER PRIMARY KEY,
    test_case_id INTEGER NOT NULL REFERENCES test_case(id),
    name         TEXT NOT NULL,
    UNIQUE (test_case_id, name)
);

-- Requirements defined per test case. Source is either a continuously
-- evaluated monitor or an instantaneous point-check.

CREATE TABLE requirement (
    id           INTEGER PRIMARY KEY,
    test_case_id INTEGER NOT NULL REFERENCES test_case(id),
    name         TEXT NOT NULL,
    source       TEXT NOT NULL CHECK (source IN ('monitor', 'point_check')),
    UNIQUE (test_case_id, name)
);

-- Which environmental states a point-check requirement applies to.
-- Used by the test case runtime to decide whether to execute a point-check
-- in the current state. Monitors are evaluated continuously and do not
-- need this mapping.
CREATE TABLE requirement_state (
    requirement_id         INTEGER NOT NULL REFERENCES requirement(id),
    environmental_state_id INTEGER NOT NULL REFERENCES environmental_state(id),
    PRIMARY KEY (requirement_id, environmental_state_id)
);

-- Test runs. Status values:
--   'running'    - in progress
--   'completed'  - ran to completion normally
--   'terminated' - ended early due to system failure

CREATE TABLE test_run (
    id           INTEGER PRIMARY KEY,
    test_case_id INTEGER NOT NULL REFERENCES test_case(id),
    started_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at  TIMESTAMP,
    status       TEXT NOT NULL DEFAULT 'running'
);

CREATE INDEX idx_test_run_test_case ON test_run(test_case_id);

-- Units placed in the test fixture for a given run. Slot numbers are
-- physical positions (1..N) and need not be contiguous.

CREATE TABLE test_run_unit (
    test_run_id INTEGER NOT NULL REFERENCES test_run(id),
    unit_id     INTEGER NOT NULL REFERENCES unit(id),
    slot_number INTEGER NOT NULL CHECK (slot_number >= 1),
    PRIMARY KEY (test_run_id, slot_number),
    UNIQUE (test_run_id, unit_id)
);

CREATE INDEX idx_test_run_unit_unit ON test_run_unit(unit_id);

-- System failures: errors with the test system itself. Any system failure
-- terminates the test run. The pareto_code is a GUID hard-coded at the
-- error site in source code, used for pareto analysis of systemic issues.

CREATE TABLE system_failure (
    id          INTEGER PRIMARY KEY,
    test_run_id INTEGER NOT NULL REFERENCES test_run(id),
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    pareto_code TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE INDEX idx_system_failure_run ON system_failure(test_run_id);
CREATE INDEX idx_system_failure_pareto ON system_failure(pareto_code);

-- Unit failures: a specific requirement violated for a specific unit.
-- Only the first occurrence per (run, unit, requirement, state) is recorded.

CREATE TABLE unit_failure (
    id                     INTEGER PRIMARY KEY,
    test_run_id            INTEGER NOT NULL,
    unit_id                INTEGER NOT NULL,
    requirement_id         INTEGER NOT NULL REFERENCES requirement(id),
    environmental_state_id INTEGER NOT NULL REFERENCES environmental_state(id),
    occurred_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    measured_value         REAL NOT NULL,
    bound_description      TEXT NOT NULL,
    description            TEXT,
    FOREIGN KEY (test_run_id, unit_id) REFERENCES test_run_unit(test_run_id, unit_id),
    UNIQUE (test_run_id, unit_id, requirement_id, environmental_state_id)
);

CREATE INDEX idx_unit_failure_run ON unit_failure(test_run_id);
CREATE INDEX idx_unit_failure_unit ON unit_failure(unit_id);
CREATE INDEX idx_unit_failure_req ON unit_failure(requirement_id);

-- Per-unit outcome derived from failure data and run status.
-- Outcome logic:
--   'fail'          - unit had any unit failures
--   'indeterminate' - no unit failures but system failures or run not completed
--   'pass'          - no failures of any kind and run completed

CREATE VIEW test_run_unit_outcome AS
SELECT
    tru.test_run_id,
    tru.unit_id,
    tru.slot_number,
    CASE
        WHEN COUNT(uf.id) > 0 THEN 'fail'
        WHEN COUNT(sf.id) > 0 THEN 'indeterminate'
        WHEN tr.status != 'completed' THEN 'indeterminate'
        ELSE 'pass'
    END AS outcome
FROM test_run_unit tru
JOIN test_run tr ON tr.id = tru.test_run_id
LEFT JOIN unit_failure uf
    ON uf.test_run_id = tru.test_run_id AND uf.unit_id = tru.unit_id
LEFT JOIN system_failure sf
    ON sf.test_run_id = tru.test_run_id
GROUP BY tru.test_run_id, tru.unit_id, tru.slot_number, tr.status;
