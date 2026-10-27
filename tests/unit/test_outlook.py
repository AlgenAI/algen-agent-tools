"""The Outlook connector against a mocked Microsoft Graph."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from algen_agent_runtime.tools.contracts import SideEffect, Tool, ToolContext
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import SecretStr, ValidationError

from algen_agent_tools.attachments import AttachmentRef, AttachmentUpload
from algen_agent_tools.mail import (
    EmailAddress,
    EmailAttachmentReference,
    EmailDeliveryError,
    EmailMessage,
    MailClient,
)
from algen_agent_tools.outlook import OutlookMailboxClient, OutlookSettings, outlook_toolkit
from algen_agent_tools.testing import assert_toolkit_contract, plugin_tools

GRAPH = "https://graph.microsoft.com/v1.0"
PDF = b"%PDF-1.7 invoice"
MESSAGE = {
    "id": "AAMk/abc+1=",
    "conversationId": "conv-1",
    "from": {"emailAddress": {"name": "Ana", "address": "ana@contoso.com"}},
    "toRecipients": [{"emailAddress": {"address": "agent@contoso.com"}}],
    "ccRecipients": [{"emailAddress": {"name": "Team", "address": "team@contoso.com"}}],
    "subject": "Invoice",
    "receivedDateTime": "2026-10-20T09:30:00Z",
    "bodyPreview": "Please find the invoice.",
    "hasAttachments": True,
    "categories": ["Finance"],
    "body": {"contentType": "text", "content": "Please find the invoice.\r\nThanks"},
}


class Graph:
    def __init__(self, *, next_link: str | None = None, fail: int | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.next_link = next_link
        self.fail = fail
        self.attachment: dict[str, Any] = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "id": "att-1",
            "name": "invoice.pdf",
            "contentType": "application/pdf",
            "size": len(PDF),
            "contentBytes": base64.b64encode(PDF).decode(),
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "login.microsoftonline.com":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        if self.fail:
            return httpx.Response(self.fail, json={"error": {"message": "secret detail"}})
        path = request.url.path
        if path.endswith("/sendMail"):
            return httpx.Response(202)
        if path.endswith("/attachments"):
            return httpx.Response(200, json={"value": [self.attachment]})
        if "/attachments/" in path:
            return httpx.Response(200, json=self.attachment)
        if path.endswith("/messages"):
            body: dict[str, Any] = {"value": [MESSAGE]}
            if self.next_link:
                body["@odata.nextLink"] = self.next_link
            return httpx.Response(200, json=body)
        return httpx.Response(200, json=MESSAGE)

    def client(self, *, delegated: bool = True, **settings: Any) -> OutlookMailboxClient:
        loader = settings.pop("artifact_loader", None)
        return OutlookMailboxClient(
            OutlookSettings(
                tenant_id="contoso.onmicrosoft.com",
                client_id="client",
                client_secret=SecretStr("client-secret"),
                refresh_token=SecretStr("refresh-secret") if delegated else None,
                mailbox="agent@contoso.com",
                **settings,
            ),
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
            artifact_loader=loader,
        )

    def factory(self, **options: Any) -> Any:
        async def open_client(secrets: Any) -> MailClient:
            return MailClient(EmailAddress(address="agent@contoso.com"), self.client(**options))

        return open_client

    def api(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == "graph.microsoft.com"]

    def listings(self) -> list[httpx.Request]:
        return [r for r in self.api() if r.url.path.endswith("/messages")]

    def token_grants(self) -> list[dict[str, list[str]]]:
        return [
            parse_qs(r.content.decode())
            for r in self.requests
            if r.url.host == "login.microsoftonline.com"
        ]


def named(toolkit: Any, action: str) -> Tool:
    return next(t for t in toolkit.tools if t.definition.name == f"outlook.{action}")


def test_the_plugin_describes_its_tools_and_settings() -> None:
    tools = plugin_tools("algen_agent_tools.outlook")
    assert {name: tool.side_effect for name, tool in tools.items()} == {
        "outlook.get_message": SideEffect.READ,
        "outlook.list_messages": SideEffect.READ,
        "outlook.send_email": SideEffect.EXTERNAL,
    }
    assert "ALGEN_TOOLS_OUTLOOK_CLIENT_SECRET" in tools["outlook.send_email"].env
    assert_toolkit_contract(outlook_toolkit(artifact_sink=lambda *a: None))  # type: ignore[arg-type]


async def test_delegated_access_uses_the_refresh_token_and_the_signed_in_mailbox() -> None:
    graph = Graph()
    await graph.client().get_message("m1")
    [grant] = graph.token_grants()
    assert grant["grant_type"] == ["refresh_token"]
    assert grant["scope"] == ["offline_access https://graph.microsoft.com/.default"]
    assert graph.requests[0].url.path == "/contoso.onmicrosoft.com/oauth2/v2.0/token"
    assert graph.api()[0].url.path == "/v1.0/me/messages/m1"


async def test_application_access_uses_client_credentials_on_the_named_mailbox() -> None:
    graph = Graph()
    await graph.client(delegated=False).get_message("m1")
    [grant] = graph.token_grants()
    assert grant["grant_type"] == ["client_credentials"]
    assert "refresh_token" not in grant
    assert (
        graph.api()[0]
        .url.raw_path.decode()
        .startswith("/v1.0/users/agent%40contoso.com/messages/m1")
    )


async def test_list_messages_reads_the_folder_newest_first_with_attachments() -> None:
    graph = Graph()
    tool = named(outlook_toolkit(client=graph.factory()), "list_messages")
    page = await tool.execute({"max_results": 10}, fake_tool_context(tool))

    listing, attachments = graph.api()
    assert listing.url.path == "/v1.0/me/mailFolders/inbox/messages"
    assert listing.url.params["$top"] == "10"
    assert listing.url.params["$orderby"] == "receivedDateTime desc"
    assert attachments.url.raw_path.decode().startswith(
        "/v1.0/me/messages/AAMk%2Fabc%2B1%3D/attachments"
    )
    [row] = page["messages"]
    assert (row["sender"], row["recipients"], row["labels"]) == (
        "Ana <ana@contoso.com>",
        "agent@contoso.com",
        ["Finance"],
    )
    assert row["received_at_ms"] == 1_792_488_600_000
    assert row["attachments"][0]["provider_attachment_id"] == "att-1"
    assert page["next_page_token"] is None


async def test_search_and_unread_filters_are_typed_and_exclusive() -> None:
    graph = Graph()
    client = graph.client()
    await client.list_messages(search='from:ana "invoice"')
    assert graph.api()[0].url.params["$search"] == '"from:ana invoice"'
    assert "$orderby" not in graph.api()[0].url.params
    await client.list_messages(unread_only=True, folder="archive")
    assert graph.listings()[-1].url.params["$filter"] == "isRead eq false"
    assert graph.listings()[-1].url.path == "/v1.0/me/mailFolders/archive/messages"
    with pytest.raises(ValueError, match="cannot be combined"):
        await client.list_messages(search="x", unread_only=True)


async def test_page_tokens_are_opaque_and_stay_in_this_mailbox() -> None:
    graph = Graph(
        next_link=f"{GRAPH}/me/mailFolders/inbox/messages?$top=25&$skip=25&$select=id&evil=1"
    )
    client = graph.client(delegated=False)
    page = await client.list_messages()
    token = page.next_page_token
    assert token is not None and "http" not in token and "evil" not in token

    graph.next_link = None
    await client.list_messages(page_token=token)
    continued = graph.listings()[-1]
    assert continued.url.raw_path.decode().startswith(
        "/v1.0/users/agent%40contoso.com/mailFolders/inbox/messages"
    )
    assert continued.url.params["$skip"] == "25"

    for forged in (
        f"{GRAPH}/users/ceo@contoso.com/messages",
        "../../users/ceo@contoso.com?$top=1",
        "inbox?$top=1&$expand=attachments",
        "inbox",
    ):
        with pytest.raises(EmailDeliveryError, match="inbox read failed"):
            await client.list_messages(page_token=forged)


async def test_get_message_asks_for_text_and_returns_the_body_and_cc() -> None:
    graph = Graph()
    tool = named(outlook_toolkit(client=graph.factory()), "get_message")
    detail = await tool.execute({"message_id": "AAMk/abc+1="}, fake_tool_context(tool))

    request = next(r for r in graph.api() if not r.url.path.endswith("/attachments"))
    assert request.headers["Prefer"] == 'outlook.body-content-type="text"'
    assert detail["text_body"] == "Please find the invoice.\nThanks"
    assert detail["cc"] == "Team <team@contoso.com>"


async def test_html_bodies_are_converted_and_cut() -> None:
    graph = Graph()
    original = MESSAGE["body"]
    MESSAGE["body"] = {"contentType": "html", "content": "<p>Hi</p><script>x</script>" + "y" * 300}
    try:
        detail = await graph.client().get_message("m1", max_chars=100)
    finally:
        MESSAGE["body"] = original
    assert detail.text_body.startswith("Hi\nyyy")
    assert (len(detail.text_body), detail.body_truncated) == (100, True)


@pytest.mark.parametrize("message_id", ["..", ".x", "a b", "a?b", ""])
async def test_ids_that_could_change_the_api_path_are_refused(message_id: str) -> None:
    tool = named(outlook_toolkit(client=Graph().factory()), "get_message")
    with pytest.raises(ValidationError, match="message_id"):
        await tool.execute({"message_id": message_id}, fake_tool_context(tool))


async def test_get_attachment_saves_file_attachments_only() -> None:
    graph = Graph()
    stored: list[tuple[AttachmentUpload, ToolContext]] = []

    async def sink(upload: AttachmentUpload, context: ToolContext) -> AttachmentRef:
        stored.append((upload, context))
        return AttachmentRef(
            artifact_id="art-1", filename=upload.filename, media_type=upload.media_type
        )

    tool = named(outlook_toolkit(client=graph.factory(), artifact_sink=sink), "get_attachment")
    result = await tool.execute(
        {"message_id": "AAMk/abc+1=", "attachment_id": "att-1"}, fake_tool_context(tool)
    )
    assert result["artifact_id"] == "art-1"
    assert stored[0][0] == AttachmentUpload("invoice.pdf", "application/pdf", PDF)

    graph.attachment["@odata.type"] = "#microsoft.graph.itemAttachment"
    with pytest.raises(EmailDeliveryError, match="not a file"):
        await tool.execute({"message_id": "m1", "attachment_id": "att-1"}, fake_tool_context(tool))
    graph.attachment.update(
        {"@odata.type": "#microsoft.graph.fileAttachment", "contentBytes": "%%"}
    )
    with pytest.raises(EmailDeliveryError, match=r"^Outlook attachment download failed \("):
        await tool.execute({"message_id": "m1", "attachment_id": "att-1"}, fake_tool_context(tool))


async def test_send_builds_the_graph_message_and_a_stable_receipt() -> None:
    graph = Graph()

    async def load(reference: EmailAttachmentReference) -> bytes:
        return PDF

    client = graph.client(artifact_loader=load)
    message = EmailMessage(
        sender=EmailAddress(address="agent@contoso.com"),
        to=(EmailAddress(address="ana@contoso.com", display_name="Ana"),),
        bcc=(EmailAddress(address="audit@contoso.com"),),
        reply_to=EmailAddress(address="desk@contoso.com"),
        subject="Your invoice",
        text_body="Attached.",
        attachments=(
            EmailAttachmentReference(
                artifact_id="a1", filename="invoice.pdf", media_type="application/pdf"
            ),
        ),
    )
    first = await client.send(message, idempotency_key="run-1:send")
    again = await client.send(message, idempotency_key="run-1:send")

    sent = json.loads(graph.api()[0].content)
    assert sent["saveToSentItems"] is True
    body = sent["message"]
    assert body["toRecipients"] == [{"emailAddress": {"address": "ana@contoso.com", "name": "Ana"}}]
    assert body["bccRecipients"] == [{"emailAddress": {"address": "audit@contoso.com"}}]
    assert body["replyTo"] == [{"emailAddress": {"address": "desk@contoso.com"}}]
    assert body["body"] == {"contentType": "Text", "content": "Attached."}
    assert body["attachments"][0]["contentBytes"] == base64.b64encode(PDF).decode()
    assert "_size" not in body["attachments"][0]
    assert body["internetMessageHeaders"][0]["name"] == "X-Algen-Idempotency-Key"
    assert first.provider_message_id == again.provider_message_id
    assert first.provider_message_id.startswith("algen-")
    assert first.accepted_recipients == ("ana@contoso.com", "audit@contoso.com")


async def test_send_limits_attachments_to_what_graph_takes_inline() -> None:
    async def load(reference: EmailAttachmentReference) -> bytes:
        return b"x" * 2_000

    client = Graph().client(artifact_loader=load, max_attachment_bytes=3_000)
    reference = EmailAttachmentReference(
        artifact_id="a", filename="a.bin", media_type="application/x"
    )
    message = EmailMessage(
        sender=EmailAddress(address="agent@contoso.com"),
        to=(EmailAddress(address="ana@contoso.com"),),
        subject="Files",
        text_body="Two files.",
        attachments=(reference, reference.model_copy(update={"artifact_id": "b"})),
    )
    with pytest.raises(EmailDeliveryError, match="size limit"):
        await client.send(message, idempotency_key="k")


async def test_failures_name_only_the_cause_type() -> None:
    client = Graph(fail=403).client()
    with pytest.raises(EmailDeliveryError) as raised:
        await client.get_message("m1")
    assert str(raised.value) == "Outlook message read failed (HTTPStatusError)"

    def refuse_token(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant", "detail": "refresh-secret"})

    broken = OutlookMailboxClient(
        OutlookSettings(
            tenant_id="contoso.onmicrosoft.com",
            client_id="c",
            client_secret=SecretStr("s"),
            mailbox="agent@contoso.com",
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(refuse_token)),
    )
    with pytest.raises(EmailDeliveryError) as raised:
        await broken.get_message("m1")
    assert "secret" not in str(raised.value)


def test_settings_refuse_a_tenant_that_could_change_the_token_url() -> None:
    with pytest.raises(ValidationError, match="tenant_id"):
        OutlookSettings(
            tenant_id="evil.com/x",
            client_id="c",
            client_secret=SecretStr("s"),
            mailbox="agent@contoso.com",
        )


@pytest.mark.parametrize("refresh", [None, "refresh"])
async def test_the_client_opens_from_env(
    monkeypatch: pytest.MonkeyPatch, refresh: str | None
) -> None:
    for name, value in {
        "TENANT_ID": "contoso.onmicrosoft.com",
        "CLIENT_ID": "client",
        "CLIENT_SECRET": "secret",
        "MAILBOX": "agent@contoso.com",
        "ALLOWED_DOMAINS": "contoso.com",
    }.items():
        monkeypatch.setenv(f"ALGEN_TOOLS_OUTLOOK_{name}", value)
    if refresh:
        monkeypatch.setenv("ALGEN_TOOLS_OUTLOOK_REFRESH_TOKEN", refresh)
    else:
        monkeypatch.delenv("ALGEN_TOOLS_OUTLOOK_REFRESH_TOKEN", raising=False)
    toolkit = outlook_toolkit()
    client: MailClient = await toolkit.client()
    assert isinstance(client.transport, OutlookMailboxClient)
    assert client.sender.address == "agent@contoso.com"
    assert client.allowed_domains == ("contoso.com",)
    assert client.transport._root == ("me" if refresh else "users/agent%40contoso.com")
    await toolkit.aclose()
