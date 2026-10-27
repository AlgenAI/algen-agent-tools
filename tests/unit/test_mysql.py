"""The MySQL toolkit against the in-memory fake."""

from __future__ import annotations

import sys
from typing import Any

import pytest
from algen_agent_runtime.exceptions.errors import ConfigurationError, ToolExecutionError
from algen_agent_runtime.tools.authoring import read, write
from algen_agent_runtime.tools.contracts import SideEffect, Tool
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import BaseModel

from algen_agent_tools.mysql import FakeMysql, FakeMysqlError, MysqlToolkit, mysql_toolkit
from algen_agent_tools.mysql.client import _settings
from algen_agent_tools.sql import TooManyRows
from algen_agent_tools.testing import assert_toolkit_contract, plugin_tools


class Customer(BaseModel):
    customer_id: str


class Invoice(BaseModel):
    id: int
    amount: float


async def run(tool: Tool, arguments: dict[str, Any]) -> Any:
    return await tool.execute(arguments, fake_tool_context(tool))


def named(toolkit: Any, action: str) -> Tool:
    return next(t for t in toolkit.tools if t.definition.name == f"mysql.{action}")


def invoices_tool(fake: FakeMysql, **options: Any) -> Tool:
    return MysqlToolkit(client=fake.factory, **options).query(
        "customer_invoices",
        "SELECT id, amount FROM invoices WHERE customer_id = %s AND note NOT LIKE '50%%'",
        description="Invoices of one customer.",
        params=Customer,
        row=Invoice,
    )


def test_the_plugin_describes_two_catalog_reads() -> None:
    tools = plugin_tools("algen_agent_tools.mysql")
    assert {name: tool.side_effect for name, tool in tools.items()} == {
        "mysql.describe_table": SideEffect.READ,
        "mysql.list_tables": SideEffect.READ,
    }
    assert tools["mysql.list_tables"].env == ("ALGEN_TOOLS_MYSQL_DSN",)
    assert_toolkit_contract(mysql_toolkit())


async def test_reads_run_read_only_under_the_tool_timeout() -> None:
    fake = FakeMysql().on("FROM invoices", rows=[{"id": 1, "amount": 9.5}])
    assert await run(invoices_tool(fake), {"customer_id": "c1"}) == [{"id": 1, "amount": 9.5}]
    sqls = [call.sql for call in fake.calls]
    assert sqls[:3] == [
        "SET SESSION max_execution_time = 30000",
        "SET SESSION innodb_lock_wait_timeout = 30",
        "START TRANSACTION READ ONLY",
    ]
    query = next(call for call in fake.calls if "FROM invoices" in call.sql)
    assert (query.args, query.readonly) == (("c1",), True)
    assert sqls[-1] == "ROLLBACK"


async def test_mariadb_limits_every_statement() -> None:
    fake = FakeMysql(mariadb=True).on("FROM invoices", rows=[])
    await run(invoices_tool(fake), {"customer_id": "c1"})
    assert fake.calls[0].sql == "SET SESSION max_statement_time = 30.0"


async def test_a_read_that_writes_is_refused_by_the_read_only_transaction() -> None:
    fake = FakeMysql().on("DELETE FROM invoices", rows=[])
    sneaky = MysqlToolkit(client=fake.factory).query(
        "sneaky", "DELETE FROM invoices", description="x", row=Invoice
    )
    with pytest.raises(ToolExecutionError, match=r"failed \(MySQL error 1792\)"):
        await run(sneaky, {})
    assert fake.calls[-1].sql == "ROLLBACK"


async def test_reads_stop_at_the_row_limit() -> None:
    fake = FakeMysql().on("FROM invoices", rows=[{"id": i, "amount": 1} for i in range(3)])
    with pytest.raises(TooManyRows, match="matched more than 2 rows"):
        await run(invoices_tool(fake, max_rows=2), {"customer_id": "c1"})


async def test_server_timeouts_and_errors_are_content_minimal() -> None:
    fake = FakeMysql().on(
        "FROM invoices",
        error=FakeMysqlError(3024, "maximum statement execution time exceeded"),
    )
    with pytest.raises(ToolExecutionError, match=r"timed out after 30\.0s"):
        await run(invoices_tool(fake), {"customer_id": "c1"})

    fake = FakeMysql().on(
        "FROM invoices", error=FakeMysqlError(1062, "Duplicate entry 'secret@x.com' for key")
    )
    with pytest.raises(ToolExecutionError) as caught:
        await run(invoices_tool(fake), {"customer_id": "c1"})
    assert str(caught.value) == "query 'customer_invoices' failed (MySQL error 1062)"
    assert caught.value.__cause__ is None


async def test_the_client_deadline_drops_a_stuck_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("algen_agent_tools.mysql.toolkit._GRACE_SECONDS", 0.0)
    fake = FakeMysql().on("FROM invoices", rows=[], delay=1)
    tool = MysqlToolkit(client=fake.factory).query(
        "slow",
        "SELECT id, amount FROM invoices",
        description="x",
        row=Invoice,
        preset=read(timeout=0.05),
    )
    with pytest.raises(ToolExecutionError, match="timed out"):
        await run(tool, {})
    assert fake.dropped == 1
    assert "ROLLBACK" not in [call.sql for call in fake.calls]


async def test_writes_commit_or_roll_back() -> None:
    fake = FakeMysql().on("INTO invoices", rowcount=1)
    add = MysqlToolkit(client=fake.factory).query(
        "add",
        "INSERT IGNORE INTO invoices (customer_id, request_key) VALUES (%s, %s)",
        description="x",
        params=Customer,
        preset=write(),
        idempotency_key=True,
    )
    context = fake_tool_context(add)
    assert await add.execute({"customer_id": "c1"}, context) == {"rows_affected": 1}
    insert = next(call for call in fake.calls if "INSERT" in call.sql)
    assert insert.args == ("c1", context.idempotency_key)
    assert [c.sql for c in fake.calls][-1] == "COMMIT"

    fake = FakeMysql().on("DELETE FROM invoices", rows=[{"id": 1, "amount": 1}] * 3)
    purge = MysqlToolkit(client=fake.factory).query(
        "purge",
        "DELETE FROM invoices RETURNING id, amount",
        description="x",
        row=Invoice,
        preset=write(),
        max_rows=2,
    )
    with pytest.raises(TooManyRows, match="rolled back"):
        await run(purge, {})
    assert [c.sql for c in fake.calls][-1] == "ROLLBACK"


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT id FROM t WHERE a = %s AND b = %s", "uses 2 placeholder"),
        ("SELECT id FROM t WHERE a = %(customer)s", "named placeholders"),
        ("SELECT id FROM t WHERE a = %s AND b LIKE '5%'", "not %s or %%"),
    ],
)
def test_placeholders_must_match_the_declared_values(sql: str, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        MysqlToolkit(client=FakeMysql().factory).query(
            "q", sql, description="x", params=Customer, row=Invoice
        )


async def test_catalog_reads_use_the_dsn_database() -> None:
    fake = (
        FakeMysql()
        .on(
            "FROM information_schema.TABLES",
            rows=[
                {"schema_name": "shop", "name": "invoices", "kind": "table", "estimated_rows": 3}
            ],
        )
        .on(
            "FROM information_schema.COLUMNS",
            rows=lambda args: (
                [
                    {
                        "schema_name": "shop",
                        "name": "id",
                        "data_type": "int",
                        "nullable": 0,
                        "default": None,
                        "primary_key": 1,
                    }
                ]
                if args == ("invoices",)
                else []
            ),
        )
    )
    toolkit = mysql_toolkit(client=fake.factory)
    assert (await run(named(toolkit, "list_tables"), {}))[0]["name"] == "invoices"
    described = await run(named(toolkit, "describe_table"), {"table": "invoices"})
    assert described == {
        "schema_name": "shop",
        "name": "invoices",
        "columns": [
            {
                "name": "id",
                "data_type": "int",
                "nullable": False,
                "default": None,
                "primary_key": True,
            }
        ],
    }
    with pytest.raises(ToolExecutionError, match=r"no readable table 'missing'$"):
        await run(named(toolkit, "describe_table"), {"table": "missing"})


def test_dsn_settings() -> None:
    settings = _settings("mysql://app%40x:p%2Fw@db.internal:3307/shop?ssl=verify")
    assert (settings["user"], settings["password"], settings["host"], settings["port"]) == (
        "app@x",
        "p/w",
        "db.internal",
        3307,
    )
    assert settings["db"] == "shop" and settings["ssl"].verify_mode.name == "CERT_REQUIRED"
    assert "ssl" not in _settings("mysql://u:p@h/shop")
    for bad in ("postgresql://u:p@h/db", "mysql://u:p@h", "mysql://u:p@h/db?ssl=off"):
        with pytest.raises(ConfigurationError):
            _settings(bad)


async def test_a_missing_driver_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_MYSQL_DSN", "mysql://u:p@localhost/shop")
    monkeypatch.setitem(sys.modules, "aiomysql", None)
    with pytest.raises(ConfigurationError, match=r"algen-agent-tools\[mysql\]"):
        await mysql_toolkit().client()
