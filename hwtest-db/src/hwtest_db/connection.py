"""Database connection management."""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


async def get_schema_sql() -> str:
    """Load the database schema SQL from package resources."""
    schema_path = importlib.resources.files("hwtest_db.schema").joinpath("test_results_schema.sql")
    return schema_path.read_text(encoding="utf-8")


async def create_database(db_path: str | Path) -> None:
    """Create a new database with the test results schema.

    Args:
        db_path: Path to the SQLite database file. Use ":memory:" for in-memory.

    Raises:
        aiosqlite.Error: If database creation fails.
    """
    schema_sql = await get_schema_sql()

    async with aiosqlite.connect(db_path) as db:
        # Enable foreign keys
        await db.execute("PRAGMA foreign_keys = ON")
        # Execute the schema
        await db.executescript(schema_sql)
        await db.commit()


async def open_database(db_path: str | Path) -> aiosqlite.Connection:
    """Open an existing database connection.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        An open database connection. Caller is responsible for closing.

    Note:
        Foreign keys are enabled automatically.
    """
    db = await aiosqlite.connect(db_path)
    await db.execute("PRAGMA foreign_keys = ON")
    # Return rows as sqlite3.Row for dict-like access
    db.row_factory = aiosqlite.Row
    return db


class Database:
    """Async context manager for database connections.

    Usage:
        async with Database("test_results.db") as db:
            # Use db connection
            pass

        # Or for in-memory testing:
        async with Database(":memory:", create=True) as db:
            # Fresh database with schema
            pass
    """

    def __init__(self, db_path: str | Path, *, create: bool = False) -> None:
        """Initialize database connection manager.

        Args:
            db_path: Path to SQLite database file, or ":memory:" for in-memory.
            create: If True, create the database with schema on the connection.
        """
        self._db_path = db_path
        self._create = create
        self._connection: aiosqlite.Connection | None = None

    async def __aenter__(self) -> aiosqlite.Connection:
        """Open the database connection."""
        self._connection = await aiosqlite.connect(self._db_path)
        await self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.row_factory = aiosqlite.Row

        if self._create:
            schema_sql = await get_schema_sql()
            await self._connection.executescript(schema_sql)
            await self._connection.commit()

        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Close the database connection."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
