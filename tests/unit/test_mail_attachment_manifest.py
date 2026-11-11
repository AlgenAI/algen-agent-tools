"""A complete attachment manifest on Gmail, IMAP and Outlook (#66): every attachment counted,
the cap reported, inline parts flagged and nameless parts listed and downloadable."""

from __future__ import annotations

import base64
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from algen_agent_tools.gmail import GmailMailboxClient, GmailOAuthSettings
from algen_agent_tools.imap import FakeImapServer, ImapMailboxClient, ImapSettings
from algen_agent_tools.mail import FakeMailbox
from algen_agent_tools.mail.inbound import (
    MAX_ATTACHMENTS,
    InboundEmailAttachment,
    InboundEmailSummary,
    generated_filename,
    is_inline_part,
    normalized_content_id,
)
from algen_agent_tools.outlook import OutlookMailboxClient, OutlookSettings

PDF = b"%PDF-1.7 contract"
PNG = b"\x89PNG\r\n\x1a\nlogo"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


# The shared model


def test_generated_names_are_typed_from_the_media_type() -> None:
    assert generated_filename(1, "application/pdf") == "attachment-1.pdf"
    assert generated_filename(2, "IMAGE/PNG; name=x") == "attachment-2.png"
    assert generated_filename(3, "message/rfc822") == "attachment-3.eml"
    assert (
        generated_filename(
            4, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        == "attachment-4.docx"
    )
    assert generated_filename(5, "application/x-unknown") == "attachment-5.bin"


def test_content_ids_lose_their_brackets_and_inline_follows_the_disposition() -> None:
    assert normalized_content_id(" <logo@x>\r\n") == "logo@x"
    assert normalized_content_id("<>") is None
    assert normalized_content_id(None) is None
    assert is_inline_part("inline", None) is True
    assert is_inline_part(None, "logo@x") is True
    assert is_inline_part("attachment", "logo@x") is False
    assert is_inline_part(None, None) is False


def test_a_summary_counts_its_listed_attachments_unless_told_more() -> None:
    attachment = InboundEmailAttachment(provider_attachment_id="1", filename="a.pdf")
    plain = InboundEmailSummary(provider_message_id="m", attachments=(attachment,))
    assert (plain.attachment_count, plain.attachments_truncated) == (1, False)
    more = InboundEmailSummary(
        provider_message_id="m", attachments=(attachment,), attachment_count=3
    )
    assert more.attachments_truncated is True
    with pytest.raises(ValidationError, match="attachment_count"):
        InboundEmailSummary(provider_message_id="m", attachments=(attachment,), attachment_count=0)


async def test_the_fake_mailbox_keeps_the_manifest() -> None:
    attachment = InboundEmailAttachment(
        provider_attachment_id="1", filename="logo.png", is_inline=True, content_id="logo"
    )
    mailbox = FakeMailbox(
        inbox=[
            InboundEmailSummary(
                provider_message_id="m", attachments=(attachment,), attachment_count=25
            )
        ]
    )
    detail = await mailbox.get_message("m")
    assert (detail.attachment_count, detail.attachments_truncated) == (25, True)
    assert detail.attachments[0].is_inline is True


# Gmail


def gmail_part(number: int, **extra: Any) -> dict[str, Any]:
    return {
        "partId": str(number),
        "mimeType": "application/pdf",
        "filename": f"file-{number}.pdf",
        "headers": [],
        "body": {"attachmentId": f"att-{number}", "size": len(PDF)},
        **extra,
    }


def gmail_message(parts: list[dict[str, Any]]) -> dict[str, Any]:
    text = {"partId": "0", "mimeType": "text/plain", "body": {"data": b64(b"Hello")}}
    return {
        "id": "msg-1",
        "threadId": "thread-1",
        "internalDate": "1700000000000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": "From", "value": "ana@example.com"}],
            "parts": [text, *parts],
        },
    }


def gmail(payload: dict[str, Any]) -> GmailMailboxClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if "/attachments/" in request.url.path:
            return httpx.Response(200, json={"data": b64(PDF)})
        return httpx.Response(200, json=payload)

    return GmailMailboxClient(
        GmailOAuthSettings(
            client_id="id", client_secret=SecretStr("s"), refresh_token=SecretStr("r")
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_gmail_reports_attachments_past_the_cap() -> None:
    client = gmail(gmail_message([gmail_part(n) for n in range(1, 22)]))
    detail = await client.get_message("msg-1")
    assert len(detail.attachments) == MAX_ATTACHMENTS == 20
    assert (detail.attachment_count, detail.attachments_truncated) == (21, True)
    assert detail.text_body == "Hello"


async def test_gmail_flags_inline_parts_and_lists_nameless_ones() -> None:
    logo = gmail_part(
        1,
        mimeType="image/png",
        filename="image001.png",
        headers=[
            {"name": "Content-Disposition", "value": 'inline; filename="image001.png"'},
            {"name": "Content-ID", "value": "<image001@01DA>"},
        ],
    )
    inline_pdf = gmail_part(
        2, headers=[{"name": "Content-Disposition", "value": 'inline; filename="file-2.pdf"'}]
    )
    nameless = gmail_part(3, filename="")
    small = gmail_part(4, filename="", body={"data": b64(PDF), "size": len(PDF)})
    client = gmail(gmail_message([logo, inline_pdf, nameless, small]))

    detail = await client.get_message("msg-1")
    rows = [(a.filename, a.is_inline, a.content_id, a.part_id) for a in detail.attachments]
    assert rows == [
        ("image001.png", True, "image001@01DA", "1"),
        ("file-2.pdf", True, None, "2"),
        ("attachment-3.pdf", False, None, "3"),
        ("attachment-4.pdf", False, None, "4"),
    ]
    assert detail.attachment_count == 4
    assert detail.text_body == "Hello"

    upload = await client.get_attachment("msg-1", "3")
    assert (upload.filename, upload.media_type, upload.data) == (
        "attachment-3.pdf",
        "application/pdf",
        PDF,
    )
    # A part small enough for Gmail to return with the message downloads from it.
    upload = await client.get_attachment("msg-1", "4")
    assert (upload.filename, upload.data) == ("attachment-4.pdf", PDF)


# IMAP


def imap(raw: bytes) -> tuple[ImapMailboxClient, FakeImapServer]:
    server = FakeImapServer()
    server.add(raw)
    settings = ImapSettings(
        host="imap.example.com", username="agent@example.com", password=SecretStr("app-password")
    )
    return ImapMailboxClient(settings, imap_factory=server.factory()), server


def mime_with(count: int) -> bytes:
    message = EmailMessage()
    message["From"] = "ana@example.com"
    message["Subject"] = "Many"
    message.set_content("Hello")
    for number in range(1, count + 1):
        message.add_attachment(PDF, maintype="application", subtype="pdf", filename=f"{number}.pdf")
    return message.as_bytes(policy=SMTP)


async def test_imap_reports_attachments_past_the_cap() -> None:
    client, _ = imap(mime_with(21))
    [row] = (await client.list_messages()).messages
    assert len(row.attachments) == 20
    assert (row.attachment_count, row.attachments_truncated) == (21, True)
    detail = await client.get_message(row.provider_message_id)
    assert (detail.attachment_count, detail.attachments_truncated) == (21, True)


async def test_imap_flags_inline_parts_and_lists_nameless_ones() -> None:
    message = EmailMessage()
    message["From"] = "ana@example.com"
    message["Subject"] = "Contract"
    message.set_content("Hello")
    message.add_alternative('<p>Hello <img src="cid:logo@x"></p>', subtype="html")
    html = message.get_payload()[1]
    html.add_related(PNG, maintype="image", subtype="png", cid="<logo@x>")
    message.add_attachment(
        PDF, maintype="application", subtype="pdf", filename="signed.pdf", disposition="inline"
    )
    message.add_attachment(PDF, maintype="application", subtype="pdf")
    del message.get_payload()[-1]["Content-Disposition"]
    client, _ = imap(message.as_bytes(policy=SMTP))

    [row] = (await client.list_messages()).messages
    rows = [(a.filename, a.media_type, a.is_inline, a.content_id) for a in row.attachments]
    assert rows == [
        ("attachment-1.png", "image/png", True, "logo@x"),
        ("signed.pdf", "application/pdf", True, None),
        ("attachment-3.pdf", "application/pdf", False, None),
    ]
    assert row.attachment_count == 3
    detail = await client.get_message(row.provider_message_id)
    assert detail.text_body == "Hello"

    nameless = row.attachments[2]
    assert nameless.part_id is not None
    upload = await client.get_attachment(row.provider_message_id, nameless.part_id)
    assert (upload.filename, upload.media_type, upload.data) == (
        "attachment-3.pdf",
        "application/pdf",
        PDF,
    )


# Outlook


class Graph:
    def __init__(self, attachments: list[dict[str, Any]], *, has_attachments: bool = True) -> None:
        self.attachments = attachments
        self.message = {
            "id": "m1",
            "conversationId": "conv-1",
            "subject": "Contract",
            "receivedDateTime": "2026-10-20T09:30:00Z",
            "hasAttachments": has_attachments,
            "body": {"contentType": "text", "content": "Hello"},
        }
        self.paths: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        path = request.url.path
        self.paths.append(path)
        if path.endswith("/attachments"):
            listed = [
                {key: value for key, value in item.items() if key != "contentBytes"}
                for item in self.attachments
            ]
            return httpx.Response(200, json={"value": listed})
        if "/attachments/" in path:
            wanted = path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=next(a for a in self.attachments if a["id"] == wanted))
        if path.endswith("/messages"):
            return httpx.Response(200, json={"value": [self.message]})
        return httpx.Response(200, json=self.message)

    def client(self) -> OutlookMailboxClient:
        return OutlookMailboxClient(
            OutlookSettings(
                tenant_id="contoso.onmicrosoft.com",
                client_id="client",
                client_secret=SecretStr("client-secret"),
                refresh_token=SecretStr("refresh-secret"),
                mailbox="agent@contoso.com",
            ),
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )


def graph_file(number: int, **extra: Any) -> dict[str, Any]:
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "id": f"att-{number}",
        "name": f"file-{number}.pdf",
        "contentType": "application/pdf",
        "size": len(PDF),
        "isInline": False,
        "contentBytes": base64.b64encode(PDF).decode(),
        **extra,
    }


async def test_outlook_reports_attachments_past_the_cap() -> None:
    client = Graph([graph_file(n) for n in range(1, 22)]).client()
    [row] = (await client.list_messages()).messages
    assert len(row.attachments) == 20
    assert (row.attachment_count, row.attachments_truncated) == (21, True)


async def test_outlook_flags_inline_parts_and_lists_nameless_ones() -> None:
    graph = Graph(
        [
            graph_file(
                1,
                name="image001.png",
                contentType="image/png",
                isInline=True,
                contentId="image001@01DA",
            ),
            graph_file(2, isInline=True),
            graph_file(3, name=""),
        ]
    )
    client = graph.client()
    detail = await client.get_message("m1")
    rows = [(a.filename, a.is_inline, a.content_id) for a in detail.attachments]
    assert rows == [
        ("image001.png", True, "image001@01DA"),
        ("file-2.pdf", True, None),
        ("attachment-3.pdf", False, None),
    ]
    upload = await client.get_attachment("m1", "att-3")
    assert (upload.filename, upload.media_type, upload.data) == (
        "attachment-3.pdf",
        "application/pdf",
        PDF,
    )


async def test_outlook_reads_inline_only_attachments_graph_does_not_flag() -> None:
    # Graph sets hasAttachments false when every attachment is inline.
    graph = Graph([graph_file(1, isInline=True)], has_attachments=False)
    client = graph.client()
    [row] = (await client.list_messages()).messages
    assert row.attachments == ()  # a listing keeps to one request per message
    detail = await client.get_message("m1")
    assert [(a.filename, a.is_inline) for a in detail.attachments] == [("file-1.pdf", True)]


async def test_outlook_marks_a_paged_attachment_listing_truncated() -> None:
    pages = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        pages["count"] += 1
        return httpx.Response(
            200,
            json={
                "value": [graph_file(pages["count"])],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages/m1/attachments"
                f"?$skip={pages['count']}",
            },
        )

    client = OutlookMailboxClient(
        OutlookSettings(
            tenant_id="contoso.onmicrosoft.com",
            client_id="client",
            client_secret=SecretStr("client-secret"),
            refresh_token=SecretStr("refresh-secret"),
            mailbox="agent@contoso.com",
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    manifest = await client._attachments("m1")
    assert pages["count"] == 5
    assert (manifest["attachment_count"], manifest["attachments_truncated"]) == (5, True)
