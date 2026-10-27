"""The small slice of an asyncpg pool the Postgres toolkit uses, and how it opens one."""

from __future__ import annotations

from collections.abc import AsyncIterable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from algen_agent_tools._env import SecretResolver, missing_extra


class PostgresConnection(Protocol):
    def transaction(self, *, readonly: bool = False) -> AbstractAsyncContextManager[Any]: ...

    def cursor(self, query: str, *args: Any) -> AsyncIterable[Mapping[str, Any]]: ...

    async def fetch(self, query: str, *args: Any) -> list[Any]: ...

    async def execute(self, query: str, *args: Any) -> str: ...


class PostgresPool(Protocol):
    def acquire(self) -> AbstractAsyncContextManager[PostgresConnection]: ...

    async def close(self) -> None: ...


async def open_pool(
    secrets: SecretResolver, *, dsn: str, min_size: int, max_size: int
) -> PostgresPool:
    """Resolve the DSN, then import asyncpg and open a pool; nothing connects before this."""
    resolved = await secrets(dsn)
    try:
        import asyncpg
    except ImportError as exc:
        raise missing_extra("asyncpg", "postgres") from exc
    pool: PostgresPool = await asyncpg.create_pool(resolved, min_size=min_size, max_size=max_size)
    return pool
