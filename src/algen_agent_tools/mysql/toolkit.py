"""Typed MySQL and MariaDB actions: named, parameterised queries a developer writes once, the
same way as for PostgreSQL.

Reads run in ``START TRANSACTION READ ONLY``. The server stops a statement at the tool's
timeout where it can (MySQL ``max_execution_time`` for reads, MariaDB ``max_statement_time``
for every statement), lock waits are capped to the timeout, and a client-side deadline drops
the connection, which rolls back an open transaction.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from functools import partial
from typing import Any

from algen_agent_runtime.exceptions.errors import ToolExecutionError
from algen_agent_runtime.tools.authoring import read

from algen_agent_tools._env import env_names
from algen_agent_tools.mysql.client import (
    MysqlConnection,
    MysqlCursor,
    MysqlDatabase,
    open_database,
)
from algen_agent_tools.sql.core import (
    DEFAULT_MAX_ROWS,
    Dialect,
    SqlToolkit,
    TableSummary,
    TooManyRows,
    strict_model,
    table_argument,
    too_many_rows,
)

DEFAULT_DSN = "env://ALGEN_TOOLS_MYSQL_DSN"

_PERCENT = re.compile(r"%(\([^)]*\)s|.?)", re.S)
# Server errors that mean a statement ran out of time: MySQL's max_execution_time, MariaDB's
# max_statement_time, and a lock wait past innodb_lock_wait_timeout.
_TIMEOUT_ERRORS = frozenset({3024, 1969, 1205})
# Extra time the client allows past the tool's timeout, so the server's own limit reports first.
_GRACE_SECONDS = 1.0

_LIST_TABLES = """
SELECT TABLE_SCHEMA AS schema_name,
       TABLE_NAME AS name,
       CASE TABLE_TYPE WHEN 'VIEW' THEN 'view' ELSE 'table' END AS kind,
       TABLE_ROWS AS estimated_rows
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
ORDER BY TABLE_NAME
"""

_DESCRIBE_TABLE = """
SELECT TABLE_SCHEMA AS schema_name,
       COLUMN_NAME AS name,
       COLUMN_TYPE AS data_type,
       IS_NULLABLE = 'YES' AS nullable,
       COLUMN_DEFAULT AS `default`,
       COLUMN_KEY = 'PRI' AS primary_key
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
ORDER BY ORDINAL_POSITION
"""


def _error_code(exc: BaseException) -> int | None:
    args = getattr(exc, "args", ())
    return args[0] if args and isinstance(args[0], int) else None


async def _limit_session(cursor: MysqlCursor, mariadb: bool, timeout_seconds: float) -> None:
    # Numbers built here, never caller text, so formatting them into the statement is safe.
    if mariadb:
        await cursor.execute(f"SET SESSION max_statement_time = {max(0.001, timeout_seconds)}")
    else:
        milliseconds = max(1, int(timeout_seconds * 1000))
        await cursor.execute(f"SET SESSION max_execution_time = {milliseconds}")
    await cursor.execute(
        f"SET SESSION innodb_lock_wait_timeout = {max(1, math.ceil(timeout_seconds))}"
    )


class MysqlDialect(Dialect):
    """``%s`` placeholders (a literal ``%`` is ``%%``) on a :class:`MysqlDatabase`.

    Rollback runs on ordinary errors only. When the client deadline cancels a statement, the
    connection is mid-result and is dropped instead, which makes the server roll back.
    """

    def placeholder_problem(self, sql: str, expected: int) -> str | None:
        found = 0
        for token in _PERCENT.findall(sql):
            if token == "s":
                found += 1
            elif token != "%":
                return "uses a % that is not %s or %% (named placeholders are not supported)"
        return f"uses {found} placeholder(s)" if found != expected else None

    async def _session[T](
        self,
        client: MysqlDatabase,
        timeout_seconds: float,
        label: str,
        body: Callable[[MysqlCursor], Awaitable[T]],
    ) -> T:
        async with client.acquire() as connection:
            try:
                async with (
                    asyncio.timeout(timeout_seconds + _GRACE_SECONDS),
                    connection.cursor() as cursor,
                ):
                    await _limit_session(cursor, client.mariadb, timeout_seconds)
                    return await body(cursor)
            except TooManyRows:
                raise
            except TimeoutError:
                _drop(connection)
                raise ToolExecutionError(
                    f"query {label!r} timed out after {timeout_seconds}s"
                ) from None
            except Exception as exc:
                code = _error_code(exc)
                if code in _TIMEOUT_ERRORS:
                    raise ToolExecutionError(
                        f"query {label!r} timed out after {timeout_seconds}s"
                    ) from None
                # The server's message can quote the data; name only the error.
                detail = f"MySQL error {code}" if code else type(exc).__name__
                raise ToolExecutionError(f"query {label!r} failed ({detail})") from None

    async def read(
        self,
        client: MysqlDatabase,
        sql: str,
        values: Sequence[Any],
        *,
        timeout_seconds: float,
        limit: int,
        label: str,
    ) -> list[Mapping[str, Any]]:
        async def body(cursor: MysqlCursor) -> list[Mapping[str, Any]]:
            await cursor.execute("START TRANSACTION READ ONLY")
            try:
                await cursor.execute(sql, tuple(values))
                rows = list(await cursor.fetchmany(limit + 1))
                if len(rows) > limit:
                    raise too_many_rows(label, limit)
            except Exception:
                await cursor.execute("ROLLBACK")
                raise
            await cursor.execute("ROLLBACK")
            return rows

        return await self._session(client, timeout_seconds, label, body)

    async def write(
        self,
        client: MysqlDatabase,
        sql: str,
        values: Sequence[Any],
        *,
        timeout_seconds: float,
        returning: bool,
        limit: int,
        label: str,
    ) -> list[Mapping[str, Any]] | int:
        async def body(cursor: MysqlCursor) -> list[Mapping[str, Any]] | int:
            await cursor.execute("START TRANSACTION")
            try:
                await cursor.execute(sql, tuple(values))
                result: list[Mapping[str, Any]] | int
                if returning:
                    result = list(await cursor.fetchmany(limit + 1))
                    if len(result) > limit:
                        raise too_many_rows(label, limit, rolled_back=True)
                else:
                    result = max(cursor.rowcount, 0)
            except Exception:
                await cursor.execute("ROLLBACK")
                raise
            await cursor.execute("COMMIT")
            return result

        return await self._session(client, timeout_seconds, label, body)


def _drop(connection: MysqlConnection) -> None:
    """Close a connection left mid-statement; the pool discards it and the server rolls back
    its open transaction."""
    with contextlib.suppress(Exception):  # already failing; closing is best effort
        connection.close()


class MysqlToolkit(SqlToolkit):
    """A toolkit of named, typed queries against one MySQL or MariaDB database.

    ``dsn`` is an ``env://`` reference (``mysql://user:password@host:port/database``, with
    ``?ssl=verify`` for verified TLS) resolved when the pool opens. ``client`` replaces the
    pool opener, for example with :meth:`FakeMysql.factory
    <algen_agent_tools.mysql.FakeMysql.factory>`. Queries use ``%s``; write a literal ``%`` as
    ``%%``.
    """

    def __init__(
        self,
        name: str = "mysql",
        *,
        dsn: str = DEFAULT_DSN,
        max_rows: int = DEFAULT_MAX_ROWS,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
        permission_prefix: str | None = None,
        client: Any = None,
    ) -> None:
        super().__init__(
            name,
            dialect=MysqlDialect(),
            max_rows=max_rows,
            permission_prefix=permission_prefix,
            env=env_names(dsn),
            client=client
            or partial(open_database, dsn=dsn, min_size=pool_min_size, max_size=pool_max_size),
        )


def mysql_toolkit(
    name: str = "mysql",
    *,
    dsn: str = DEFAULT_DSN,
    max_rows: int = DEFAULT_MAX_ROWS,
    client: Any = None,
) -> MysqlToolkit:
    """A :class:`MysqlToolkit` with two catalog reads of the DSN's database:
    ``<name>.list_tables`` and ``<name>.describe_table``.

    Add the database's own typed queries with :meth:`MysqlToolkit.query`.
    """
    toolkit = MysqlToolkit(name, dsn=dsn, max_rows=max_rows, client=client)
    toolkit.query(
        "list_tables",
        _LIST_TABLES,
        description=(
            "List the tables and views you can read in the database, with their approximate "
            "row counts."
        ),
        row=TableSummary,
        preset=read(),
    )
    toolkit.add_describe_table(
        _DESCRIBE_TABLE,
        arguments=strict_model("describe_table_arguments", table=table_argument(64)),
        values=lambda arguments: [getattr(arguments, "table")],  # noqa: B009
    )
    return toolkit
