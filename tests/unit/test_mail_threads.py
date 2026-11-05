"""<mailbox>.get_thread and the threading headers, on every mailbox transport's fake."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any

import httpx
import pytest
from algen_agent_runtime.tools.contracts import Tool
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import SecretStr, ValidationError

from algen_agent_tools.gmail import GmailMailboxClient, GmailOAuthSettings
from algen_agent_tools.imap import FakeImapServer, ImapMailboxClient, ImapSettings, imap_toolkit
from algen_agent_tools.mail import (
    EmailDeliveryError,
    FakeMailbox,
    InboundEmailDetail,
)
from algen_agent_tools.outlook import OutlookMailboxClient, OutlookSettings
from algen_agent_tools.testing import assert_toolkit_contract

ROOT, REPLY, SECOND = "<root@example.com>", "<reply@example.com>", "<second@example.com>"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


# Gmail


def gmail_message(index: int, message_id: str, refs: str = "") -> dict[str, Any]:
    headers = [
        {"name": "From", "value": "ana@example.com"},
        {"name": "Subject", "value": "Offer"},
        {"name": "Message-ID", "value": message_id},
    ]
    if refs:
        headers += [
            {"name": "In-Reply-To", "value": refs.split()[-1]},
            {"name": "References", "value": refs},
        ]
    return {
        "id": f"m{index}",
        "threadId": "t1",
        "internalDate": str(1_700_000_000_000 + index),
        "payload": {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": b64(f"Message {index}".encode())},
        },
    }


def gmail(thread: dict[str, Any], status: int = 200) -> tuple[GmailMailboxClient, list[str]]:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        paths.append(request.url.raw_path.decode())
        return httpx.Response(status, json=thread)

    client = GmailMailboxClient(
        GmailOAuthSettings(
            client_id="id", client_secret=SecretStr("s"), refresh_token=SecretStr("r")
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return client, paths


GMAIL_THREAD = {
    "id": "t1",
    "messages": [
        gmail_message(1, ROOT),
        gmail_message(2, REPLY, ROOT),
        gmail_message(3, SECOND, f"{ROOT} {REPLY}"),
    ],
}


async def test_gmail_reads_a_thread_oldest_first_with_its_headers() -> None:
    client, paths = gmail(GMAIL_THREAD)

    thread = await client.get_thread("t1")

    assert [m.text_body for m in thread.messages] == ["Message 1", "Message 2", "Message 3"]
    last = thread.messages[-1]
    assert (last.message_id_header, last.in_reply_to, last.references) == (
        SECOND,
        REPLY,
        (ROOT, REPLY),
    )
    assert thread.truncated is False
    assert paths == ["/gmail/v1/users/me/threads/t1?format=full"]


async def test_a_long_gmail_thread_keeps_its_newest_messages() -> None:
    client, _ = gmail(GMAIL_THREAD)
    thread = await client.get_thread("t1", max_messages=2)
    assert [m.provider_message_id for m in thread.messages] == ["m2", "m3"]
    assert thread.truncated is True


async def test_gmail_get_message_carries_the_threading_headers() -> None:
    client, _ = gmail(gmail_message(2, REPLY, ROOT))
    detail = await client.get_message("m2")
    assert (detail.message_id_header, detail.in_reply_to, detail.references) == (
        REPLY,
        ROOT,
        (ROOT,),
    )


async def test_an_unknown_gmail_thread_is_a_clear_error() -> None:
    client, _ = gmail({"error": {"message": "secret detail"}}, status=404)
    with pytest.raises(EmailDeliveryError) as raised:
        await client.get_thread("other")
    assert str(raised.value) == "Gmail thread read failed (HTTPStatusError)"


# Outlook


def graph_message(index: int, message_id: str, refs: str = "") -> dict[str, Any]:
    headers = [{"name": "In-Reply-To", "value": refs.split()[-1]}] if refs else []
    if refs:
        headers.append({"name": "References", "value": refs})
    return {
        "id": f"o{index}",
        "conversationId": "conv-1",
        "from": {"emailAddress": {"address": "ana@contoso.com"}},
        "subject": "Offer",
        "receivedDateTime": f"2026-10-2{index}T09:00:00Z",
        "body": {"contentType": "text", "content": f"Message {index}"},
        "internetMessageId": message_id,
        "internetMessageHeaders": headers,
    }


def outlook(value: list[dict[str, Any]], **extra: Any) -> tuple[OutlookMailboxClient, list[Any]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        requests.append(request)
        return httpx.Response(200, json={"value": value, **extra})

    client = OutlookMailboxClient(
        OutlookSettings(
            tenant_id="contoso.onmicrosoft.com",
            client_id="client",
            client_secret=SecretStr("secret"),
            refresh_token=SecretStr("refresh"),
            mailbox="agent@contoso.com",
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return client, requests


async def test_outlook_filters_by_conversation_and_sorts_by_date() -> None:
    newest_first = [
        graph_message(3, SECOND, f"{ROOT} {REPLY}"),
        graph_message(1, ROOT),
        graph_message(2, REPLY, ROOT),
    ]
    client, requests = outlook(newest_first)

    thread = await client.get_thread("conv-1")

    assert [m.provider_message_id for m in thread.messages] == ["o1", "o2", "o3"]
    assert thread.messages[2].references == (ROOT, REPLY)
    assert thread.messages[0].message_id_header == ROOT
    params = requests[0].url.params
    assert params["$filter"] == "conversationId eq 'conv-1'"
    assert "$orderby" not in params
    assert requests[0].headers["Prefer"] == 'outlook.body-content-type="text"'


async def test_more_outlook_messages_than_one_page_marks_the_thread_truncated() -> None:
    client, _ = outlook([graph_message(1, ROOT)], **{"@odata.nextLink": "https://graph"})
    assert (await client.get_thread("conv-1")).truncated is True


async def test_an_outlook_thread_id_cannot_break_out_of_the_filter() -> None:
    client, requests = outlook([])
    with pytest.raises(ValueError, match="thread_id"):
        await client.get_thread("x' or true or conversationId eq 'y")
    assert requests == []


async def test_an_empty_outlook_conversation_is_a_clear_error() -> None:
    client, _ = outlook([])
    with pytest.raises(EmailDeliveryError, match="no messages"):
        await client.get_thread("conv-1")


# IMAP


def mime(message_id: str, refs: str = "", body: str = "Hi") -> bytes:
    message = EmailMessage()
    message["From"] = "ana@example.com"
    message["To"] = "hr@example.com"
    message["Subject"] = "Offer"
    message["Message-ID"] = message_id
    if refs:
        message["In-Reply-To"] = refs.split()[-1]
        message["References"] = refs
    message.set_content(body)
    return message.as_bytes(policy=SMTP)


def imap(server: FakeImapServer, folders: tuple[str, ...] = ("INBOX",)) -> ImapMailboxClient:
    return ImapMailboxClient(
        ImapSettings(
            host="imap.example.com",
            username=server.username,
            password=SecretStr(server.password),
            folders=folders,
        ),
        imap_factory=server.factory(),
    )


def plain_server() -> FakeImapServer:
    server = FakeImapServer(folders={"INBOX": [], "Sent": []})
    server.add(mime(ROOT, body="First"), internaldate="01-Oct-2026 09:00:00 +0000")
    server.add(
        mime(REPLY, ROOT, body="Second"), folder="Sent", internaldate="02-Oct-2026 09:00:00 +0000"
    )
    server.add(
        mime(SECOND, f"{ROOT} {REPLY}", body="Third"), internaldate="03-Oct-2026 09:00:00 +0000"
    )
    server.add(mime("<other@example.com>", body="Unrelated"))
    return server


async def test_plain_imap_threads_by_references_across_folders() -> None:
    client = imap(plain_server(), ("INBOX", "Sent"))

    page = await client.list_messages()
    root_thread = page.messages[-1].thread_id
    assert root_thread is not None and root_thread.startswith("r.")

    thread = await client.get_thread(root_thread)

    assert [m.text_body for m in thread.messages] == ["First", "Second", "Third"]
    assert thread.messages[2].in_reply_to == REPLY
    assert {m.thread_id for m in thread.messages} == {root_thread}
    unrelated = next(m for m in page.messages if m.snippet == "Unrelated")
    assert unrelated.thread_id != root_thread


async def test_plain_imap_reads_only_the_configured_folders() -> None:
    client = imap(plain_server(), ("INBOX",))
    page = await client.list_messages()
    thread = await client.get_thread(page.messages[-1].thread_id or "")
    assert [m.text_body for m in thread.messages] == ["First", "Third"]


async def test_plain_imap_get_message_carries_the_threading_headers() -> None:
    client = imap(plain_server())
    page = await client.list_messages()
    newest_thread_member = next(m for m in page.messages if m.snippet.startswith("Third"))
    detail = await client.get_message(newest_thread_member.provider_message_id)
    assert (detail.message_id_header, detail.references) == (SECOND, (ROOT, REPLY))


@pytest.mark.parametrize("thread_id", ["r.bm90LWFuLWlk", "18c0000000000001", "x"])
async def test_a_thread_id_that_is_not_this_mailboxs_is_an_error(thread_id: str) -> None:
    with pytest.raises(EmailDeliveryError):
        await imap(plain_server()).get_thread(thread_id)


async def test_gmail_imap_threads_by_x_gm_thrid_and_counts_a_message_once() -> None:
    server = FakeImapServer(gmail=True, folders={"INBOX": [], "[Gmail]/All Mail": []})
    first = server.add(mime(ROOT, body="First"), internaldate="01-Oct-2026 09:00:00 +0000")
    thread_number = first.gmail_thread_id
    server.add(
        mime(REPLY, ROOT, body="Second"),
        internaldate="02-Oct-2026 09:00:00 +0000",
        gmail_thread_id=thread_number,
    )
    # The same reply under another label.
    server.add(
        mime(REPLY, ROOT, body="Second"),
        folder="[Gmail]/All Mail",
        internaldate="02-Oct-2026 09:00:00 +0000",
        gmail_thread_id=thread_number,
    )
    client = imap(server, ("INBOX", "[Gmail]/All Mail"))

    thread = await client.get_thread(format(thread_number or 0, "x"))

    assert [m.text_body for m in thread.messages] == ["First", "Second"]
    assert any("X-GM-THRID" in command for command in server.commands)


# The tool


def thread_tool(box: FakeMailbox) -> Tool:
    from algen_agent_tools.gmail import gmail_toolkit

    toolkit = gmail_toolkit(client=box.factory())
    assert_toolkit_contract(toolkit)
    return next(t for t in toolkit.tools if t.definition.name == "gmail.get_thread")


def row(index: int, thread_id: str = "t1") -> InboundEmailDetail:
    return InboundEmailDetail(
        provider_message_id=f"m{index}",
        thread_id=thread_id,
        received_at_ms=index,
        text_body="x" * 500,
    )


async def test_the_tool_bounds_messages_and_characters() -> None:
    box = FakeMailbox(inbox=[row(3), row(1), row(2), row(4, "t2")])
    tool = thread_tool(box)

    result = await tool.execute(
        {"thread_id": "t1", "max_messages": 2, "max_chars_per_message": 100},
        fake_tool_context(tool),
    )

    assert [m["provider_message_id"] for m in result["messages"]] == ["m2", "m3"]
    assert {len(m["text_body"]) for m in result["messages"]} == {100}
    assert result["truncated"] is True


@pytest.mark.parametrize(
    "arguments",
    [
        {"thread_id": "a/b"},
        {"thread_id": "t1", "max_messages": 51},
        {"thread_id": "t1", "max_chars_per_message": 10_001},
    ],
)
async def test_the_tool_refuses_inputs_outside_its_limits(arguments: dict[str, Any]) -> None:
    tool = thread_tool(FakeMailbox())
    with pytest.raises(ValidationError):
        await tool.execute(arguments, fake_tool_context(tool))


def test_every_imap_toolkit_offers_get_thread() -> None:
    names = {t.definition.name for t in imap_toolkit().tools}
    assert "imap.get_thread" in names
