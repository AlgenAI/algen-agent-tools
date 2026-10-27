"""Typed SQLite actions over Python's own ``sqlite3``: named, parameterised queries a developer
writes once, the same way as for PostgreSQL.

The file opens read-only unless the toolkit is built with ``read_only=False``. Reads also run
with ``PRAGMA query_only``. SQLite has no statement timeout, so a progress handler interrupts a
statement that runs past the tool's timeout.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from algen_agent_runtime.exceptions.errors import ConfigurationError, ToolExecutionError
from algen_agent_runtime.tools.authoring import read

from algen_agent_tools._env import SecretResolver, env_names
from algen_agent_tools.sql.core import (
    DEFAULT_MAX_ROWS,
    Dialect,
    SqlToolkit,
    TableSummary,
    TooManyRows,
    schema_argument,
    strict_model,
    table_argument,
    too_many_rows,
)

DEFAULT_PATH = "env://ALGEN_TOOLS_SQLITE_PATH"

_NUMBERED = re.compile(r"\?\d|[:@$][A-Za-z_]")
# Interrupt checks run every this many SQLite virtual-machine steps.
_PROGRESS_STEPS = 1_000

_LIST_TABLES = """
SELECT 'main' AS schema_name, name, type AS kind, NULL AS estimated_rows
FROM sqlite_schema
WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'
ORDER BY name
"""

_DESCRIBE_TABLE = """
SELECT name, type AS data_type, "notnull" = 0 AS nullable, dflt_value AS "default",
       pk > 0 AS primary_key
FROM pragma_table_info(?)
ORDER BY cid
"""


class SqliteDatabase:
    """One SQLite connection, used by one statement at a time from a worker thread."""

    def __init__(self, path: str, *, read_only: bool) -> None:
        mode = "ro" if read_only else "rw"  # rw never creates a missing file
        self._connection = sqlite3.connect(
            f"file:{quote(str(Path(path)))}?mode={mode}",
            uri=True,
            check_same_thread=False,
            isolation_level=None,  # the dialect issues BEGIN, COMMIT and ROLLBACK itself
        )
        self._connection.row_factory = sqlite3.Row
        self._lock = asyncio.Lock()

    async def run[T](self, work: Callable[[sqlite3.Connection], T]) -> T:
        async with self._lock:
            return await asyncio.to_thread(work, self._connection)

    async def close(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._connection.close)


async def open_database(secrets: SecretResolver, *, path: str, read_only: bool) -> SqliteDatabase:
    """Resolve the path, then open the existing database file; a missing file is an error."""
    resolved = await secrets(path)
    try:
        return await asyncio.to_thread(SqliteDatabase, resolved, read_only=read_only)
    except sqlite3.Error as exc:
        raise ConfigurationError(f"could not open the SQLite database ({exc})") from exc


def _deadline_handler(timeout_seconds: float) -> Callable[[], int]:
    deadline = time.monotonic() + timeout_seconds
    return lambda: 1 if time.monotonic() > deadline else 0


def _run[T](
    connection: sqlite3.Connection,
    timeout_seconds: float,
    label: str,
    body: Callable[[sqlite3.Connection], T],
) -> T:
    connection.set_progress_handler(_deadline_handler(timeout_seconds), _PROGRESS_STEPS)
    try:
        return body(connection)
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc):
            raise ToolExecutionError(
                f"query {label!r} timed out after {timeout_seconds}s"
            ) from None
        raise ToolExecutionError(f"query {label!r} failed ({type(exc).__name__})") from exc
    except sqlite3.Error as exc:
        raise ToolExecutionError(f"query {label!r} failed ({type(exc).__name__})") from exc
    finally:
        connection.set_progress_handler(None, 0)


class SqliteDialect(Dialect):
    """``?`` placeholders on a :class:`SqliteDatabase`."""

    def placeholder_problem(self, sql: str, expected: int) -> str | None:
        if _NUMBERED.search(sql):
            return "uses numbered or named placeholders (use plain ?)"
        found = sql.count("?")
        return f"uses {found} placeholder(s)" if found != expected else None

    async def read(
        self,
        client: SqliteDatabase,
        sql: str,
        values: Sequence[Any],
        *,
        timeout_seconds: float,
        limit: int,
        label: str,
    ) -> list[Mapping[str, Any]]:
        def body(connection: sqlite3.Connection) -> list[Mapping[str, Any]]:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            try:
                rows: list[Mapping[str, Any]] = []
                for record in connection.execute(sql, tuple(values)):
                    if len(rows) == limit:
                        raise too_many_rows(label, limit)
                    rows.append(dict(record))
                return rows
            finally:
                connection.execute("ROLLBACK")
                connection.execute("PRAGMA query_only = OFF")

        return await client.run(lambda connection: _run(connection, timeout_seconds, label, body))

    async def write(
        self,
        client: SqliteDatabase,
        sql: str,
        values: Sequence[Any],
        *,
        timeout_seconds: float,
        returning: bool,
        limit: int,
        label: str,
    ) -> list[Mapping[str, Any]] | int:
        def body(connection: sqlite3.Connection) -> list[Mapping[str, Any]] | int:
            connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = connection.execute(sql, tuple(values))
                result: list[Mapping[str, Any]] | int
                if returning:
                    result = [dict(record) for record in cursor.fetchmany(limit + 1)]
                    if len(result) > limit:
                        raise too_many_rows(label, limit, rolled_back=True)
                else:
                    result = max(cursor.rowcount, 0)
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")
            return result

        return await client.run(lambda connection: _run(connection, timeout_seconds, label, body))


class SqliteToolkit(SqlToolkit):
    """A toolkit of named, typed queries against one SQLite database file.

    ``path`` is an ``env://`` reference to an existing file. With ``read_only`` (the default)
    the file opens read-only and write queries cannot be declared. Queries use ``?``.
    """

    def __init__(
        self,
        name: str = "sqlite",
        *,
        path: str = DEFAULT_PATH,
        read_only: bool = True,
        max_rows: int = DEFAULT_MAX_ROWS,
        permission_prefix: str | None = None,
        client: Any = None,
    ) -> None:
        async def open_client(secrets: SecretResolver) -> SqliteDatabase:
            return await open_database(secrets, path=path, read_only=read_only)

        super().__init__(
            name,
            dialect=SqliteDialect(),
            max_rows=max_rows,
            permission_prefix=permission_prefix,
            env=env_names(path),
            client=client or open_client,
        )
        self.read_only = read_only

    def check_write_allowed(self, name: str) -> None:
        if self.read_only:
            raise ConfigurationError(
                f"write query {name!r} needs a toolkit built with read_only=False"
            )


def sqlite_toolkit(
    name: str = "sqlite",
    *,
    path: str = DEFAULT_PATH,
    read_only: bool = True,
    max_rows: int = DEFAULT_MAX_ROWS,
    client: Any = None,
) -> SqliteToolkit:
    """A :class:`SqliteToolkit` with two catalog reads: ``<name>.list_tables`` and
    ``<name>.describe_table``.

    Add the database's own typed queries with :meth:`SqliteToolkit.query`.
    """
    toolkit = SqliteToolkit(name, path=path, read_only=read_only, max_rows=max_rows, client=client)
    toolkit.query(
        "list_tables",
        _LIST_TABLES,
        description="List the tables and views in the database.",
        row=TableSummary,
        preset=read(),
    )
    toolkit.add_describe_table(
        _DESCRIBE_TABLE,
        arguments=strict_model(
            "describe_table_arguments",
            schema_name=schema_argument(("main",)),
            table=table_argument(128),
        ),
        values=lambda arguments: [getattr(arguments, "table")],  # noqa: B009
    )
    return toolkit


__all__ = [
    "DEFAULT_PATH",
    "SqliteDatabase",
    "SqliteDialect",
    "SqliteToolkit",
    "TooManyRows",
    "open_database",
    "sqlite_toolkit",
]
