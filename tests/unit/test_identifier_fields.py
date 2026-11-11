"""Id fields declare ``format: identifier``, so the Runtime's ``pii`` guardrail does not read a
digit-string chat or message id as a phone or Aadhaar number (Runtime RT-B13, RT-G08)."""

from __future__ import annotations

from typing import Any

import pytest
from algen_agent_runtime.policies.contracts import PolicyAction
from algen_agent_runtime.policies.engine import CompositePolicyEngine, PIIRedactionPolicy
from algen_agent_runtime.tools.executor import ToolExecutor
from algen_agent_runtime.tools.registry import ToolRegistry
from algen_agent_runtime.tools.testing import fake_tool_context

from algen_agent_tools.discord import discord_toolkit
from algen_agent_tools.gmail import gmail_toolkit
from algen_agent_tools.imap import imap_toolkit
from algen_agent_tools.outlook import outlook_toolkit
from algen_agent_tools.telegram import telegram_toolkit
from tests.unit import test_telegram

# Twelve digits ending in their Verhoeff check digit: the Runtime's pii guardrail reads them as
# an Aadhaar number unless the field is declared an identifier.
CHAT_ID = "234567890124"


def _declares(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    return (
        schema.get("format") == "identifier"
        or any(_declares(branch) for branch in schema.get("anyOf", ()))
        or _declares(schema.get("items"))
    )


def _identifiers(schema: Any) -> set[str]:
    """The names of the properties, at any depth, whose schema declares an identifier."""
    found: set[str] = set()
    if isinstance(schema, dict):
        for name, child in (schema.get("properties") or {}).items():
            if _declares(child):
                found.add(name)
        for value in schema.values():
            found |= _identifiers(value)
    elif isinstance(schema, list):
        for item in schema:
            found |= _identifiers(item)
    return found


def _tool(toolkit: Any, name: str) -> Any:
    return next(tool for tool in toolkit.tools if tool.definition.name == name)


@pytest.mark.parametrize(
    ("toolkit", "name", "inputs"),
    [
        (
            telegram_toolkit(),
            "telegram.send_message",
            {"conversation_id", "reply_to_message_id"},
        ),
        (telegram_toolkit(), "telegram.list_messages", {"conversation_id", "page_token"}),
        (
            discord_toolkit(),
            "discord.send_message",
            {"conversation_id", "reply_to_message_id"},
        ),
        (gmail_toolkit(), "gmail.get_message", {"message_id"}),
        (gmail_toolkit(), "gmail.list_messages", {"page_token"}),
        (outlook_toolkit(), "outlook.get_thread", {"thread_id"}),
        (imap_toolkit(), "imap.list_messages", {"page_token"}),
    ],
)
def test_id_arguments_are_declared_identifiers(toolkit: Any, name: str, inputs: set[str]) -> None:
    schema = _tool(toolkit, name).definition.input_schema

    assert inputs <= _identifiers(schema)
    # Free text is never exempt.
    assert not {"text", "query", "search", "subject"} & _identifiers(schema)


def test_id_fields_of_results_are_declared_identifiers() -> None:
    messages = _tool(telegram_toolkit(), "telegram.list_messages").definition.output_schema
    found = _identifiers(messages)
    assert {"id", "message_id", "reply_to_message_id", "attachment_id"} <= found
    assert "next_page_token" in found
    assert "text" not in found and "display_name" not in found

    mail = _tool(gmail_toolkit(), "gmail.get_message").definition.output_schema
    assert {
        "provider_message_id",
        "thread_id",
        "provider_attachment_id",
        "part_id",
        "content_id",
        "message_id_header",
        "in_reply_to",
        "references",
    } <= _identifiers(mail)


async def _honours_identifiers() -> bool:
    """Whether the installed Runtime's pii guardrail honours ``format: identifier`` (0.2.0b2
    and later). Earlier Runtimes ignore the annotation, which must be harmless."""
    definition = type(
        "Definition",
        (),
        {"input_schema": {"properties": {"id": {"type": "string", "format": "identifier"}}}},
    )()
    decision = await PIIRedactionPolicy().evaluate(
        "before_tool", {"id": CHAT_ID}, {"tool": definition}
    )
    return decision.action == PolicyAction.ALLOW


async def test_a_digit_string_chat_id_passes_the_default_pii_guardrail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not await _honours_identifiers():
        pytest.skip("this Runtime's pii guardrail does not read format: identifier")
    chat = {"id": int(CHAT_ID), "type": "private", "first_name": "Ana"}
    updates = [
        {
            "update_id": 1,
            "message": {
                "message_id": 7,
                "date": 1_792_488_600,
                "chat": chat,
                "from": {"id": int(CHAT_ID), "is_bot": False, "first_name": "Ana"},
                "text": "my Aadhaar is 2345 6789 0124",
            },
        }
    ]
    monkeypatch.setattr(test_telegram, "UPDATES", updates)
    toolkit = telegram_toolkit(client=test_telegram.BotApi().factory())
    registry = ToolRegistry()
    for tool in toolkit.tools:
        registry.register(tool)
    listing = registry.get("telegram.list_messages")
    executor = ToolExecutor(registry, CompositePolicyEngine())

    result = await executor.execute(
        "telegram.list_messages", {"conversation_id": CHAT_ID}, fake_tool_context(listing)
    )

    [message] = result.value["messages"]
    assert message["conversation"]["id"] == CHAT_ID
    assert message["sender"]["id"] == CHAT_ID
    # The message text is still screened.
    assert message["text"] == "my Aadhaar is [REDACTED_AADHAAR]"
