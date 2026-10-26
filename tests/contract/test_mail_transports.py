"""The SMTP and Gmail transports moved from the Runtime keep its email contract."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from algen_agent_tools.gmail import GmailMailboxClient, GmailOAuthSettings
from algen_agent_tools.mail import (
    EmailAttachmentReference,
    EmailDeliveryError,
    EmailDeliveryStatus,
    EmailMessage,
)
from algen_agent_tools.smtp import SmtpEmailSender, SmtpSettings


def message(**updates: Any) -> EmailMessage:
    values: dict[str, Any] = {
        "sender": {"address": "notifications@example.org", "display_name": "Example"},
        "to": [{"address": "person@example.com"}],
        "subject": "Order update",
        "text_body": "Your order has shipped.",
    }
    values.update(updates)
    return EmailMessage.model_validate(values)


def test_messages_reject_header_injection_duplicate_recipients_and_paths() -> None:
    with pytest.raises(ValidationError, match="line breaks"):
        message(subject="Order update\nBcc: attacker@example.com")
    with pytest.raises(ValidationError, match="unique"):
        message(cc=[{"address": "person@example.com"}])
    with pytest.raises(ValidationError, match="must not contain a path"):
        EmailAttachmentReference(
            artifact_id="artifact-1", filename="../invoice.pdf", media_type="application/pdf"
        )
    with pytest.raises(ValidationError, match="plaintext SMTP"):
        SmtpSettings(host="smtp.example.com", security="plain")
    with pytest.raises(ValidationError, match="header name is invalid"):
        message(headers={"X-Valid: Bcc": "attacker@example.com"})
    with pytest.raises(ValidationError, match="media_type"):
        EmailAttachmentReference(
            artifact_id="artifact-1", filename="a.txt", media_type="text/plain\r\nX-Evil: yes"
        )
    with pytest.raises(ValidationError, match="message ID domain"):
        SmtpSettings(host="smtp.example.com", message_id_domain="mail.example.com\r\nBcc")
    with pytest.raises(ValidationError, match="text_body or html_body"):
        message(text_body=None)


class FakeSmtpClient:
    def __init__(self, *, refused: dict[str, tuple[int, bytes]] | None = None) -> None:
        self.refused = refused or {}
        self.messages: list[Any] = []
        self.recipients: tuple[str, ...] = ()
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.closed = False

    def ehlo(self) -> None:
        pass

    def starttls(self, *, context: Any) -> None:
        self.started_tls = context is not None

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def send_message(self, value: Any, *, to_addrs: tuple[str, ...]) -> Any:
        self.messages.append(value)
        self.recipients = to_addrs
        return self.refused

    def quit(self) -> None:
        self.closed = True

    def close(self) -> None:
        self.closed = True


async def test_smtp_uses_artifact_references_bcc_and_a_stable_message_id() -> None:
    client = FakeSmtpClient()

    async def load_artifact(reference: EmailAttachmentReference) -> bytes:
        assert reference.artifact_id == "artifact-1"
        return b"synthetic invoice"

    sender = SmtpEmailSender(
        SmtpSettings(
            host="smtp.example.com",
            username="smtp-user",
            password=SecretStr("smtp-password"),
            message_id_domain="mail.example.org",
        ),
        artifact_loader=load_artifact,
        smtp_factory=lambda settings: client,
    )
    value = message(
        bcc=[{"address": "audit@example.com"}],
        attachments=[
            {"artifact_id": "artifact-1", "filename": "invoice.txt", "media_type": "text/plain"}
        ],
    )

    first = await sender.send(value, idempotency_key="order:42:shipped")
    second = await sender.send(value, idempotency_key="order:42:shipped")

    assert first.status is EmailDeliveryStatus.ACCEPTED
    assert first.provider_message_id == second.provider_message_id
    assert first.provider_message_id.endswith("@mail.example.org>")
    assert "audit@example.com" in client.recipients
    assert client.messages[0].get("Bcc") is None
    assert client.messages[0].get_payload()[-1].get_filename() == "invoice.txt"
    assert client.started_tls is True
    assert client.logged_in == ("smtp-user", "smtp-password")
    assert client.closed is True


async def test_smtp_reports_refused_recipients() -> None:
    client = FakeSmtpClient(refused={"person@example.com": (550, b"no such user")})
    sender = SmtpEmailSender(
        SmtpSettings(host="localhost", security="plain"), smtp_factory=lambda settings: client
    )

    receipt = await sender.send(message(), idempotency_key="key")

    assert receipt.status is EmailDeliveryStatus.REJECTED
    assert receipt.rejected_recipients == {"person@example.com": "SMTP 550"}


async def test_smtp_attachment_requires_a_loader() -> None:
    sender = SmtpEmailSender(
        SmtpSettings(host="localhost", security="plain"),
        smtp_factory=lambda settings: FakeSmtpClient(),
    )
    value = message(
        attachments=[
            {"artifact_id": "artifact-1", "filename": "a.pdf", "media_type": "application/pdf"}
        ]
    )

    with pytest.raises(EmailDeliveryError, match="artifact loader"):
        await sender.send(value, idempotency_key="key")


async def test_smtp_errors_name_the_failure_type_only() -> None:
    def broken(settings: SmtpSettings) -> Any:
        raise OSError("connection refused to smtp.internal:25 with password hunter2")

    sender = SmtpEmailSender(SmtpSettings(host="localhost", security="plain"), smtp_factory=broken)

    with pytest.raises(EmailDeliveryError) as raised:
        await sender.send(message(), idempotency_key="key")
    assert str(raised.value) == "SMTP delivery failed (OSError)"


async def test_gmail_sends_and_reads_through_bounded_api_calls() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/token":
            assert b"refresh_token=refresh-secret" in request.content
            return httpx.Response(200, json={"access_token": "access-token", "expires_in": 3600})
        assert request.headers["Authorization"] == "Bearer access-token"
        if request.url.path.endswith("/messages/send"):
            assert "raw" in request.read().decode()
            return httpx.Response(200, json={"id": "gmail-sent-1"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(
                200, json={"messages": [{"id": "gmail-in-1"}], "resultSizeEstimate": 1}
            )
        if request.url.path.endswith("/attachments/attachment-1"):
            return httpx.Response(200, json={"data": "emlwLWJ5dGVz", "size": 9})
        return httpx.Response(
            200,
            json={
                "id": "gmail-in-1",
                "threadId": "thread-1",
                "internalDate": "1720000000000",
                "snippet": "Please see the attached file",
                "labelIds": ["UNREAD", "INBOX"],
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Ana <ana@example.com>"},
                        {"name": "To", "value": "inbox@example.org"},
                        {"name": "Subject", "value": "Documents"},
                    ],
                    "parts": [
                        {
                            "filename": "documents.zip",
                            "mimeType": "application/zip",
                            "body": {"attachmentId": "attachment-1", "size": 9},
                        }
                    ],
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gmail = GmailMailboxClient(
        GmailOAuthSettings(
            client_id="client-id",
            client_secret=SecretStr("client-secret"),
            refresh_token=SecretStr("refresh-secret"),
            token_uri="https://oauth.example/token",
            api_base_url="https://gmail.example/gmail/v1",
            message_id_domain="example.org",
        ),
        client=client,
    )

    receipt = await gmail.send(message(), idempotency_key="order:1:reminder")
    inbox = await gmail.list_messages(query="is:unread", max_results=10)

    assert receipt.provider == "gmail"
    assert receipt.provider_message_id == "gmail-sent-1"
    assert inbox.messages[0].subject == "Documents"
    assert inbox.messages[0].attachments[0].filename == "documents.zip"
    assert await gmail.download_attachment("gmail-in-1", "attachment-1") == b"zip-bytes"
    assert calls.count("/token") == 1
    await client.aclose()


async def test_gmail_token_failure_hides_the_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant", "secret": "leak"})

    gmail = GmailMailboxClient(
        GmailOAuthSettings(
            client_id="id", client_secret=SecretStr("s"), refresh_token=SecretStr("r")
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(EmailDeliveryError) as raised:
        await gmail.list_messages()
    assert str(raised.value) == "Gmail OAuth token request failed (HTTPStatusError)"
