"""An in-memory stand-in for a MySQL pool, for offline tests."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from algen_agent_tools._env import SecretResolver

Rows = Sequence[Mapping[str, Any]]
RowsHandler = Callable[[tuple[Any, ...]], Rows]

_WRITE = re.compile(r"^\s*(insert|update|delete|replace|create|alter|drop|truncate)\b", re.I)
_CONTROL = re.compile(r"^(START TRANSACTION|ROLLBACK|COMMIT|SET SESSION )")


def _normalise(sql: str) -> str:
    return " ".join(sql.split())


@dataclass(frozen=True)
class FakeMysqlCall:
    sql: str
    args: tuple[Any, ...] | None
    readonly: bool


@dataclass
class _Answer:
    fragment: str
    rows: Rows | RowsHandler
    rowcount: int
    error: BaseException | None
    delay: float


class _FakeCursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection
        self._rows: list[Mapping[str, Any]] = []
        self.rowcount = -1

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, query: str, args: Any = None) -> int:
        database = self._connection.database
        text = _normalise(query)
        arguments = tuple(args) if args is not None else None
        database.calls.append(FakeMysqlCall(text, arguments, self._connection.readonly))
        if text == "START TRANSACTION READ ONLY":
            self._connection.readonly = True
            return 0
        if text in ("ROLLBACK", "COMMIT"):
            self._connection.readonly = False
            return 0
        if _CONTROL.match(text):
            return 0
        if self._connection.readonly and _WRITE.match(text):
            raise database.error_type(1792, "Cannot execute statement in a READ ONLY transaction.")
        for answer in database._answers:
            if answer.fragment in text:
                if answer.delay:
                    await asyncio.sleep(answer.delay)
                if answer.error is not None:
                    raise answer.error
                rows = answer.rows(arguments or ()) if callable(answer.rows) else answer.rows
                self._rows = list(rows)
                self.rowcount = answer.rowcount if answer.rowcount >= 0 else len(self._rows)
                return self.rowcount
        raise AssertionError(f"FakeMysql has no answer for: {text}")

    async def fetchmany(self, size: int) -> list[Mapping[str, Any]]:
        taken, self._rows = self._rows[:size], self._rows[size:]
        return taken


class _FakeConnection:
    def __init__(self, database: FakeMysql) -> None:
        self.database = database
        self.readonly = False
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def close(self) -> None:
        self.closed = True
        self.database.dropped += 1


class FakeMysqlError(Exception):
    """Raised like a PyMySQL error: ``args`` are ``(code, message)``."""


@dataclass
class FakeMysql:
    """Answers queries by SQL fragment and records every call.

    ``fake.on("FROM invoices", rows=[{...}])`` answers any statement containing the fragment
    (whitespace-normalised); ``rows`` may be a function of the arguments, ``error`` raises
    instead and ``delay`` sleeps first. A write inside a read-only transaction fails, as it
    would in MySQL.
    """

    mariadb: bool = False
    calls: list[FakeMysqlCall] = field(default_factory=list)
    closed: bool = False
    dropped: int = 0
    error_type: type[Exception] = FakeMysqlError
    _answers: list[_Answer] = field(default_factory=list)

    def on(
        self,
        fragment: str,
        *,
        rows: Rows | RowsHandler = (),
        rowcount: int = -1,
        error: BaseException | None = None,
        delay: float = 0,
    ) -> FakeMysql:
        self._answers.append(_Answer(_normalise(fragment), rows, rowcount, error, delay))
        return self

    async def factory(self, secrets: SecretResolver) -> FakeMysql:
        """Use as the toolkit's ``client``; it resolves no secret."""
        return self

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_FakeConnection]:
        yield _FakeConnection(self)

    async def close(self) -> None:
        self.closed = True

    def statements(self) -> list[str]:
        """The SQL of every call except session settings and transaction control."""
        return [call.sql for call in self.calls if not _CONTROL.match(call.sql)]
