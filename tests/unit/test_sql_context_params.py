"""Typed queries that bind the caller's identity from the call, never from the model."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from algen_agent_runtime.exceptions.errors import ConfigurationError
from algen_agent_runtime.tools.authoring import write
from algen_agent_runtime.tools.contracts import Tool
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import AliasChoices, AliasPath, BaseModel, Field, ValidationError

from algen_agent_tools.postgres import FakePostgres, PostgresToolkit
from algen_agent_tools.sql import CONTEXT_VALUES
from algen_agent_tools.sqlite import SqliteDatabase, SqliteToolkit
from algen_agent_tools.testing import assert_toolkit_contract


class DueBefore(BaseModel):
    due_before: str


class Invoice(BaseModel):
    id: int
    amount: float


class Note(BaseModel):
    invoice_id: int
    text: str


def billing(fake: FakePostgres) -> PostgresToolkit:
    toolkit = PostgresToolkit("billing", client=fake.factory)
    toolkit.query(
        "my_invoices",
        "SELECT id, amount FROM invoices WHERE due < $1 AND tenant_id = $2 AND owner_id = $3",
        description="List the caller's invoices due before a date.",
        params=DueBefore,
        row=Invoice,
        context_params=("tenant_id", "user_id"),
    )
    toolkit.query(
        "add_my_note",
        "INSERT INTO notes (invoice_id, text, tenant_id, author_id, request_key) "
        "VALUES ($1, $2, $3, $4, $5) ON CONFLICT (request_key) DO NOTHING",
        description="Add a note to one of the caller's invoices.",
        params=Note,
        preset=write(),
        idempotency_key=True,
        context_params=("tenant_id", "user_id"),
    )
    return toolkit


def tool(toolkit: PostgresToolkit, name: str) -> Tool:
    return next(item for item in toolkit.tools if item.definition.name == f"billing.{name}")


def test_context_params_are_not_part_of_the_tool_input() -> None:
    toolkit = billing(FakePostgres())
    assert_toolkit_contract(toolkit)

    for name, fields in (("my_invoices", {"due_before"}), ("add_my_note", {"invoice_id", "text"})):
        schema = tool(toolkit, name).definition.input_schema
        assert set(schema["properties"]) == fields
        assert schema.get("additionalProperties") is False
    assert CONTEXT_VALUES == ("tenant_id", "user_id", "run_id", "step_id")


async def test_a_read_binds_the_callers_tenant_and_user_after_the_model_params() -> None:
    fake = FakePostgres().on("FROM invoices", rows=[{"id": 1, "amount": 9.5}])
    read_tool = tool(billing(fake), "my_invoices")
    context = fake_tool_context(read_tool, tenant_id="acme", user_id="u-42")

    result = await read_tool.execute({"due_before": "2026-12-01"}, context)

    assert result == [{"id": 1, "amount": 9.5}]
    assert fake.calls[-1].args == ("2026-12-01", "acme", "u-42")
    assert fake.calls[-1].readonly


async def test_the_model_cannot_supply_a_context_value() -> None:
    fake = FakePostgres().on("FROM invoices", rows=[])
    read_tool = tool(billing(fake), "my_invoices")

    with pytest.raises(ValidationError):
        await read_tool.execute(
            {"due_before": "2026-12-01", "tenant_id": "other", "user_id": "admin"},
            fake_tool_context(read_tool, tenant_id="acme", user_id="u-42"),
        )
    assert fake.calls == []


async def test_a_write_binds_context_values_before_the_idempotency_key() -> None:
    fake = FakePostgres().on("INSERT INTO notes", status="INSERT 0 1")
    write_tool = tool(billing(fake), "add_my_note")
    context = fake_tool_context(
        write_tool, tenant_id="acme", user_id="u-42", idempotency_key="run-1:step-2"
    )

    result = await write_tool.execute({"invoice_id": 7, "text": "Paid"}, context)

    assert result == {"rows_affected": 1}
    assert fake.calls[-1].args == (7, "Paid", "acme", "u-42", "run-1:step-2")


@pytest.mark.parametrize(
    ("sql", "kwargs", "message"),
    [
        (
            "SELECT id, amount FROM invoices WHERE due < $1 AND tenant_id = $2",
            {"context_params": ("tenant_id", "user_id")},
            "context user_id",
        ),
        ("SELECT 1 WHERE $1 = $2", {"context_params": ("tenant", "user_id")}, "not one of"),
        ("SELECT 1 WHERE $1 = $2", {"context_params": ("credentials",)}, "not one of"),
        ("SELECT 1 WHERE $1 = $2", {"context_params": ("tenant_id", "tenant_id")}, "twice"),
        ("SELECT 1 WHERE $1 = $2", {"context_params": "tenant_id"}, "sequence"),
    ],
)
def test_context_params_are_checked_when_declared(
    sql: str, kwargs: dict[str, Any], message: str
) -> None:
    toolkit = PostgresToolkit("billing", client=FakePostgres().factory)
    with pytest.raises(ConfigurationError, match=message):
        toolkit.query("q", sql, description="d", params=DueBefore, row=Invoice, **kwargs)


class ByName(BaseModel):
    tenant_id: str


class ByAlias(BaseModel):
    tenant: str = Field(alias="tenant_id")


class ByValidationAlias(BaseModel):
    tenant: str = Field(validation_alias="tenant_id")


class ByAliasChoice(BaseModel):
    tenant: str = Field(validation_alias=AliasChoices("org", "tenant_id"))


class ByAliasPath(BaseModel):
    tenant: str = Field(validation_alias=AliasPath("tenant_id", 0))


@pytest.mark.parametrize(
    "spoofable", [ByName, ByAlias, ByValidationAlias, ByAliasChoice, ByAliasPath]
)
def test_a_model_field_cannot_shadow_a_context_value(spoofable: type[BaseModel]) -> None:
    toolkit = PostgresToolkit("billing", client=FakePostgres().factory)
    with pytest.raises(ConfigurationError, match="would let the model choose"):
        toolkit.query(
            "q",
            "SELECT id, amount FROM invoices WHERE tenant_id = $1 AND tenant_id = $2",
            description="d",
            params=spoofable,
            row=Invoice,
            context_params=("tenant_id",),
        )


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "shop.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE invoices (
                id INTEGER PRIMARY KEY, tenant_id TEXT, owner_id TEXT, amount REAL
            );
            INSERT INTO invoices (tenant_id, owner_id, amount) VALUES
                ('acme', 'u-1', 10), ('acme', 'u-2', 20), ('globex', 'u-1', 30);
            """
        )
    return path


async def test_sqlite_rows_are_filtered_to_the_callers_tenant_and_user(database: Path) -> None:
    async def open_client(secrets: Any) -> SqliteDatabase:
        return SqliteDatabase(str(database), read_only=True)

    toolkit = SqliteToolkit(client=open_client)
    mine = toolkit.query(
        "my_invoices",
        "SELECT id, amount FROM invoices WHERE tenant_id = ? AND owner_id = ? ORDER BY id",
        description="The caller's invoices.",
        row=Invoice,
        context_params=("tenant_id", "user_id"),
    )

    for tenant, user, expected in (
        ("acme", "u-1", [{"id": 1, "amount": 10.0}]),
        ("acme", "u-2", [{"id": 2, "amount": 20.0}]),
        ("globex", "u-1", [{"id": 3, "amount": 30.0}]),
        ("initech", "u-1", []),
    ):
        context = fake_tool_context(mine, tenant_id=tenant, user_id=user)
        assert await mine.execute({}, context) == expected
    await toolkit.aclose()
