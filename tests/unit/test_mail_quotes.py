"""Quoted history and signatures split off a reply, and every inline text part kept."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any

import httpx
import pytest
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import SecretStr

from algen_agent_tools.gmail import GmailMailboxClient, GmailOAuthSettings, gmail_toolkit
from algen_agent_tools.imap import FakeImapServer, ImapMailboxClient, ImapSettings
from algen_agent_tools.mail import FakeMailbox, InboundEmailDetail
from algen_agent_tools.mail.quotes import latest_text

GMAIL = """Sounds good, Thursday works.

On Wed, 4 Nov 2026 at 10:00, Ana Silva <ana@example.com> wrote:
> Can we meet on Thursday?
> Ana"""

WRAPPED = """Yes.

On Wed, 4 Nov 2026 at 10:00, Ana Silva <ana.silva@a-rather-long-domain.example.com>
wrote:
> Is it confirmed?"""

APPLE = """Thanks!

Sent from my iPhone

On 4 Nov 2026, at 10:00, Ana Silva <ana@example.com> wrote:

> Here is the offer."""

OUTLOOK = """Please find the signed copy attached.

Regards,
Bo

________________________________
From: Ana Silva <ana@example.com>
Sent: Wednesday, November 4, 2026 10:00 AM
To: Bo <bo@example.com>
Subject: Contract

Could you sign the contract?"""

ORIGINAL = """Approved.

-----Original Message-----
From: Ana
Please approve the budget."""

GERMAN = """Passt.

Am 04.11.2026 um 10:00 schrieb Ana Silva <ana@example.com>:
> Passt Donnerstag?"""

SIGNATURE = """See you then.

--
Bo Chen
Head of Ops"""

INLINE = """> Can you do Thursday?
Yes, after 2pm.
> And bring the contract?
I will."""

FORWARD = """FYI, see below.

---------- Forwarded message ---------
From: Ana <ana@example.com>
Date: Wed, 4 Nov 2026
Subject: Offer

The offer is attached."""


@pytest.mark.parametrize(
    ("text", "latest"),
    [
        (GMAIL, "Sounds good, Thursday works."),
        (WRAPPED, "Yes."),
        (APPLE, "Thanks!"),
        (OUTLOOK, "Please find the signed copy attached.\n\nRegards,\nBo"),
        (ORIGINAL, "Approved."),
        (GERMAN, "Passt."),
        (SIGNATURE, "See you then."),
        ("Reply.\n\n> quoted\n> lines\n", "Reply."),
    ],
)
def test_quoted_history_and_signatures_are_removed(text: str, latest: str) -> None:
    assert latest_text(text) == (latest, True)


FORWARD_WITH_SIGNATURE = FORWARD.replace(
    "The offer is attached.", "The offer is attached.\n\n-- \nAna Silva"
)


@pytest.mark.parametrize(
    "text", [INLINE, FORWARD, FORWARD_WITH_SIGNATURE, "Just a plain message.\nTwo lines."]
)
def test_inline_replies_forwards_and_plain_text_are_kept(text: str) -> None:
    assert latest_text(text) == (text.strip(), False)


def test_a_message_that_is_only_a_quote_is_returned_whole() -> None:
    text = "On Wed, 4 Nov 2026, Ana <ana@example.com> wrote:\n> Hello"
    assert latest_text(text) == (text, False)


def test_from_alone_does_not_start_history() -> None:
    text = "From: the finance team\nPlease review the numbers."
    assert latest_text(text) == (text, False)


JAPANESE = """承知しました。木曜日で大丈夫です。

________________________________
差出人: Ana Silva <ana@example.com>
送信日時: 2026年11月4日 10:00
宛先: Bo <bo@example.com>
件名: 打ち合わせ

木曜日に打ち合わせできますか。"""

CHINESE = """好的 周四可以。

发件人: Ana Silva <ana@example.com>
发送时间: 2026年11月4日 10:00
收件人: Bo <bo@example.com>
主题: 会议

周四可以开会吗。"""
FULL_WIDTH_COLON = "\uff1a"


@pytest.mark.parametrize(
    ("text", "latest"),
    [
        (JAPANESE, "承知しました。木曜日で大丈夫です。"),
        # Only the subject line follows the sent line: To may be left out.
        (JAPANESE.replace("宛先: Bo <bo@example.com>\n", ""), "承知しました。木曜日で大丈夫です。"),
        (CHINESE, "好的 周四可以。"),
        (CHINESE.replace(": ", FULL_WIDTH_COLON), "好的 周四可以。"),
    ],
)
def test_cjk_outlook_header_blocks_start_history(text: str, latest: str) -> None:
    assert latest_text(text) == (latest, True)


@pytest.mark.parametrize(
    "text",
    [
        "差出人: 経理部\n請求書をご確認ください。",
        f"发件人{FULL_WIDTH_COLON}财务部\n请查看这些数字。",
        "件名: 会議\n差出人: 経理部\n明日です。",
    ],
)
def test_a_cjk_from_line_alone_does_not_start_history(text: str) -> None:
    assert latest_text(text) == (text, False)


# The tools


def detail(index: int, body: str) -> InboundEmailDetail:
    return InboundEmailDetail(
        provider_message_id=f"m{index}", thread_id="t1", received_at_ms=index, text_body=body
    )


async def call(action: str, box: FakeMailbox, **arguments: Any) -> Any:
    toolkit = gmail_toolkit(client=box.factory())
    tool = next(t for t in toolkit.tools if t.definition.name == f"gmail.{action}")
    return await tool.execute(arguments, fake_tool_context(tool))


async def test_get_message_returns_the_whole_body_unless_asked() -> None:
    box = FakeMailbox(inbox=[detail(1, GMAIL)])

    whole = await call("get_message", box, message_id="m1")
    latest = await call("get_message", box, message_id="m1", include="latest")

    assert (whole["text_body"], whole["quoted_text_removed"]) == (GMAIL, False)
    assert (latest["text_body"], latest["quoted_text_removed"]) == (
        "Sounds good, Thursday works.",
        True,
    )


async def test_get_thread_returns_each_senders_own_text_by_default() -> None:
    box = FakeMailbox(inbox=[detail(1, "Can we meet on Thursday?"), detail(2, GMAIL)])

    thread = await call("get_thread", box, thread_id="t1")
    full = await call("get_thread", box, thread_id="t1", include="full")

    assert [m["text_body"] for m in thread["messages"]] == [
        "Can we meet on Thursday?",
        "Sounds good, Thursday works.",
    ]
    assert full["messages"][1]["text_body"] == GMAIL


# Every inline text part


async def test_gmail_joins_every_inline_text_part() -> None:
    def b64(text: str) -> str:
        return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")

    payload = {
        "id": "m1",
        "threadId": "t1",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "Subject", "value": "Photos"}],
            "parts": [
                {"partId": "0", "mimeType": "text/plain", "body": {"data": b64("Before the photo.")}},
                {"partId": "1", "mimeType": "image/png", "filename": "a.png",
                 "body": {"attachmentId": "x", "size": 3}},
                {"partId": "2", "mimeType": "text/plain", "body": {"data": b64("After the photo.")}},
            ],
        },
    }  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(200, json=payload)

    client = GmailMailboxClient(
        GmailOAuthSettings(
            client_id="i", client_secret=SecretStr("s"), refresh_token=SecretStr("r")
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert (await client.get_message("m1")).text_body == "Before the photo.\n\nAfter the photo."


async def test_imap_joins_every_inline_text_part() -> None:
    message = EmailMessage()
    message["From"] = "ana@example.com"
    message["Subject"] = "Photos"
    message.make_mixed()
    for text in ("Before the photo.", "After the photo."):
        part = EmailMessage()
        part.set_content(text)
        message.attach(part)
    server = FakeImapServer()
    server.add(message.as_bytes(policy=SMTP))
    client = ImapMailboxClient(
        ImapSettings(
            host="imap.example.com", username=server.username, password=SecretStr(server.password)
        ),
        imap_factory=server.factory(),
    )

    page = await client.list_messages()
    detail = await client.get_message(page.messages[0].provider_message_id)

    assert detail.text_body == "Before the photo.\n\nAfter the photo."
