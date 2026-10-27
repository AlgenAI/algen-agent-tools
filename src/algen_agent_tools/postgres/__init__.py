"""Typed PostgreSQL actions.

The model never writes SQL: each tool is a named, parameterised query declared in code, run in
a read-only transaction when it reads, under a statement timeout and a row limit. Replaces the
Runtime's ``database_tool``, which took a raw statement.
"""

from algen_agent_tools.postgres.client import PostgresConnection, PostgresPool, open_pool
from algen_agent_tools.postgres.fake import FakeCall, FakePostgres
from algen_agent_tools.postgres.toolkit import (
    DEFAULT_DSN,
    ColumnDescription,
    PostgresToolkit,
    RowsAffected,
    TableDescription,
    TableSummary,
    postgres_toolkit,
)

__all__ = [
    "DEFAULT_DSN",
    "ColumnDescription",
    "FakeCall",
    "FakePostgres",
    "PostgresConnection",
    "PostgresPool",
    "PostgresToolkit",
    "RowsAffected",
    "TableDescription",
    "TableSummary",
    "open_pool",
    "postgres_toolkit",
]
