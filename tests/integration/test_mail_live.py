"""Send real email through SMTP and Gmail. Opt-in only.

Run with ``ALGEN_TOOLS_LIVE=1``, the connector's ``ALGEN_TOOLS_SMTP_*`` or
``ALGEN_TOOLS_GMAIL_*`` variables, and ``ALGEN_TOOLS_LIVE_RECIPIENT`` set to a mailbox you own.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable

import pytest
from algen_agent_runtime.tools.authoring import Toolkit
from algen_agent_runtime.tools.testing import fake_tool_context

from algen_agent_tools.gmail import gmail_toolkit
from algen_agent_tools.smtp import smtp_toolkit

pytestmark = pytest.mark.live


def _needs(*names: str) -> None:
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        pytest.skip(f"set {', '.join(missing)}")


@pytest.mark.parametrize(
    ("factory", "required"),
    [
        (smtp_toolkit, ("ALGEN_TOOLS_SMTP_HOST", "ALGEN_TOOLS_SMTP_SENDER")),
        (gmail_toolkit, ("ALGEN_TOOLS_GMAIL_REFRESH_TOKEN", "ALGEN_TOOLS_GMAIL_SENDER")),
    ],
)
async def test_send_one_real_email(
    factory: Callable[[], Toolkit], required: tuple[str, ...]
) -> None:
    _needs("ALGEN_TOOLS_LIVE_RECIPIENT", *required)
    toolkit = factory()
    send = next(tool for tool in toolkit.tools if tool.definition.name.endswith(".send_email"))
    try:
        receipt = await send.execute(
            {
                "to": [{"address": os.environ["ALGEN_TOOLS_LIVE_RECIPIENT"]}],
                "subject": f"algen-agent-tools live test {uuid.uuid4().hex[:8]}",
                "text_body": "Sent by the algen-agent-tools live test suite.",
            },
            fake_tool_context(send),
        )
    finally:
        await toolkit.aclose()
    assert receipt["status"] == "accepted"


async def test_gmail_lists_the_inbox() -> None:
    _needs("ALGEN_TOOLS_GMAIL_REFRESH_TOKEN", "ALGEN_TOOLS_GMAIL_SENDER")
    toolkit = gmail_toolkit()
    list_messages = toolkit.tools[0]
    try:
        page = await list_messages.execute({"max_results": 1}, fake_tool_context(list_messages))
    finally:
        await toolkit.aclose()
    assert page["result_size_estimate"] >= 0
