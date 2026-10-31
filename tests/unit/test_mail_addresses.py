"""Mail results carry addresses as schema-declared fields, so an agent can reply (#35 item 2)."""

from __future__ import annotations

from typing import Any

from algen_agent_runtime.policies.engine import CompositePolicyEngine
from algen_agent_runtime.tools.executor import ToolExecutor
from algen_agent_runtime.tools.registry import ToolRegistry
from algen_agent_runtime.tools.testing import fake_tool_context

from algen_agent_tools.mail.inbound import InboundEmailDetail, InboundEmailSummary
from algen_agent_tools.outlook import outlook_toolkit
from tests.unit.test_outlook import Graph


def test_addresses_are_read_from_the_display_text() -> None:
    summary = InboundEmailSummary(
        provider_message_id="m1",
        sender='"Ana, Finance" <ana@contoso.com>',
        recipients="agent@contoso.com, Bo <bo@contoso.com>, not an address",
    )
    assert (summary.sender_name, summary.sender_address) == ("Ana, Finance", "ana@contoso.com")
    assert summary.recipient_addresses == ("agent@contoso.com", "bo@contoso.com")
    detail = InboundEmailDetail(provider_message_id="m1", cc="Team <team@contoso.com>")
    assert detail.cc_addresses == ("team@contoso.com",)
    assert detail.sender_address is None
    # The display strings are unchanged, for callers that read them.
    assert summary.sender == '"Ana, Finance" <ana@contoso.com>'


def _schema_formats(schema: Any) -> set[str]:
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("format") == "email":
                found.add("email")
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return found


async def test_the_default_pii_guardrail_leaves_the_sender_for_the_model() -> None:
    """Through the Runtime's executor and its default policies: the address fields arrive
    intact, while addresses inside free text are still redacted."""
    toolkit = outlook_toolkit(client=Graph().factory())
    registry = ToolRegistry()
    for tool in toolkit.tools:
        registry.register(tool)
    listing = registry.get("outlook.list_messages")
    assert _schema_formats(listing.definition.output_schema) == {"email"}
    executor = ToolExecutor(registry, CompositePolicyEngine())

    result = await executor.execute(
        "outlook.list_messages", {"max_results": 10}, fake_tool_context(listing)
    )

    [row] = result.value["messages"]
    assert row["sender_address"] == "ana@contoso.com"
    assert row["recipient_addresses"] == ["agent@contoso.com"]
    assert "ana@contoso.com" not in row["sender"]  # the display string is free text
    await toolkit.aclose()
