"""The MySQL toolkit against a real server.

Runs when ``ALGEN_TOOLS_TEST_MYSQL_DSN`` names a disposable database (CI starts one as a service
container). Each test works in its own table and drops it afterwards.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from algen_agent_runtime.exceptions.errors import ToolExecutionError
from algen_agent_runtime.tools.authoring import read, write
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import BaseModel

from algen_agent_tools.mysql import MysqlToolkit, mysql_toolkit

pytest.importorskip("aiomysql")

DSN_VARIABLE = "ALGEN_TOOLS_TEST_MYSQL_DSN"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv(DSN_VARIABLE), reason=f"{DSN_VARIABLE} is not set"),
]


class Customer(BaseModel):
    customer_id: str


class Invoice(BaseModel):
    id: int
    customer_id: str


@pytest.fixture
async def table() -> AsyncIterator[str]:
    name = f"invoices_{uuid.uuid4().hex[:8]}"
    admin = MysqlToolkit("admin", dsn=f"env://{DSN_VARIABLE}", pool_max_size=1)
    database: Any = await admin.client()
    async with database.acquire() as connection, connection.cursor() as cursor:
        await cursor.execute(
            f"CREATE TABLE {name} (id INT AUTO_INCREMENT PRIMARY KEY, "
            "customer_id VARCHAR(20) NOT NULL, note VARCHAR(20) DEFAULT 'x', "
            "request_key VARCHAR(100) UNIQUE)"
        )
        await cursor.execute(
            f"INSERT INTO {name} (customer_id, note) VALUES ('c1', '50%'), ('c1', 'a'), ('c2', 'b')"
        )
    try:
        yield name
    finally:
        async with database.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(f"DROP TABLE {name}")
        await admin.aclose()


def toolkit(**options: Any) -> MysqlToolkit:
    return MysqlToolkit(dsn=f"env://{DSN_VARIABLE}", **options)


async def test_catalog_reads(table: str) -> None:
    tools = mysql_toolkit(dsn=f"env://{DSN_VARIABLE}")
    listed = next(t for t in tools.tools if t.definition.name == "mysql.list_tables")
    names = [row["name"] for row in await listed.execute({}, fake_tool_context(listed))]
    assert table in names
    describe = next(t for t in tools.tools if t.definition.name == "mysql.describe_table")
    described = await describe.execute({"table": table}, fake_tool_context(describe))
    columns = {column["name"]: column for column in described["columns"]}
    assert columns["id"]["primary_key"] is True and columns["customer_id"]["nullable"] is False
    assert columns["note"]["default"] == "x"
    await tools.aclose()


async def test_typed_reads_with_a_literal_percent(table: str) -> None:
    tools = toolkit()
    query = tools.query(
        "customer_invoices",
        f"SELECT id, customer_id FROM {table} WHERE customer_id = %s AND note NOT LIKE '50%%' "
        "ORDER BY id",
        description="x",
        params=Customer,
        row=Invoice,
    )
    rows = await query.execute({"customer_id": "c1"}, fake_tool_context(query))
    assert [row["customer_id"] for row in rows] == ["c1"]
    await tools.aclose()


async def test_a_read_cannot_write(table: str) -> None:
    tools = toolkit()
    sneaky = tools.query(
        "sneaky", f"DELETE FROM {table}", description="x", row=Invoice, preset=read()
    )
    with pytest.raises(ToolExecutionError, match="failed"):
        await sneaky.execute({}, fake_tool_context(sneaky))
    count = tools.query(
        "count",
        f"SELECT COUNT(*) AS id, 'all' AS customer_id FROM {table}",
        description="x",
        row=Invoice,
    )
    assert (await count.execute({}, fake_tool_context(count)))[0]["id"] == 3
    await tools.aclose()


async def test_writes_are_exactly_once_per_idempotency_key(table: str) -> None:
    tools = toolkit()
    add = tools.query(
        "add",
        f"INSERT IGNORE INTO {table} (customer_id, request_key) VALUES (%s, %s)",
        description="x",
        params=Customer,
        preset=write(),
        idempotency_key=True,
    )
    context = fake_tool_context(add)
    assert await add.execute({"customer_id": "c9"}, context) == {"rows_affected": 1}
    assert await add.execute({"customer_id": "c9"}, context) == {"rows_affected": 0}
    await tools.aclose()


async def test_a_slow_read_times_out(table: str) -> None:
    tools = toolkit()
    slow = tools.query(
        "slow",
        "SELECT COUNT(*) AS id, 'x' AS customer_id FROM information_schema.COLUMNS a, "
        "information_schema.COLUMNS b, information_schema.COLUMNS c",
        description="x",
        row=Invoice,
        preset=read(timeout=0.5),
    )
    with pytest.raises(ToolExecutionError, match="timed out"):
        await slow.execute({}, fake_tool_context(slow))
    # The pool still serves queries afterwards.
    ok = tools.query(
        "ok",
        f"SELECT id, customer_id FROM {table} ORDER BY id LIMIT 1",
        description="x",
        row=Invoice,
    )
    assert len(await ok.execute({}, fake_tool_context(ok))) == 1
    await tools.aclose()
