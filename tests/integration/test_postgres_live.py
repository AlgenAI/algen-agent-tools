"""The Postgres toolkit against a real server.

Runs when ``ALGEN_TOOLS_TEST_POSTGRES_DSN`` names a disposable database (CI starts one as a
service container). Each test works in its own schema and drops it afterwards.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
from algen_agent_runtime.tools.authoring import write
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import BaseModel

from algen_agent_tools.postgres import PostgresToolkit, postgres_toolkit

asyncpg = pytest.importorskip("asyncpg")

DSN_VARIABLE = "ALGEN_TOOLS_TEST_POSTGRES_DSN"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.getenv(DSN_VARIABLE), reason=f"{DSN_VARIABLE} is not set"),
]


@pytest.fixture
async def schema() -> AsyncIterator[str]:
    name = f"t_{uuid.uuid4().hex[:10]}"
    connection = await asyncpg.connect(os.environ[DSN_VARIABLE])
    try:
        await connection.execute(f"CREATE SCHEMA {name}")
        await connection.execute(
            f"CREATE TABLE {name}.invoices (id integer PRIMARY KEY, customer text NOT NULL, "
            "amount numeric(10, 2) NOT NULL DEFAULT 0, note text)"
        )
        await connection.execute(
            f"CREATE TABLE {name}.notes (request_key text PRIMARY KEY, invoice_id integer, "
            "text text)"
        )
        await connection.execute(f"CREATE VIEW {name}.big AS SELECT * FROM {name}.invoices")
        await connection.execute(
            f"INSERT INTO {name}.invoices VALUES (1, 'acme', 10.50, null), (2, 'acme', 3, 'x')"
        )
        yield name
    finally:
        await connection.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")
        await connection.close()


class Customer(BaseModel):
    customer: str


class Invoice(BaseModel):
    id: int
    amount: Decimal


class Note(BaseModel):
    invoice_id: int
    text: str


async def test_catalog_tools_read_the_real_catalog(schema: str) -> None:
    toolkit = postgres_toolkit(dsn=f"env://{DSN_VARIABLE}", schemas=(schema,))
    list_tables, describe_table = toolkit.tools
    try:
        tables = await list_tables.execute({}, fake_tool_context(list_tables))
        described = await describe_table.execute(
            {"table": "invoices"}, fake_tool_context(describe_table)
        )
    finally:
        await toolkit.aclose()

    assert [(table["name"], table["kind"]) for table in tables] == [
        ("big", "view"),
        ("invoices", "table"),
        ("notes", "table"),
    ]
    columns = {column["name"]: column for column in described["columns"]}
    assert list(columns) == ["id", "customer", "amount", "note"]
    assert columns["id"]["primary_key"] and not columns["id"]["nullable"]
    assert columns["amount"]["data_type"] == "numeric(10,2)"
    assert columns["amount"]["default"] == "0"
    assert columns["note"]["nullable"]


async def test_typed_read_and_exactly_once_write(schema: str) -> None:
    toolkit = PostgresToolkit("billing", dsn=f"env://{DSN_VARIABLE}")
    find = toolkit.query(
        "invoices_for",
        f"SELECT id, amount FROM {schema}.invoices WHERE customer = $1 ORDER BY id",
        description="List a customer's invoices.",
        params=Customer,
        row=Invoice,
    )
    add_note = toolkit.query(
        "add_note",
        f"INSERT INTO {schema}.notes (invoice_id, text, request_key) VALUES ($1, $2, $3) "
        "ON CONFLICT (request_key) DO NOTHING",
        description="Add a note to an invoice.",
        params=Note,
        preset=write(),
        idempotency_key=True,
    )
    try:
        rows = await find.execute({"customer": "acme"}, fake_tool_context(find))
        context = fake_tool_context(add_note, idempotency_key="run:1")
        first = await add_note.execute({"invoice_id": 1, "text": "paid"}, context)
        replay = await add_note.execute({"invoice_id": 1, "text": "paid"}, context)
    finally:
        await toolkit.aclose()

    assert rows == [{"id": 1, "amount": "10.50"}, {"id": 2, "amount": "3.00"}]
    assert first == {"rows_affected": 1}
    assert replay == {"rows_affected": 0}


async def test_a_read_query_cannot_write(schema: str) -> None:
    toolkit = PostgresToolkit("billing", dsn=f"env://{DSN_VARIABLE}")
    sneaky = toolkit.query(
        "sneaky",
        f"UPDATE {schema}.invoices SET amount = 0 WHERE customer = $1 RETURNING id, amount",
        description="Looks like a read.",
        params=Customer,
        row=Invoice,
    )
    try:
        with pytest.raises(asyncpg.exceptions.ReadOnlySQLTransactionError):
            await sneaky.execute({"customer": "acme"}, fake_tool_context(sneaky))
    finally:
        await toolkit.aclose()


async def test_context_params_keep_a_query_to_the_callers_rows(schema: str) -> None:
    toolkit = PostgresToolkit("billing", dsn=f"env://{DSN_VARIABLE}")
    mine = toolkit.query(
        "my_invoices",
        f"SELECT id, amount FROM {schema}.invoices WHERE customer = $1 ORDER BY id",
        description="List the calling tenant's invoices.",
        row=Invoice,
        context_params=("tenant_id",),
    )
    try:
        acme = await mine.execute({}, fake_tool_context(mine, tenant_id="acme"))
        other = await mine.execute({}, fake_tool_context(mine, tenant_id="globex"))
    finally:
        await toolkit.aclose()

    assert acme == [{"id": 1, "amount": "10.50"}, {"id": 2, "amount": "3.00"}]
    assert other == []
