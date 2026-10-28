"""Read a real mailbox over IMAP. Opt-in only.

Run with ``ALGEN_TOOLS_LIVE=1`` and the connector's ``ALGEN_TOOLS_IMAP_HOST``,
``ALGEN_TOOLS_IMAP_USERNAME`` and ``ALGEN_TOOLS_IMAP_PASSWORD`` (an app password). The test
reads only; it checks that a message it opened is still unread when it started unread.
"""

from __future__ import annotations

import os

import pytest
from algen_agent_runtime.tools.testing import fake_tool_context

from algen_agent_tools.imap import imap_toolkit

pytestmark = pytest.mark.live

REQUIRED = ("ALGEN_TOOLS_IMAP_HOST", "ALGEN_TOOLS_IMAP_USERNAME", "ALGEN_TOOLS_IMAP_PASSWORD")


async def test_list_and_read_without_marking_seen() -> None:
    missing = [name for name in REQUIRED if not os.getenv(name)]
    if missing:
        pytest.skip(f"set {', '.join(missing)}")
    toolkit = imap_toolkit()
    tools = {tool.definition.name: tool for tool in toolkit.tools}
    listing, reading = tools["imap.list_messages"], tools["imap.get_message"]
    try:
        page = await listing.execute(
            {"unread_only": True, "max_results": 1}, fake_tool_context(listing)
        )
        if not page["messages"]:
            pytest.skip("the mailbox has no unread message to read")
        [row] = page["messages"]
        detail = await reading.execute(
            {"message_id": row["provider_message_id"], "max_chars": 500},
            fake_tool_context(reading),
        )
        assert detail["provider_message_id"] == row["provider_message_id"]
        again = await listing.execute(
            {"unread_only": True, "max_results": 1}, fake_tool_context(listing)
        )
    finally:
        await toolkit.aclose()
    assert again["messages"][0]["provider_message_id"] == row["provider_message_id"]
