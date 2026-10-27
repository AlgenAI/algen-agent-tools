"""Typed MySQL and MariaDB actions.

The model never writes SQL: each tool is a named, parameterised query declared in code, run in
a read-only transaction when it reads, under a timeout and a row limit.
"""

from algen_agent_tools.mysql.client import MysqlDatabase, open_database
from algen_agent_tools.mysql.fake import FakeMysql, FakeMysqlCall, FakeMysqlError
from algen_agent_tools.mysql.toolkit import DEFAULT_DSN, MysqlToolkit, mysql_toolkit

__all__ = [
    "DEFAULT_DSN",
    "FakeMysql",
    "FakeMysqlCall",
    "FakeMysqlError",
    "MysqlDatabase",
    "MysqlToolkit",
    "mysql_toolkit",
    "open_database",
]
