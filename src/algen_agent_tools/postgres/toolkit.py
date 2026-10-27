"""Typed PostgreSQL actions: named, parameterised queries a developer writes once.

The model never supplies SQL. It picks a declared query and fills in its typed parameters.
Reads run in a read-only transaction; every statement runs under a ``statement_timeout`` that
matches the tool's timeout, and reads stop at a row limit instead of loading everything.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any

from algen_agent_runtime.tools.authoring import read

from algen_agent_tools._env import env_names
from algen_agent_tools.postgres.client import PostgresConnection, PostgresPool, open_pool
from algen_agent_tools.sql.core import (
    DEFAULT_MAX_ROWS,
    ColumnDescription,
    Dialect,
    RowsAffected,
    SqlToolkit,
    TableDescription,
    TableSummary,
    schema_argument,
    strict_model,
    table_argument,
    too_many_rows,
)

__all__ = [
    "DEFAULT_DSN",
    "DEFAULT_MAX_ROWS",
    "ColumnDescription",
    "PostgresDialect",
    "PostgresToolkit",
    "RowsAffected",
    "TableDescription",
    "TableSummary",
    "postgres_toolkit",
]

DEFAULT_DSN = "env://ALGEN_TOOLS_POSTGRES_DSN"

_PLACEHOLDER = re.compile(r"\$(\d+)")

_LIST_TABLES = """
SELECT n.nspname AS schema_name,
       c.relname AS name,
       CASE c.relkind WHEN 'v' THEN 'view' WHEN 'm' THEN 'materialized_view' ELSE 'table' END
           AS kind,
       CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END AS estimated_rows
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = $1
  AND c.relkind IN ('r', 'p', 'v', 'm')
  AND pg_catalog.has_table_privilege(c.oid, 'SELECT')
ORDER BY c.relname
"""

_DESCRIBE_TABLE = """
SELECT a.attname AS name,
       pg_catalog.format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable,
       pg_catalog.pg_get_expr(d.adbin, d.adrelid) AS default,
       COALESCE(a.attnum = ANY (i.indkey), false) AS primary_key
FROM pg_catalog.pg_attribute AS a
JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN pg_catalog.pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
LEFT JOIN pg_catalog.pg_index AS i ON i.indrelid = c.oid AND i.indisprimary
WHERE n.nspname = $1
  AND c.relname = $2
  AND a.attnum > 0
  AND NOT a.attisdropped
  AND pg_catalog.has_table_privilege(c.oid, 'SELECT')
ORDER BY a.attnum
"""


async def _set_timeout(connection: PostgresConnection, timeout_seconds: float) -> None:
    # An int built here, never caller text, so formatting it into the statement is safe.
    # At least 1 ms: PostgreSQL reads statement_timeout = 0 as no timeout at all.
    milliseconds = max(1, int(timeout_seconds * 1000))
    await connection.execute(f"SET LOCAL statement_timeout = {milliseconds}")


def _rows_affected(status: str) -> int:
    """The count at the end of a command tag such as ``UPDATE 3`` or ``INSERT 0 1``."""
    last = status.rsplit(" ", 1)[-1]
    return int(last) if last.isdigit() else 0


class PostgresDialect(Dialect):
    """``$1``-style placeholders on an asyncpg pool: reads in a read-only transaction, every
    statement under ``SET LOCAL statement_timeout``."""

    def placeholder_problem(self, sql: str, expected: int) -> str | None:
        found = {int(index) for index in _PLACEHOLDER.findall(sql)}
        if found != set(range(1, expected + 1)):
            return f"uses placeholders {sorted(found)}"
        return None

    async def read(
        self,
        client: PostgresPool,
        sql: str,
        values: Sequence[Any],
        *,
        timeout_seconds: float,
        limit: int,
        label: str,
    ) -> list[Mapping[str, Any]]:
        async with client.acquire() as connection, connection.transaction(readonly=True):
            await _set_timeout(connection, timeout_seconds)
            rows: list[Mapping[str, Any]] = []
            async for record in connection.cursor(sql, *values):
                if len(rows) == limit:
                    raise too_many_rows(label, limit)
                rows.append(record)
            return rows

    async def write(
        self,
        client: PostgresPool,
        sql: str,
        values: Sequence[Any],
        *,
        timeout_seconds: float,
        returning: bool,
        limit: int,
        label: str,
    ) -> list[Mapping[str, Any]] | int:
        async with client.acquire() as connection, connection.transaction():
            await _set_timeout(connection, timeout_seconds)
            if not returning:
                return _rows_affected(await connection.execute(sql, *values))
            records = await connection.fetch(sql, *values)
            if len(records) > limit:
                raise too_many_rows(label, limit, rolled_back=True)
            return list(records)


class PostgresToolkit(SqlToolkit):
    """A toolkit of named, typed queries against one PostgreSQL database.

    ``dsn`` is an ``env://`` reference resolved when the pool opens. ``client`` replaces the
    pool opener, for example with :meth:`FakePostgres.factory
    <algen_agent_tools.postgres.FakePostgres.factory>`. Queries use ``$1``, ``$2``, ...
    """

    def __init__(
        self,
        name: str = "postgres",
        *,
        dsn: str = DEFAULT_DSN,
        max_rows: int = DEFAULT_MAX_ROWS,
        pool_min_size: int = 0,
        pool_max_size: int = 5,
        permission_prefix: str | None = None,
        client: Any = None,
    ) -> None:
        super().__init__(
            name,
            dialect=PostgresDialect(),
            max_rows=max_rows,
            permission_prefix=permission_prefix,
            env=env_names(dsn),
            client=client
            or partial(open_pool, dsn=dsn, min_size=pool_min_size, max_size=pool_max_size),
        )


def postgres_toolkit(
    name: str = "postgres",
    *,
    dsn: str = DEFAULT_DSN,
    schemas: tuple[str, ...] = ("public",),
    max_rows: int = DEFAULT_MAX_ROWS,
    client: Any = None,
) -> PostgresToolkit:
    """A :class:`PostgresToolkit` with two catalog reads limited to ``schemas``:
    ``<name>.list_tables`` and ``<name>.describe_table``.

    Add the database's own typed queries with :meth:`PostgresToolkit.query`.
    """
    schema_field = schema_argument(schemas)
    toolkit = PostgresToolkit(name, dsn=dsn, max_rows=max_rows, client=client)
    toolkit.query(
        "list_tables",
        _LIST_TABLES,
        description=(
            "List the tables and views you can read in one database schema, with their "
            "approximate row counts."
        ),
        params=strict_model("list_tables_arguments", schema_name=schema_field),
        row=TableSummary,
        preset=read(),
    )
    toolkit.add_describe_table(
        _DESCRIBE_TABLE,
        arguments=strict_model(
            "describe_table_arguments", schema_name=schema_field, table=table_argument(63)
        ),
        values=lambda arguments: [
            getattr(arguments, "schema_name"),  # noqa: B009 - a dynamic model's field
            getattr(arguments, "table"),  # noqa: B009
        ],
    )
    return toolkit
