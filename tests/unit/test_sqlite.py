"""The SQLite toolkit against real database files in a temporary directory."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from algen_agent_runtime.exceptions.errors import ConfigurationError, ToolExecutionError
from algen_agent_runtime.tools.authoring import read, write
from algen_agent_runtime.tools.contracts import SideEffect, Tool
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import BaseModel

from algen_agent_tools.sql import TooManyRows
from algen_agent_tools.sqlite import SqliteDatabase, SqliteToolkit, sqlite_toolkit
from algen_agent_tools.testing import assert_toolkit_contract, plugin_tools


class Customer(BaseModel):
    customer_id: str


class Invoice(BaseModel):
    id: int
    customer_id: str
    amount: float


class NewInvoice(BaseModel):
    customer_id: str
    amount: float


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "shop.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE invoices (
                id INTEGER PRIMARY KEY,
                customer_id TEXT NOT NULL,
                amount REAL DEFAULT 0,
                request_key TEXT UNIQUE
            );
            CREATE VIEW big_invoices AS SELECT * FROM invoices WHERE amount > 100;
            INSERT INTO invoices (customer_id, amount) VALUES ('c1', 10), ('c1', 250), ('c2', 5);
            """
        )
    return path


def opener(path: Path, *, read_only: bool = True) -> Any:
    async def open_client(secrets: Any) -> SqliteDatabase:
        return SqliteDatabase(str(path), read_only=read_only)

    return open_client


async def run(tool: Tool, arguments: dict[str, Any]) -> Any:
    return await tool.execute(arguments, fake_tool_context(tool))


def named(toolkit: Any, action: str) -> Tool:
    return next(t for t in toolkit.tools if t.definition.name == f"sqlite.{action}")


def test_the_plugin_describes_two_catalog_reads() -> None:
    tools = plugin_tools("algen_agent_tools.sqlite")
    assert {name: tool.side_effect for name, tool in tools.items()} == {
        "sqlite.describe_table": SideEffect.READ,
        "sqlite.list_tables": SideEffect.READ,
    }
    assert tools["sqlite.list_tables"].env == ("ALGEN_TOOLS_SQLITE_PATH",)
    assert_toolkit_contract(sqlite_toolkit())


async def test_list_and_describe_tables(database: Path) -> None:
    toolkit = sqlite_toolkit(client=opener(database))
    tables = await run(named(toolkit, "list_tables"), {})
    assert [(t["name"], t["kind"]) for t in tables] == [
        ("big_invoices", "view"),
        ("invoices", "table"),
    ]
    described = await run(named(toolkit, "describe_table"), {"table": "invoices"})
    assert described["schema_name"] == "main"
    assert described["columns"][0] == {
        "name": "id",
        "data_type": "INTEGER",
        "nullable": True,
        "default": None,
        "primary_key": True,
    }
    assert described["columns"][1]["nullable"] is False
    assert described["columns"][2]["default"] == "0"
    with pytest.raises(ToolExecutionError, match="no readable table 'missing'"):
        await run(named(toolkit, "describe_table"), {"table": "missing"})
    await toolkit.aclose()


async def test_declared_reads_take_typed_parameters_and_stop_at_the_row_limit(
    database: Path,
) -> None:
    toolkit = SqliteToolkit(client=opener(database), max_rows=1)
    tool = toolkit.query(
        "customer_invoices",
        "SELECT id, customer_id, amount FROM invoices WHERE customer_id = ? ORDER BY id",
        description="Invoices of one customer.",
        params=Customer,
        row=Invoice,
    )
    assert await run(tool, {"customer_id": "c2"}) == [{"id": 3, "customer_id": "c2", "amount": 5.0}]
    with pytest.raises(TooManyRows, match="matched more than 1 rows"):
        await run(tool, {"customer_id": "c1"})


async def test_reads_cannot_change_the_database(database: Path) -> None:
    toolkit = SqliteToolkit(client=opener(database, read_only=False))
    sneaky = toolkit.query(
        "sneaky",
        "DELETE FROM invoices RETURNING id",
        description="A read that is really a delete.",
        row=Customer,
    )
    with pytest.raises(ToolExecutionError, match="failed"):
        await run(sneaky, {})
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM invoices").fetchone() == (3,)


async def test_a_slow_statement_is_interrupted_at_the_tool_timeout(database: Path) -> None:
    toolkit = SqliteToolkit(client=opener(database))
    endless = toolkit.query(
        "endless",
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) "
        "SELECT max(i) AS customer_id FROM n",
        description="Never finishes.",
        row=Customer,
        preset=read(timeout=0.2),
    )
    with pytest.raises(ToolExecutionError, match=r"timed out after 0\.2s"):
        await run(endless, {})
    # The connection is usable afterwards.
    assert await run(named(sqlite_toolkit(client=opener(database)), "list_tables"), {})


def test_a_read_only_toolkit_refuses_write_queries(database: Path) -> None:
    with pytest.raises(ConfigurationError, match="read_only=False"):
        SqliteToolkit(client=opener(database)).query(
            "add", "DELETE FROM invoices", description="x", preset=write()
        )


async def test_writes_commit_once_per_idempotency_key(database: Path) -> None:
    toolkit = SqliteToolkit(read_only=False, client=opener(database, read_only=False))
    add = toolkit.query(
        "add_invoice",
        "INSERT INTO invoices (customer_id, amount, request_key) VALUES (?, ?, ?) "
        "ON CONFLICT (request_key) DO NOTHING",
        description="Add an invoice.",
        params=NewInvoice,
        preset=write(),
        idempotency_key=True,
    )
    context = fake_tool_context(add)
    first = await add.execute({"customer_id": "c3", "amount": 7}, context)
    again = await add.execute({"customer_id": "c3", "amount": 7}, context)
    assert (first, again) == ({"rows_affected": 1}, {"rows_affected": 0})

    returning = toolkit.query(
        "raise_all",
        "UPDATE invoices SET amount = amount + 1 RETURNING id, customer_id, amount",
        description="Raise every invoice.",
        row=Invoice,
        preset=write(),
        max_rows=2,
    )
    with pytest.raises(TooManyRows, match="rolled back"):
        await run(returning, {})
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT sum(amount) FROM invoices").fetchone() == (272.0,)


async def test_a_read_only_file_refuses_writes_even_from_raw_sql(database: Path) -> None:
    client = SqliteDatabase(str(database), read_only=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        await client.run(lambda connection: connection.execute("DELETE FROM invoices"))
    await client.close()


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT id FROM invoices WHERE customer_id = ? AND id = ?", "uses 2 placeholder"),
        ("SELECT id FROM invoices WHERE customer_id = :customer", "use plain \\?"),
        ("SELECT id FROM invoices WHERE customer_id = ?1", "use plain \\?"),
    ],
)
def test_placeholders_must_match_the_declared_values(sql: str, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        SqliteToolkit(client=lambda secrets: None).query(
            "q", sql, description="x", params=Customer, row=Customer
        )


async def test_the_database_opens_from_env_and_must_exist(
    database: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_SQLITE_PATH", str(database))
    toolkit = sqlite_toolkit()
    assert len(await run(named(toolkit, "list_tables"), {})) == 2
    await toolkit.aclose()

    monkeypatch.setenv("ALGEN_TOOLS_SQLITE_PATH", str(tmp_path / "missing.db"))
    with pytest.raises(ConfigurationError, match="could not open the SQLite database"):
        await sqlite_toolkit().client()
    assert not (tmp_path / "missing.db").exists()
