"""The small slice of an aiomysql pool the MySQL toolkit uses, and how it opens one."""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlsplit

from algen_agent_runtime.exceptions.errors import ConfigurationError

from algen_agent_tools._env import SecretResolver, missing_extra


class MysqlCursor(Protocol):
    rowcount: int

    async def execute(self, query: str, args: Any = None) -> int: ...

    async def fetchmany(self, size: int) -> Any: ...

    async def __aenter__(self) -> MysqlCursor: ...

    async def __aexit__(self, *exc: object) -> None: ...


class MysqlConnection(Protocol):
    def cursor(self) -> MysqlCursor: ...

    def close(self) -> None: ...


class MysqlDatabase:
    """An aiomysql pool whose cursors return rows as dicts, and which server flavour it is."""

    def __init__(self, pool: Any, *, mariadb: bool) -> None:
        self._pool = pool
        self.mariadb = mariadb

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[MysqlConnection]:
        async with self._pool.acquire() as connection:
            yield connection

    async def close(self) -> None:
        self._pool.close()
        await self._pool.wait_closed()


def _settings(dsn: str) -> dict[str, Any]:
    """Connection settings from ``mysql://user:password@host:port/database?ssl=verify``."""
    parts = urlsplit(dsn)
    if parts.scheme != "mysql" or not parts.hostname or not parts.path.strip("/"):
        raise ConfigurationError(
            "the MySQL DSN must look like mysql://user:password@host:port/database"
        )
    options = {key: values[-1] for key, values in parse_qs(parts.query).items()}
    unknown = set(options) - {"ssl"}
    if unknown or options.get("ssl", "verify") != "verify":
        raise ConfigurationError("the MySQL DSN takes only the option ssl=verify")
    settings: dict[str, Any] = {
        "host": parts.hostname,
        "port": parts.port or 3306,
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "db": unquote(parts.path.strip("/")),
        "charset": "utf8mb4",
        "autocommit": True,  # the dialect opens and ends each transaction itself
    }
    if "ssl" in options:
        settings["ssl"] = ssl.create_default_context()
    return settings


async def open_database(
    secrets: SecretResolver, *, dsn: str, min_size: int, max_size: int
) -> MysqlDatabase:
    """Resolve the DSN, then import aiomysql, open a pool and note the server's flavour."""
    settings = _settings(await secrets(dsn))
    try:
        import aiomysql
    except ImportError as exc:
        raise missing_extra("aiomysql", "mysql") from exc
    pool = await aiomysql.create_pool(
        minsize=min_size, maxsize=max_size, cursorclass=aiomysql.DictCursor, **settings
    )
    async with pool.acquire() as connection, connection.cursor() as cursor:
        await cursor.execute("SELECT VERSION() AS version")
        [row] = await cursor.fetchmany(1)
    return MysqlDatabase(pool, mariadb="mariadb" in str(row["version"]).casefold())
