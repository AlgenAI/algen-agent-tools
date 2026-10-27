"""An agent run through the Runtime's real executor, policies and approval flow."""

from __future__ import annotations

from typing import Any

from algen_agent_runtime.config.settings import AppSettings
from algen_agent_runtime.models.providers.mock import MockModelProvider, tool_call_response
from algen_agent_runtime.orchestration.container import build_container
from algen_agent_runtime.runtime.client import AlgenAgentRuntimeClient
from algen_agent_runtime.types.contracts import RunRequest, RunStatus

from tests.fixtures import agent_plugin

TENANT = "tenant-1"


def settings() -> AppSettings:
    return AppSettings.model_validate(
        {
            "providers": {"demo": {"type": "mock", "default_model": "deterministic"}},
            "agents": [
                {
                    "name": "desk",
                    "version": "1.0.0",
                    "description": "Echoes, then emails after approval.",
                    "system_instructions": "Use the tools.",
                    "default_model": {
                        "name": "deterministic",
                        "provider": "demo",
                        "model": "deterministic",
                        "required_capabilities": ["chat", "tools"],
                    },
                    "domain_plugins": ["tests.fixtures.agent_plugin:AgentPlugin"],
                    "enabled_tools": ["sample.echo", "smtp.send_email"],
                    "tool_permissions": ["sample.read", "smtp.write"],
                }
            ],
            "security": {"trusted_plugin_prefixes": ["tests.fixtures"]},
            "storage": {"run_store": "memory", "memory_store": "memory"},
        }
    )


async def run(decision: str, body: str = "Hello.") -> dict[str, Any]:
    agent_plugin.MAILBOX.sent.clear()
    agent_plugin.MAILBOX.receipts.clear()
    container = build_container(settings())
    model = container.runtime.router.provider("demo")
    assert isinstance(model, MockModelProvider)
    model.queue(
        tool_call_response("sample.echo", {"text": "ping"}),
        tool_call_response(
            "smtp.send_email",
            {"to": [{"address": "person@example.com"}], "subject": "Hi", "text_body": body},
        ),
        "Done.",
    )
    try:
        await container.astart()
        result = await AlgenAgentRuntimeClient(container.runtime).run(
            RunRequest(agent="desk", input="Echo, then email.", tenant_id=TENANT, user_id="u")
        )
        paused = result.status
        if paused == RunStatus.AWAITING_APPROVAL:
            await container.runtime.resume(result.run_id, TENANT, {"decision": decision})
        final = await container.runtime.wait(result.run_id, TENANT)
        return {
            "paused": paused,
            "status": final.status,
            "output": final.output,
            "error": final.error,
        }
    finally:
        await container.aclose()


async def test_an_approved_email_is_sent_once_from_the_deployment_address() -> None:
    outcome = await run("approved")

    assert outcome["paused"] == RunStatus.AWAITING_APPROVAL
    assert outcome["status"] == RunStatus.COMPLETED
    [sent] = agent_plugin.MAILBOX.sent
    assert sent.sender.address == "desk@example.org"
    assert [item.address for item in sent.to] == ["person@example.com"]


async def test_a_rejected_email_is_never_sent() -> None:
    outcome = await run("rejected")

    assert outcome["paused"] == RunStatus.AWAITING_APPROVAL
    assert agent_plugin.MAILBOX.sent == []


async def test_the_default_pii_guardrail_still_screens_the_message() -> None:
    # Recipients declare format: email, so pii leaves them alone; an address in the body is
    # redacted, and the Runtime refuses to run a tool on redacted arguments.
    outcome = await run("approved", body="Write to ops@example.com or call 9876543210.")

    assert outcome["status"] == RunStatus.FAILED
    assert "transformed by policy" in str(outcome["error"])
    assert agent_plugin.MAILBOX.sent == []
