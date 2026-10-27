"""Reading one Gmail message and saving its attachments, against a mocked Gmail API."""

from __future__ import annotations

import base64
from typing import Any

import httpx
import pytest
from algen_agent_runtime.tools.authoring import Toolkit
from algen_agent_runtime.tools.contracts import SideEffect, Tool, ToolContext
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import SecretStr, ValidationError

from algen_agent_tools.attachments import (
    AttachmentPolicy,
    AttachmentRef,
    AttachmentRefused,
    AttachmentUpload,
)
from algen_agent_tools.gmail import GmailMailboxClient, GmailOAuthSettings, gmail_toolkit
from algen_agent_tools.mail import (
    GMAIL_AUTHSERV_ID,
    EmailAddress,
    EmailDeliveryError,
    MailClient,
    html_to_text,
)
from algen_agent_tools.testing import assert_toolkit_contract

PDF = b"%PDF-1.7 report"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def message(*, plain: str | None = "Hello,\r\nsee attached.", html: str | None = None) -> Any:
    alternatives = []
    if plain is not None:
        alternatives.append(
            {"partId": "0.0", "mimeType": "text/plain", "body": {"data": b64(plain.encode())}}
        )
    if html is not None:
        alternatives.append(
            {"partId": "0.1", "mimeType": "text/html", "body": {"data": b64(html.encode())}}
        )
    return {
        "id": "msg-1",
        "threadId": "thread-1",
        "internalDate": "1700000000000",
        "snippet": "Hello, see attached.",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Ana <ana@example.com>"},
                {"name": "To", "value": "agent@example.com"},
                {"name": "Cc", "value": "team@example.com"},
                {"name": "Subject", "value": "Report"},
            ],
            "parts": [
                {"partId": "0", "mimeType": "multipart/alternative", "parts": alternatives},
                {
                    "partId": "1",
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    # Gmail issues a different attachmentId on every read; the part id is stable.
                    "body": {"attachmentId": "fresh-id", "size": len(PDF)},
                },
            ],
        },
    }


class GmailApi:
    def __init__(self, payload: Any, attachment: bytes = PDF) -> None:
        self.payload = payload
        self.attachment = attachment
        self.paths: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        self.paths.append(request.url.raw_path.decode())
        if "/attachments/" in request.url.path:
            return httpx.Response(200, json={"data": b64(self.attachment)})
        return httpx.Response(200, json=self.payload)

    def client(self) -> GmailMailboxClient:
        return GmailMailboxClient(
            GmailOAuthSettings(
                client_id="id", client_secret=SecretStr("s"), refresh_token=SecretStr("r")
            ),
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )

    def factory(self) -> Any:
        async def open_client(secrets: Any) -> MailClient:
            return MailClient(EmailAddress(address="agent@example.com"), self.client())

        return open_client


def named(toolkit: Toolkit, action: str) -> Tool:
    return next(tool for tool in toolkit.tools if tool.definition.name == f"gmail.{action}")


async def test_get_message_returns_headers_plain_text_and_stable_attachment_ids() -> None:
    api = GmailApi(message(html="<p>ignored when plain text exists</p>"))
    tool = named(gmail_toolkit(client=api.factory()), "get_message")
    assert tool.definition.side_effect is SideEffect.READ
    assert tool.definition.required_permissions == frozenset({"gmail.read"})

    detail = await tool.execute({"message_id": "msg-1"}, fake_tool_context(tool))

    assert detail["sender"] == "Ana <ana@example.com>"
    assert detail["cc"] == "team@example.com"
    assert detail["text_body"] == "Hello,\nsee attached."
    assert detail["body_truncated"] is False
    [attachment] = detail["attachments"]
    assert attachment["part_id"] == "1"
    assert attachment["filename"] == "report.pdf"
    assert api.paths == ["/gmail/v1/users/me/messages/msg-1?format=full"]


async def test_get_message_falls_back_to_html_and_cuts_long_bodies() -> None:
    html = "<html><style>p{}</style><body><p>Line one</p><p>" + "x" * 500 + "</p></body></html>"
    api = GmailApi(message(plain=None, html=html))
    tool = named(gmail_toolkit(client=api.factory()), "get_message")

    detail = await tool.execute({"message_id": "msg-1", "max_chars": 100}, fake_tool_context(tool))

    assert detail["text_body"].startswith("Line one\n\nxxx")
    assert len(detail["text_body"]) == 100
    assert detail["body_truncated"] is True


@pytest.mark.parametrize("message_id", ["..", "a/b", ".hidden", "a b", ""])
async def test_ids_that_could_change_the_api_path_are_refused(message_id: str) -> None:
    api = GmailApi(message())
    tool = named(gmail_toolkit(client=api.factory()), "get_message")
    with pytest.raises(ValidationError, match="message_id"):
        await tool.execute({"message_id": message_id}, fake_tool_context(tool))
    assert api.paths == []


async def test_the_client_encodes_ids_into_a_single_path_segment() -> None:
    api = GmailApi(message())
    await api.client().get_message("a/../b")
    assert api.paths == ["/gmail/v1/users/me/messages/a%2F..%2Fb?format=full"]


async def test_read_failures_name_only_the_cause_type() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(404, json={"error": {"message": "secret detail"}})

    gmail = GmailMailboxClient(
        GmailOAuthSettings(
            client_id="id", client_secret=SecretStr("s"), refresh_token=SecretStr("r")
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(broken)),
    )
    with pytest.raises(EmailDeliveryError) as raised:
        await gmail.get_message("msg-1")
    assert str(raised.value) == "Gmail message read failed (HTTPStatusError)"


def sink_into(stored: list[tuple[AttachmentUpload, ToolContext]]) -> Any:
    async def sink(upload: AttachmentUpload, context: ToolContext) -> AttachmentRef:
        stored.append((upload, context))
        return AttachmentRef(
            artifact_id="art-1", filename=upload.filename, media_type=upload.media_type
        )

    return sink


def test_get_attachment_is_offered_only_with_a_sink() -> None:
    assert "gmail.get_attachment" not in {t.definition.name for t in gmail_toolkit().tools}
    toolkit = gmail_toolkit(artifact_sink=sink_into([]))
    assert_toolkit_contract(toolkit)
    tool = named(toolkit, "get_attachment")
    assert tool.definition.side_effect is SideEffect.READ
    assert tool.definition.required_permissions == frozenset({"gmail.read"})


@pytest.mark.parametrize("attachment_id", ["1", "fresh-id"])
async def test_get_attachment_saves_the_file_with_the_messages_name_and_type(
    attachment_id: str,
) -> None:
    api = GmailApi(message())
    stored: list[tuple[AttachmentUpload, ToolContext]] = []
    tool = named(
        gmail_toolkit(client=api.factory(), artifact_sink=sink_into(stored)), "get_attachment"
    )
    context = fake_tool_context(tool)

    result = await tool.execute({"message_id": "msg-1", "attachment_id": attachment_id}, context)

    assert result == {
        "artifact_id": "art-1",
        "filename": "report.pdf",
        "media_type": "application/pdf",
    }
    assert stored == [(AttachmentUpload("report.pdf", "application/pdf", PDF), context)]
    assert api.paths[-1] == "/gmail/v1/users/me/messages/msg-1/attachments/fresh-id"


async def test_get_attachment_refuses_unknown_attachments_and_policy_breaks() -> None:
    stored: list[tuple[AttachmentUpload, ToolContext]] = []
    api = GmailApi(message())
    tool = named(
        gmail_toolkit(client=api.factory(), artifact_sink=sink_into(stored)), "get_attachment"
    )
    with pytest.raises(EmailDeliveryError, match="no such attachment"):
        await tool.execute({"message_id": "msg-1", "attachment_id": "9"}, fake_tool_context(tool))

    disguised = GmailApi(message(), attachment=b"MZ\x90\x00 not a pdf")
    tool = named(
        gmail_toolkit(client=disguised.factory(), artifact_sink=sink_into(stored)), "get_attachment"
    )
    with pytest.raises(AttachmentRefused, match="does not match"):
        await tool.execute({"message_id": "msg-1", "attachment_id": "1"}, fake_tool_context(tool))

    small = named(
        gmail_toolkit(
            client=api.factory(),
            artifact_sink=sink_into(stored),
            attachment_policy=AttachmentPolicy(media_types=("image/png",)),
        ),
        "get_attachment",
    )
    with pytest.raises(AttachmentRefused, match="not allowed"):
        await small.execute({"message_id": "msg-1", "attachment_id": "1"}, fake_tool_context(small))
    assert stored == []


def test_html_to_text_drops_markup_scripts_and_blank_runs() -> None:
    html = "<div>Hi&nbsp;<b>there</b></div><script>alert(1)</script><br><br><br><p>Bye</p>"
    assert html_to_text(html) == "Hi\xa0there\n\nBye"


async def test_the_fake_mailbox_serves_messages_and_attachments() -> None:
    from algen_agent_tools.mail import FakeMailbox, InboundEmailDetail

    mailbox = FakeMailbox(
        inbox=[InboundEmailDetail(provider_message_id="m1", subject="Hi", text_body="x" * 300)],
        files={("m1", "1"): AttachmentUpload("a.pdf", "application/pdf", PDF)},
    )
    stored: list[tuple[AttachmentUpload, ToolContext]] = []
    toolkit = gmail_toolkit(client=mailbox.factory(), artifact_sink=sink_into(stored))
    read = named(toolkit, "get_message")
    detail = await read.execute({"message_id": "m1", "max_chars": 100}, fake_tool_context(read))
    assert (detail["subject"], len(detail["text_body"]), detail["body_truncated"]) == (
        "Hi",
        100,
        True,
    )
    with pytest.raises(EmailDeliveryError):
        await read.execute({"message_id": "m2"}, fake_tool_context(read))

    save = named(toolkit, "get_attachment")
    await save.execute({"message_id": "m1", "attachment_id": "1"}, fake_tool_context(save))
    assert stored[0][0].filename == "a.pdf"


def with_auth(*values: str) -> Any:
    payload = message()
    payload["payload"]["headers"] = [
        *({"name": "Authentication-Results", "value": value} for value in values),
        *payload["payload"]["headers"],
    ]
    return payload


async def test_gmail_reports_its_own_verdict_on_the_sender() -> None:
    api = GmailApi(with_auth(f"{GMAIL_AUTHSERV_ID}; dmarc=fail (p=REJECT) header.from=bank.com"))
    tool = named(gmail_toolkit(client=api.factory()), "get_message")
    detail = await tool.execute({"message_id": "msg-1"}, fake_tool_context(tool))
    assert detail["sender_auth"] == "fail"


async def test_gmail_without_the_header_is_unknown() -> None:
    api = GmailApi(message())
    tool = named(gmail_toolkit(client=api.factory()), "get_message")
    detail = await tool.execute({"message_id": "msg-1"}, fake_tool_context(tool))
    assert detail["sender_auth"] == "unknown"
