"""``context_params`` reads through the Runtime's real executor with tool-result caching on.

The tool declares the values it binds as its ``cache_context`` (algen-agent-runtime#70), so the
cache key holds them as well as the tool's arguments and the identity the scope selects. Two
users of one tenant making the same call each get their own rows at every scope.
"""

from __future__ import annotations

import json

import pytest
from algen_agent_runtime.config.settings import AppSettings
from algen_agent_runtime.models.providers.mock import MockModelProvider, tool_call_response
from algen_agent_runtime.orchestration.container import build_container
from algen_agent_runtime.runtime.client import AlgenAgentRuntimeClient
from algen_agent_runtime.types.contracts import RunRequest, RunStatus

from tests.fixtures import billing_plugin

TENANT = "acme"
CALL = {"due_before": "2026-12-01"}


def settings(cache_scope: str | None) -> AppSettings:
    cache: dict[str, object] = {"backend": "memory"}
    if cache_scope is not None:
        cache["policies"] = {"tool_results": {"enabled": True, "scope": cache_scope}}
    return AppSettings.model_validate(
        {
            "providers": {"demo": {"type": "mock", "default_model": "deterministic"}},
            "agents": [
                {
                    "name": "billing",
                    "version": "1.0.0",
                    "description": "Lists the caller's invoices.",
                    "system_instructions": "Use the tools.",
                    "default_model": {
                        "name": "deterministic",
                        "provider": "demo",
                        "model": "deterministic",
                        "required_capabilities": ["chat", "tools"],
                    },
                    "domain_plugins": ["tests.fixtures.billing_plugin:BillingPlugin"],
                    "enabled_tools": ["billing.my_invoices"],
                    "tool_permissions": ["billing.read"],
                }
            ],
            "security": {"trusted_plugin_prefixes": ["tests.fixtures"]},
            "storage": {"run_store": "memory", "memory_store": "memory"},
            "cache": cache,
        }
    )


async def owners_seen(cache_scope: str | None, users: list[str]) -> list[str]:
    """Run the agent once per user, in order, in one Runtime; return the owner each one saw."""
    billing_plugin.DATABASE.calls.clear()
    container = build_container(settings(cache_scope))
    model = container.runtime.router.provider("demo")
    assert isinstance(model, MockModelProvider)
    seen: list[str] = []
    try:
        await container.astart()
        client = AlgenAgentRuntimeClient(container.runtime)
        for user in users:
            model.queue(tool_call_response("billing.my_invoices", CALL), "Done.")
            result = await client.run(
                RunRequest(agent="billing", input="My invoices?", tenant_id=TENANT, user_id=user)
            )
            final = await container.runtime.wait(result.run_id, TENANT)
            assert final.status == RunStatus.COMPLETED, final.error
            tool_message = model.requests[-1].messages[-1]
            rows = json.loads(tool_message.text_content)
            seen.extend(row["owner"] for row in rows)
    finally:
        await container.aclose()
    return seen


def sql_runs() -> int:
    return sum("FROM invoices" in sql for sql in billing_plugin.DATABASE.statements())


async def test_the_bound_identity_reaches_the_query_through_the_executor() -> None:
    assert await owners_seen(None, ["u-1", "u-2"]) == ["acme/u-1", "acme/u-2"]
    assert sql_runs() == 2


@pytest.mark.parametrize("scope", ["user", "run"])
async def test_a_cache_scoped_to_the_caller_keeps_users_apart(scope: str) -> None:
    assert await owners_seen(scope, ["u-1", "u-2"]) == ["acme/u-1", "acme/u-2"]


async def test_a_user_scoped_cache_still_serves_the_same_user() -> None:
    # Proves the cache is on in these tests: the second call is answered without SQL.
    assert await owners_seen("user", ["u-1", "u-1"]) == ["acme/u-1", "acme/u-1"]
    assert sql_runs() == 1


@pytest.mark.parametrize("scope", ["tenant", "global"])
async def test_a_tenant_wide_cache_keeps_users_apart(scope: str) -> None:
    # The tool's cache_context puts the bound user in the key, so even a cache shared by the
    # whole tenant, or by everyone, answers each user from their own query.
    assert await owners_seen(scope, ["u-1", "u-2"]) == ["acme/u-1", "acme/u-2"]
    assert sql_runs() == 2


async def test_a_tenant_wide_cache_still_serves_the_same_user() -> None:
    assert await owners_seen("tenant", ["u-1", "u-1"]) == ["acme/u-1", "acme/u-1"]
    assert sql_runs() == 1
