"""Typed SQLite actions, with no extra packages.

The model never writes SQL: each tool is a named, parameterised query declared in code, run
read-only when it reads, under a timeout and a row limit. The file opens read-only unless the
toolkit allows writes.
"""

from algen_agent_tools.sqlite.toolkit import (
    DEFAULT_PATH,
    SqliteDatabase,
    SqliteToolkit,
    sqlite_toolkit,
)

__all__ = ["DEFAULT_PATH", "SqliteDatabase", "SqliteToolkit", "sqlite_toolkit"]
