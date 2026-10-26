from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal

import pytest
from algen_agent_runtime.exceptions.errors import ConfigurationError, ToolExecutionError
from algen_agent_runtime.tools.authoring import destructive, write
from algen_agent_runtime.tools.contracts import Idempotency, SideEffect
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import BaseModel, Field, ValidationError

from algen_agent_tools.postgres import (
    FakePostgres,
    PostgresToolkit,
    open_pool,
    postgres_toolkit,
)
from algen_agent_tools.testing import assert_toolkit_contract, plugin_tools


class CustomerInvoices(BaseModel):
    customer_id: str = Field(min_length=1)
    due_before: date


class Invoice(BaseModel):
    id: int
    amount: Decimal
    due: date


class NewNote(BaseModel):
    invoice_id: int
    text: str = Field(max_length=500)


def billing(fake: FakePostgres, *, max_rows: int = 500) -> PostgresToolkit:
    toolkit = PostgresToolkit(
        "billing", dsn="env://BILLING_DSN", client=fake.factory, max_rows=max_rows
    )
    toolkit.query(
        "list_open_invoices",
        "SELECT id, amount, due FROM invoices WHERE customer_id = $1 AND due < $2",
        description="List one customer's open invoices due before a date.",
        params=CustomerInvoices,
        row=Invoice,
    )
    toolkit.query(
        "add_note",
        "INSERT INTO invoice_notes (invoice_id, text, request_key) VALUES ($1, $2, $3) "
        "ON CONFLICT (request_key) DO NOTHING",
        description="Add a note to an invoice.",
        params=NewNote,
        preset=write(),
        idempotency_key=True,
    )
    return toolkit


def tool(toolkit: PostgresToolkit, name: str) -> object:
    return next(item for item in toolkit.tools if item.definition.name == name)


def test_toolkits_meet_the_contract() -> None:
    assert_toolkit_contract(billing(FakePostgres()))
    assert_toolkit_contract(postgres_toolkit(client=FakePostgres().factory))


def test_a_query_is_a_typed_tool_with_no_sql_parameter() -> None:
    toolkit = billing(FakePostgres())
    [read_tool, write_tool] = toolkit.tools

    assert read_tool.definition.name == "billing.list_open_invoices"
    assert read_tool.definition.side_effect == SideEffect.READ
    assert read_tool.definition.required_permissions == frozenset({"billing.read"})
    assert set(read_tool.definition.input_schema["properties"]) == {"customer_id", "due_before"}
    assert read_tool.definition.output_schema["type"] == "array"
    assert write_tool.definition.idempotency == Idempotency.KEYED
    assert write_tool.definition.retry_policy.max_attempts == 1
    assert write_tool.definition.required_permissions == frozenset({"billing.write"})
    assert set(write_tool.definition.input_schema["properties"]) == {"invoice_id", "text"}
    assert toolkit.env == ("BILLING_DSN",)


async def test_a_read_runs_read_only_with_a_timeout_and_typed_values() -> None:
    fake = FakePostgres().on(
        "FROM invoices",
        rows=[{"id": 7, "amount": Decimal("12.50"), "due": date(2026, 11, 1)}],
    )
    read_tool = billing(fake).tools[0]

    result = await read_tool.execute(
        {"customer_id": "c-1", "due_before": "2026-12-01"}, fake_tool_context(read_tool)
    )

    assert result == [{"id": 7, "amount": "12.50", "due": "2026-11-01"}]
    timeout, query = fake.calls
    assert timeout.sql == "SET LOCAL statement_timeout = 30000"
    assert query.args == ("c-1", date(2026, 12, 1))
    assert query.readonly and timeout.readonly


async def test_a_read_that_matches_too_many_rows_fails_instead_of_truncating() -> None:
    rows = [{"id": n, "amount": "1", "due": "2026-01-01"} for n in range(3)]
    read_tool = billing(FakePostgres().on("FROM invoices", rows=rows), max_rows=2).tools[0]

    with pytest.raises(ToolExecutionError, match="more than 2 rows"):
        await read_tool.execute(
            {"customer_id": "c", "due_before": "2026-01-02"}, fake_tool_context(read_tool)
        )


async def test_a_write_passes_the_idempotency_key_and_reports_rows() -> None:
    fake = FakePostgres().on("INSERT INTO invoice_notes", status="INSERT 0 1")
    write_tool = billing(fake).tools[1]
    context = fake_tool_context(write_tool, idempotency_key="run-1:step-2")

    result = await write_tool.execute({"invoice_id": 7, "text": "Paid by wire"}, context)

    assert result == {"rows_affected": 1}
    assert fake.calls[-1].args == (7, "Paid by wire", "run-1:step-2")
    assert not fake.calls[-1].readonly


async def test_a_write_with_returning_returns_rows() -> None:
    fake = FakePostgres().on(
        "DELETE FROM invoices", rows=[{"id": 1, "amount": 3, "due": "2026-01-01"}]
    )
    toolkit = PostgresToolkit("billing", client=fake.factory)

    class InvoiceId(BaseModel):
        id: int

    removed = toolkit.query(
        "void_invoice",
        "DELETE FROM invoices WHERE id = $1 RETURNING id, amount, due",
        description="Void one invoice.",
        params=InvoiceId,
        row=Invoice,
        preset=destructive(),
    )

    assert removed.definition.requires_approval
    result = await removed.execute({"id": 1}, fake_tool_context(removed))
    assert result == [{"id": 1, "amount": "3", "due": "2026-01-01"}]


async def test_the_fake_refuses_a_write_inside_a_read_only_transaction() -> None:
    fake = FakePostgres().on("UPDATE invoices", rows=[])
    toolkit = PostgresToolkit("billing", client=fake.factory)

    class Nothing(BaseModel):
        pass

    sneaky = toolkit.query(
        "sneaky", "UPDATE invoices SET amount = 0 RETURNING id", description="x", row=Nothing
    )

    with pytest.raises(RuntimeError, match="read-only transaction"):
        await sneaky.execute({}, fake_tool_context(sneaky))


async def test_arguments_outside_the_schema_are_rejected() -> None:
    read_tool = billing(FakePostgres()).tools[0]

    with pytest.raises(ValidationError):
        await read_tool.execute(
            {"customer_id": "c", "due_before": "2026-01-01", "sql": "DROP TABLE x"},
            fake_tool_context(read_tool),
        )


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT 1 WHERE a = $1", "declares 2 value"),
        ("SELECT 1 WHERE a = $1 AND b = $3", "placeholders \\[1, 3\\]"),
    ],
)
def test_placeholders_must_match_the_declared_values(sql: str, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message):
        PostgresToolkit(client=FakePostgres().factory).query(
            "q", sql, description="x", params=CustomerInvoices, row=Invoice
        )


def test_a_read_needs_rows_and_cannot_take_the_key() -> None:
    toolkit = PostgresToolkit(client=FakePostgres().factory)
    with pytest.raises(ConfigurationError, match="needs a row model"):
        toolkit.query("q", "SELECT 1", description="x")
    with pytest.raises(ConfigurationError, match="cannot take the idempotency key"):
        toolkit.query("q", "SELECT $1", description="x", row=Invoice, idempotency_key=True)


async def test_list_tables_is_limited_to_the_configured_schemas() -> None:
    fake = FakePostgres().on(
        "FROM pg_catalog.pg_class",
        rows=[{"schema_name": "sales", "name": "orders", "kind": "table", "estimated_rows": 10}],
    )
    toolkit = postgres_toolkit(schemas=("sales", "ref"), client=fake.factory)
    list_tables = tool(toolkit, "postgres.list_tables")

    schema = list_tables.definition.input_schema["properties"]["schema_name"]  # type: ignore[attr-defined]
    assert schema["enum"] == ["sales", "ref"]
    assert await list_tables.execute({}, fake_tool_context(list_tables)) == [  # type: ignore[attr-defined]
        {"schema_name": "sales", "name": "orders", "kind": "table", "estimated_rows": 10}
    ]
    assert fake.calls[-1].args == ("sales",)
    with pytest.raises(ValidationError):
        await list_tables.execute({"schema_name": "pg_catalog"}, fake_tool_context(list_tables))  # type: ignore[attr-defined]


async def test_describe_table_returns_one_table_or_fails() -> None:
    fake = FakePostgres().on(
        "FROM pg_catalog.pg_attribute",
        rows=lambda args: (
            [
                {"name": "id", "data_type": "integer", "nullable": False, "primary_key": True},
                {"name": "note", "data_type": "text", "nullable": True, "default": None},
            ]
            if args == ("public", "orders")
            else []
        ),
    )
    describe = tool(postgres_toolkit(client=fake.factory), "postgres.describe_table")
    context = fake_tool_context(describe)  # type: ignore[arg-type]

    result = await describe.execute({"table": "orders"}, context)  # type: ignore[attr-defined]

    assert result["name"] == "orders" and result["schema_name"] == "public"
    assert [column["name"] for column in result["columns"]] == ["id", "note"]
    assert describe.definition.output_schema["properties"]["columns"]["type"] == "array"  # type: ignore[attr-defined]
    with pytest.raises(ToolExecutionError, match="no readable table 'missing'"):
        await describe.execute({"table": "missing"}, context)  # type: ignore[attr-defined]


def test_plugin_describes_the_catalog_tools_and_their_env() -> None:
    tools = plugin_tools("algen_agent_tools.postgres")

    assert set(tools) == {"postgres.list_tables", "postgres.describe_table"}
    assert all(item.side_effect == SideEffect.READ for item in tools.values())
    assert all(item.env == ("ALGEN_TOOLS_POSTGRES_DSN",) for item in tools.values())


async def test_open_pool_resolves_the_dsn_before_importing_asyncpg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved: list[str] = []

    async def secrets(reference: str) -> str:
        resolved.append(reference)
        return "postgresql://localhost/x"

    monkeypatch.setitem(sys.modules, "asyncpg", None)

    with pytest.raises(ConfigurationError, match=r"algen-agent-tools\[postgres\]"):
        await open_pool(secrets, dsn="env://X", min_size=0, max_size=1)
    assert resolved == ["env://X"]


def test_the_dsn_must_be_an_env_reference() -> None:
    with pytest.raises(ConfigurationError, match="env:// reference"):
        PostgresToolkit(dsn="postgresql://user:secret@host/db")
