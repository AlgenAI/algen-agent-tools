"""Typed PostgreSQL actions: named, parameterised queries a developer writes once.

The model never supplies SQL. It picks a declared query and fills in its typed parameters.
Reads run in a read-only transaction; every statement runs under a ``statement_timeout`` that
matches the tool's timeout, and reads stop at a row limit instead of loading everything.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import partial
from typing import Any, Literal

from algen_agent_runtime.exceptions.errors import ConfigurationError, ToolExecutionError
from algen_agent_runtime.tools.authoring import (
    DEFAULT_TIMEOUT_SECONDS,
    Toolkit,
    ToolPreset,
    read,
)
from algen_agent_runtime.tools.contracts import SideEffect, Tool, ToolContext
from pydantic import BaseModel, ConfigDict, Field, create_model

from algen_agent_tools._env import env_names
from algen_agent_tools.postgres.client import PostgresConnection, PostgresPool, open_pool

DEFAULT_DSN = "env://ALGEN_TOOLS_POSTGRES_DSN"
DEFAULT_MAX_ROWS = 500

_PLACEHOLDER = re.compile(r"\$(\d+)")


class RowsAffected(BaseModel):
    """What a write query without ``RETURNING`` reports."""

    rows_affected: int


class TableSummary(BaseModel):
    schema_name: str
    name: str
    kind: Literal["table", "view", "materialized_view"]
    estimated_rows: int | None = None


class ColumnDescription(BaseModel):
    name: str
    data_type: str
    nullable: bool
    default: str | None = None
    primary_key: bool = False


class TableDescription(BaseModel):
    schema_name: str
    name: str
    columns: list[ColumnDescription]


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


def _strict_model(name: str, **fields: Any) -> type[BaseModel]:
    model: type[BaseModel] = create_model(name, __config__=ConfigDict(extra="forbid"), **fields)
    return model


def _forbid_extra(model: type[BaseModel], name: str) -> type[BaseModel]:
    """``model``, refusing unknown fields at validation as its schema already says it does."""
    if model.model_config.get("extra") == "forbid":
        return model
    strict: type[BaseModel] = type(
        f"{name}_arguments", (model,), {"model_config": ConfigDict(extra="forbid")}
    )
    return strict


class PostgresToolkit(Toolkit):
    """A toolkit of named, typed queries against one PostgreSQL database.

    ``dsn`` is an ``env://`` reference resolved when the pool opens. ``client`` replaces the
    pool opener, for example with :meth:`FakePostgres.factory
    <algen_agent_tools.postgres.FakePostgres.factory>`.
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
        if max_rows < 1:
            raise ConfigurationError("max_rows must be at least 1")
        super().__init__(
            name,
            permission_prefix=permission_prefix,
            env=env_names(dsn),
            client=client
            or partial(open_pool, dsn=dsn, min_size=pool_min_size, max_size=pool_max_size),
        )
        self.max_rows = max_rows

    async def fetch_rows[RowT: BaseModel](
        self,
        sql: str,
        values: Sequence[Any],
        row: type[RowT],
        *,
        label: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_rows: int | None = None,
    ) -> list[RowT]:
        """Run one read in a read-only transaction and return at most ``max_rows`` rows.

        Use it inside your own ``@toolkit.tool(read())`` functions when a tool's result is not
        a plain list of rows. More rows than the limit is an error, not a silent truncation.
        """
        limit = max_rows or self.max_rows
        pool: PostgresPool = await self.client()
        async with pool.acquire() as connection, connection.transaction(readonly=True):
            await _set_timeout(connection, timeout_seconds)
            rows: list[RowT] = []
            async for record in connection.cursor(sql, *values):
                if len(rows) == limit:
                    raise ToolExecutionError(
                        f"query {label!r} matched more than {limit} rows; narrow its parameters"
                    )
                rows.append(row.model_validate(dict(record)))
            return rows

    def query(
        self,
        name: str,
        sql: str,
        *,
        description: str,
        params: type[BaseModel] | None = None,
        row: type[BaseModel] | None = None,
        preset: ToolPreset | None = None,
        idempotency_key: bool = False,
        max_rows: int | None = None,
    ) -> Tool:
        """Declare one query as the tool ``<toolkit>.<name>``.

        ``params`` fields fill ``$1``, ``$2``, ... in field order; with ``idempotency_key`` the
        call's idempotency key fills the next placeholder, so a write can be made exactly-once
        (for example ``ON CONFLICT (request_key) DO NOTHING``). ``row`` is the model of each
        returned row: a read returns ``list[row]``; a write returns ``list[row]`` when it has
        ``RETURNING``, else :class:`RowsAffected`. ``preset`` defaults to ``read()``.
        """
        preset = preset or read()
        reads = preset.side_effect in (SideEffect.NONE, SideEffect.READ)
        params = _forbid_extra(params, name) if params else _strict_model(f"{name}_arguments")
        fields = tuple(params.model_fields)
        expected = len(fields) + (1 if idempotency_key else 0)
        found = {int(index) for index in _PLACEHOLDER.findall(sql)}
        if found != set(range(1, expected + 1)):
            raise ConfigurationError(
                f"query {name!r} uses placeholders {sorted(found)} but declares {expected} "
                f"value(s): {', '.join(fields) or 'no params'}"
                f"{' and the idempotency key' if idempotency_key else ''}"
            )
        if reads and row is None:
            raise ConfigurationError(f"read query {name!r} needs a row model")
        if reads and idempotency_key:
            raise ConfigurationError(f"read query {name!r} cannot take the idempotency key")
        limit = max_rows or self.max_rows

        async def run(arguments: BaseModel, context: ToolContext) -> Any:
            values = [getattr(arguments, field) for field in fields]
            if idempotency_key:
                values.append(context.idempotency_key)
            if reads:
                assert row is not None
                return await self.fetch_rows(
                    sql,
                    values,
                    row,
                    label=name,
                    timeout_seconds=preset.timeout_seconds,
                    max_rows=limit,
                )
            pool: PostgresPool = await self.client()
            async with pool.acquire() as connection, connection.transaction():
                await _set_timeout(connection, preset.timeout_seconds)
                if row is None:
                    status = await connection.execute(sql, *values)
                    return RowsAffected(rows_affected=_rows_affected(status))
                records = await connection.fetch(sql, *values)
                if len(records) > limit:
                    raise ToolExecutionError(
                        f"query {name!r} returned more than {limit} rows; the change was "
                        "rolled back"
                    )
                return [row.model_validate(dict(record)) for record in records]

        run.__name__ = run.__qualname__ = name
        run.__annotations__ = {
            "arguments": params,
            "context": ToolContext,
            "return": list[row] if row is not None else RowsAffected,  # type: ignore[valid-type]
        }
        return self.tool(preset, description=description)(run)


async def _set_timeout(connection: PostgresConnection, timeout_seconds: float) -> None:
    # An int built here, never caller text, so formatting it into the statement is safe.
    await connection.execute(f"SET LOCAL statement_timeout = {int(timeout_seconds * 1000)}")


def _rows_affected(status: str) -> int:
    """The count at the end of a command tag such as ``UPDATE 3`` or ``INSERT 0 1``."""
    last = status.rsplit(" ", 1)[-1]
    return int(last) if last.isdigit() else 0


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
    if not schemas:
        raise ConfigurationError("schemas must name at least one schema")
    toolkit = PostgresToolkit(name, dsn=dsn, max_rows=max_rows, client=client)
    schema_field = (
        Literal[schemas],
        Field(default=schemas[0], description=f"One of: {', '.join(schemas)}."),
    )
    list_arguments = _strict_model("list_tables_arguments", schema_name=schema_field)
    describe_arguments = _strict_model(
        "describe_table_arguments",
        schema_name=schema_field,
        table=(str, Field(min_length=1, max_length=63, description="The table or view name.")),
    )
    preset = read()

    toolkit.query(
        "list_tables",
        _LIST_TABLES,
        description=(
            "List the tables and views you can read in one database schema, with their "
            "approximate row counts."
        ),
        params=list_arguments,
        row=TableSummary,
        preset=preset,
    )

    async def describe_table(arguments: BaseModel) -> TableDescription:
        """Describe one table or view: each column's name, type, whether it may be null, its
        default and whether it is part of the primary key."""
        schema: str = getattr(arguments, "schema_name")  # noqa: B009 - a dynamic model's field
        table: str = getattr(arguments, "table")  # noqa: B009
        columns = await toolkit.fetch_rows(
            _DESCRIBE_TABLE,
            [schema, table],
            ColumnDescription,
            label="describe_table",
            timeout_seconds=preset.timeout_seconds,
        )
        if not columns:
            raise ToolExecutionError(f"no readable table {table!r} in schema {schema!r}")
        return TableDescription(schema_name=schema, name=table, columns=columns)

    describe_table.__annotations__ = {
        "arguments": describe_arguments,
        "return": TableDescription,
    }
    toolkit.tool(preset)(describe_table)
    return toolkit
