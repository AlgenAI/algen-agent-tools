"""An in-memory stand-in for an asyncpg pool, for offline tests."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from algen_agent_tools._env import SecretResolver

Rows = Sequence[Mapping[str, Any]]
RowsHandler = Callable[[tuple[Any, ...]], Rows]

_WRITE = re.compile(r"^\s*(insert|update|delete|merge|create|alter|drop|truncate)\b", re.I)


def _normalise(sql: str) -> str:
    return " ".join(sql.split())


@dataclass(frozen=True)
class FakeCall:
    sql: str
    args: tuple[Any, ...]
    readonly: bool


@dataclass
class _Answer:
    fragment: str
    rows: Rows | RowsHandler
    status: str


class _FakeConnection:
    def __init__(self, database: FakePostgres) -> None:
        self._database = database
        self._readonly = False

    @asynccontextmanager
    async def transaction(self, *, readonly: bool = False) -> AsyncIterator[None]:
        self._readonly = readonly
        try:
            yield
        finally:
            self._readonly = False

    def _answer(self, sql: str, args: tuple[Any, ...]) -> _Answer:
        text = _normalise(sql)
        self._database.calls.append(FakeCall(text, args, self._readonly))
        if self._readonly and _WRITE.match(text):
            raise RuntimeError("cannot execute a write in a read-only transaction")
        for answer in self._database._answers:
            if answer.fragment in text:
                return answer
        raise AssertionError(f"FakePostgres has no answer for: {text}")

    def _rows(self, answer: _Answer, args: tuple[Any, ...]) -> Rows:
        return answer.rows(args) if callable(answer.rows) else answer.rows

    async def cursor(self, query: str, *args: Any) -> AsyncIterator[Mapping[str, Any]]:
        answer = self._answer(query, args)
        for row in self._rows(answer, args):
            yield row

    async def fetch(self, query: str, *args: Any) -> list[Mapping[str, Any]]:
        answer = self._answer(query, args)
        return list(self._rows(answer, args))

    async def execute(self, query: str, *args: Any) -> str:
        if _normalise(query).startswith("SET LOCAL "):
            self._database.calls.append(FakeCall(_normalise(query), args, self._readonly))
            return "SET"
        return self._answer(query, args).status


@dataclass
class FakePostgres:
    """Answers queries by SQL fragment and records every call.

    ``fake.on("FROM invoices", rows=[{...}])`` answers any statement containing the fragment
    (whitespace-normalised); ``rows`` may be a function of the arguments. A write inside a
    read-only transaction fails, as it would in PostgreSQL.
    """

    calls: list[FakeCall] = field(default_factory=list)
    closed: bool = False
    _answers: list[_Answer] = field(default_factory=list)

    def on(
        self, fragment: str, *, rows: Rows | RowsHandler = (), status: str = "SELECT 0"
    ) -> FakePostgres:
        self._answers.append(_Answer(_normalise(fragment), rows, status))
        return self

    async def factory(self, secrets: SecretResolver) -> FakePostgres:
        """Use as the toolkit's ``client``; it resolves no secret."""
        return self

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[_FakeConnection]:
        yield _FakeConnection(self)

    async def close(self) -> None:
        self.closed = True

    def statements(self) -> list[str]:
        """The SQL of every call except the per-transaction ``SET LOCAL``."""
        return [call.sql for call in self.calls if not call.sql.startswith("SET LOCAL ")]
