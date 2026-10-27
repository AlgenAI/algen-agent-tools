"""The shared core of the database connectors: named, typed queries the model fills in but
never writes, run by each database's :class:`Dialect` under a timeout and a row limit."""

from algen_agent_tools.sql.core import (
    DEFAULT_MAX_ROWS,
    ColumnDescription,
    Dialect,
    RowsAffected,
    SqlToolkit,
    TableDescription,
    TableSummary,
    TooManyRows,
)

__all__ = [
    "DEFAULT_MAX_ROWS",
    "ColumnDescription",
    "Dialect",
    "RowsAffected",
    "SqlToolkit",
    "TableDescription",
    "TableSummary",
    "TooManyRows",
]
