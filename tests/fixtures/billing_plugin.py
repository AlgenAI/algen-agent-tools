"""A project's plugin for the caching test: a query bound to the caller over a fake database."""

from __future__ import annotations

from typing import Any

from algen_agent_runtime.plugins.sdk import PluginManifest, plugin
from pydantic import BaseModel

from algen_agent_tools.postgres import FakePostgres, PostgresToolkit


class DueBefore(BaseModel):
    due_before: str


class Invoice(BaseModel):
    id: int
    owner: str


def _owned_rows(args: tuple[Any, ...]) -> list[dict[str, Any]]:
    _, tenant_id, user_id = args
    return [{"id": 1, "owner": f"{tenant_id}/{user_id}"}]


DATABASE = FakePostgres().on("FROM invoices", rows=_owned_rows)


@plugin(PluginManifest(name="tests.billing", version="1.0.0", publisher="algen-agent-tools tests"))
class BillingPlugin:
    def register(self, context: Any) -> None:
        billing = PostgresToolkit("billing", client=DATABASE.factory)
        billing.query(
            "my_invoices",
            "SELECT id, owner FROM invoices WHERE due < $1 AND tenant_id = $2 AND owner_id = $3",
            description="List the caller's invoices due before a date.",
            params=DueBefore,
            row=Invoice,
            context_params=("tenant_id", "user_id"),
        )
        billing.register(context)
