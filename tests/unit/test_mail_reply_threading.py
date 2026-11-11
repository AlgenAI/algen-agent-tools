"""Replies that thread: In-Reply-To and References on SMTP and Gmail, Gmail's threadId, and
Outlook's reply to the original message."""

from __future__ import annotations

import base64
import email
import json
from email.policy import default as default_policy
from typing import Any

import httpx
import pytest
from algen_agent_runtime.tools.contracts import Tool
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import SecretStr, ValidationError

from algen_agent_tools.gmail import GmailMailboxClient, GmailOAuthSettings, gmail_toolkit
from algen_agent_tools.mail import (
    EmailAddress,
    EmailDeliveryError,
    EmailMessage,
    FakeMailbox,
    build_mime_message,
)
from algen_agent_tools.outlook import OutlookMailboxClient, OutlookSettings, outlook_toolkit
from algen_agent_tools.smtp import SmtpEmailSender, SmtpSettings, smtp_toolkit

PARENT = "<CAF=parent+1@mail.example.com>"
ROOT = "<root.1@mail.example.com>"
# Exchange Message-IDs are longer than a header line once named.
LONG = "<AM0PR01MB1234567890ABCDEF1234567890ABCDEF@AM0PR01MB1234.eurprd01.prod.exchangelabs.com>"


def message(**updates: Any) -> EmailMessage:
    values: dict[str, Any] = {
        "sender": {"address": "desk@example.org"},
        "to": [{"address": "ana@example.com"}],
        "subject": "Re: Invoice 42",
        "text_body": "Thanks, paid.",
    }
    values.update(updates)
    return EmailMessage.model_validate(values)


def headers(value: EmailMessage) -> email.message.EmailMessage:
    mime, _ = build_mime_message(
        value, idempotency_key="k", attachments=[], message_id_domain="example.org"
    )
    parsed = email.message_from_bytes(mime.as_bytes(), policy=default_policy)
    assert isinstance(parsed, email.message.EmailMessage)
    return parsed


def test_a_reply_carries_in_reply_to_and_the_parents_references() -> None:
    parsed = headers(message(in_reply_to=PARENT, references=[ROOT]))
    assert parsed["In-Reply-To"] == PARENT
    assert parsed["References"].split() == [ROOT, PARENT]


def test_references_default_to_the_parent_and_are_not_repeated() -> None:
    assert headers(message(in_reply_to=PARENT))["References"] == PARENT
    again = headers(message(in_reply_to=PARENT, references=[ROOT, PARENT]))
    assert again["References"].split() == [ROOT, PARENT]
    assert "In-Reply-To" not in headers(message())


def test_long_message_ids_are_folded_between_ids_never_encoded() -> None:
    mime, _ = build_mime_message(
        message(in_reply_to=LONG, references=[ROOT, PARENT, LONG]),
        idempotency_key="k",
        attachments=[],
        message_id_domain="example.org",
    )
    raw = mime.as_bytes()
    assert b"=?" not in raw.split(b"\n\n", 1)[0]
    parsed = email.message_from_bytes(raw, policy=default_policy)
    assert parsed["In-Reply-To"] == LONG
    assert parsed["References"].split() == [ROOT, PARENT, LONG]


@pytest.mark.parametrize(
    "updates",
    [
        {"in_reply_to": "parent@mail.example.com"},  # no angle brackets
        {"in_reply_to": "<two words@example.com>"},
        {"in_reply_to": "<no-at-sign>"},
        {"in_reply_to": f"{PARENT}\r\nBcc: attacker@example.net"},
        {"in_reply_to": "<a@b> <c@d>"},  # one id only
        {"references": ["<ok@example.com>", "<bad@exa mple.com>"]},
        {"references": [f"<{n}@example.com>" for n in range(101)]},
        {"in_reply_to": "<" + "a" * 980 + "@example.com>"},
        {"thread_id": "18c0abc"},  # a provider thread without the message replied to
        {"in_reply_to": PARENT, "thread_id": "../threads"},
    ],
)
def test_invalid_threading_fields_are_refused(updates: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        message(**updates)


def test_references_are_bounded_in_total() -> None:
    ids = [f"<{'r' * 300}.{n}@example.com>" for n in range(70)]
    with pytest.raises(ValidationError, match="references exceed"):
        message(references=ids)


def test_a_message_id_with_a_literal_domain_and_specials_is_accepted() -> None:
    value = message(in_reply_to="<a.b!#$%&'*+/=?^_`{|}~-c@[192.0.2.1]>")
    assert value.in_reply_to == "<a.b!#$%&'*+/=?^_`{|}~-c@[192.0.2.1]>"


# SMTP


class Smtp:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    def __call__(self, settings: SmtpSettings) -> Smtp:
        return self

    def ehlo(self) -> None:
        pass

    def send_message(self, value: Any, *, to_addrs: tuple[str, ...]) -> dict[str, Any]:
        self.messages.append(value)
        return {}

    def quit(self) -> None:
        pass


async def test_smtp_sends_the_threading_headers() -> None:
    smtp = Smtp()
    sender = SmtpEmailSender(SmtpSettings(host="localhost", security="plain"), smtp_factory=smtp)

    await sender.send(message(in_reply_to=PARENT, references=[ROOT]), idempotency_key="k")

    [sent] = smtp.messages
    assert sent["In-Reply-To"] == PARENT
    assert sent["References"].split() == [ROOT, PARENT]


async def test_smtp_refuses_a_provider_thread_before_sending() -> None:
    smtp = Smtp()
    sender = SmtpEmailSender(SmtpSettings(host="localhost", security="plain"), smtp_factory=smtp)
    with pytest.raises(EmailDeliveryError, match="no provider threads"):
        await sender.send(message(in_reply_to=PARENT, thread_id="18c0abc"), idempotency_key="k")
    assert smtp.messages == []


# Gmail


class Gmail:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if request.url.path.endswith("/messages/send"):
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "sent-1", "threadId": "18c0abc"})
        return httpx.Response(404)

    def client(self) -> GmailMailboxClient:
        return GmailMailboxClient(
            GmailOAuthSettings(
                client_id="id",
                client_secret=SecretStr("secret"),
                refresh_token=SecretStr("refresh"),
                token_uri="https://oauth.example/token",
                api_base_url="https://gmail.example/gmail/v1",
            ),
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
        )

    def raw(self, index: int = 0) -> email.message.Message:
        data = self.sent[index]["raw"]
        return email.message_from_bytes(
            base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)), policy=default_policy
        )


async def test_gmail_sends_the_thread_id_and_the_headers() -> None:
    gmail = Gmail()

    await gmail.client().send(
        message(in_reply_to=PARENT, references=[ROOT], thread_id="18c0abc"),
        idempotency_key="k",
    )
    await gmail.client().send(message(), idempotency_key="k2")

    assert gmail.sent[0]["threadId"] == "18c0abc"
    assert gmail.raw(0)["In-Reply-To"] == PARENT
    assert gmail.raw(0)["References"].split() == [ROOT, PARENT]
    assert "threadId" not in gmail.sent[1]


# Outlook


class Graph:
    def __init__(self, *, found: bool = True) -> None:
        self.found = found
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/messages") and request.method == "GET":
            return httpx.Response(
                200, json={"value": [{"id": "AAMk/abc+1="}] if self.found else []}
            )
        if path.endswith("/reply") or path.endswith("/sendMail"):
            return httpx.Response(202)
        return httpx.Response(404)

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


async def test_outlook_replies_to_the_original_message() -> None:
    graph = Graph()

    receipt = await graph.client().send(
        message(in_reply_to="<it's.1@contoso.com>", references=[ROOT]), idempotency_key="k"
    )

    lookup, reply = graph.requests
    assert lookup.method == "GET"
    assert lookup.url.params["$filter"] == "internetMessageId eq '<it''s.1@contoso.com>'"
    assert lookup.url.params["$select"] == "id"
    assert reply.method == "POST"
    # The Graph id is one path segment, encoded.
    assert reply.url.raw_path.decode().endswith("/me/messages/AAMk%2Fabc%2B1%3D/reply")
    body = json.loads(reply.content)
    assert "saveToSentItems" not in body
    assert body["message"]["toRecipients"] == [{"emailAddress": {"address": "ana@example.com"}}]
    assert body["message"]["body"] == {"contentType": "Text", "content": "Thanks, paid."}
    names = [item["name"] for item in body["message"]["internetMessageHeaders"]]
    assert names == ["X-Algen-Idempotency-Key"]
    assert receipt.provider_message_id.startswith("algen-")


async def test_outlook_without_in_reply_to_sends_a_new_message() -> None:
    graph = Graph()
    await graph.client().send(message(), idempotency_key="k")
    [request] = graph.requests
    assert request.url.path.endswith("/sendMail")


@pytest.mark.parametrize(
    ("updates", "error"),
    [
        ({"in_reply_to": PARENT, "thread_id": "conv1"}, "thread_id is not supported"),
        ({"references": [ROOT]}, "reply with in_reply_to"),
    ],
)
async def test_outlook_refuses_threading_it_cannot_send(
    updates: dict[str, Any], error: str
) -> None:
    graph = Graph()
    with pytest.raises(EmailDeliveryError, match=error):
        await graph.client().send(message(**updates), idempotency_key="k")
    assert graph.requests == []


async def test_outlook_refuses_a_reply_to_a_message_it_does_not_have() -> None:
    graph = Graph(found=False)
    with pytest.raises(EmailDeliveryError, match="no message with that in_reply_to"):
        await graph.client().send(message(in_reply_to=PARENT), idempotency_key="k")
    assert [r.method for r in graph.requests] == ["GET"]


# The send_email tools


def send_tool(toolkit: Any) -> Tool:
    return next(t for t in toolkit.tools if t.definition.name.endswith(".send_email"))


def email_schema(tool: Tool) -> dict[str, Any]:
    # A tool whose one argument is a model takes that model's fields as its input.
    schema: dict[str, Any] = tool.definition.input_schema
    return schema


@pytest.mark.parametrize(
    ("toolkit", "thread_id"),
    [(smtp_toolkit, False), (gmail_toolkit, True), (outlook_toolkit, False)],
)
def test_send_tools_take_the_threading_fields(toolkit: Any, thread_id: bool) -> None:
    schema = email_schema(send_tool(toolkit()))
    properties = schema["properties"]
    assert properties["in_reply_to"]["anyOf"][0]["format"] == "identifier"
    assert properties["references"]["items"]["format"] == "identifier"
    assert properties["references"]["maxItems"] == 100
    assert ("thread_id" in properties) is thread_id
    if thread_id:
        assert properties["thread_id"]["anyOf"][0]["format"] == "identifier"


async def test_send_email_passes_the_threading_fields_to_the_transport() -> None:
    mailbox = FakeMailbox()
    tool = send_tool(gmail_toolkit(client=mailbox.factory()))
    email_input = {
        "to": [{"address": "ana@example.com"}],
        "subject": "Re: Invoice 42",
        "text_body": "Thanks.",
        "in_reply_to": PARENT,
        "references": [ROOT],
        "thread_id": "18c0abc",
    }

    await tool.execute(email_input, fake_tool_context(tool))

    [sent] = mailbox.sent
    assert (sent.in_reply_to, sent.references, sent.thread_id) == (PARENT, (ROOT,), "18c0abc")
    assert sent.sender == EmailAddress(address="agent@example.com")


async def test_send_email_refuses_an_invalid_reference() -> None:
    mailbox = FakeMailbox()
    tool = send_tool(smtp_toolkit(client=mailbox.factory()))
    email_input = {
        "to": [{"address": "ana@example.com"}],
        "subject": "Re: Invoice 42",
        "text_body": "Thanks.",
        "in_reply_to": "parent@example.com",
    }
    with pytest.raises(ValidationError):
        await tool.execute(email_input, fake_tool_context(tool))
    assert mailbox.sent == []
