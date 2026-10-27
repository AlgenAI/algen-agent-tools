"""Regression tests for defects found in review: each test names the failure it prevents."""

from __future__ import annotations

import base64
from email import message_from_bytes
from typing import Any

import httpx
import pytest
from algen_agent_runtime.exceptions.errors import (
    ConfigurationError,
    PolicyDeniedError,
    ToolExecutionError,
)
from algen_agent_runtime.tools.authoring import Toolkit, read
from algen_agent_runtime.tools.contracts import ToolContext
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import BaseModel, SecretStr, ValidationError

from algen_agent_tools.gmail import GmailMailboxClient, GmailOAuthSettings
from algen_agent_tools.mail import (
    EmailAddress,
    EmailAttachmentReference,
    EmailDeliveryError,
    EmailMessage,
    FakeMailbox,
    MailClient,
    bind_artifact_loader,
)
from algen_agent_tools.postgres import FakePostgres, PostgresToolkit
from algen_agent_tools.smtp import SmtpEmailSender, SmtpSettings, smtp_toolkit
from algen_agent_tools.testing import passthrough_parameters, toolkit_contract_problems

EMAIL = {"to": [{"address": "person@example.com"}], "subject": "Hi", "text_body": "Hello."}


async def test_gmail_delivers_bcc_recipients() -> None:
    raws: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        raw = httpx.Response(200, content=request.content).json()["raw"]
        raws.append(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return httpx.Response(200, json={"id": "sent-1"})

    gmail = GmailMailboxClient(
        GmailOAuthSettings(
            client_id="id",
            client_secret=SecretStr("s"),
            refresh_token=SecretStr("r"),
            token_uri="https://oauth.example/token",
        ),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    message = EmailMessage.model_validate(
        {**EMAIL, "sender": {"address": "desk@example.org"}, "bcc": [{"address": "a@example.com"}]}
    )

    await gmail.send(message, idempotency_key="k")

    assert message_from_bytes(raws[0])["Bcc"] == "a@example.com"


class _Smtp:
    def __init__(self) -> None:
        self.sent: list[Any] = []

    def ehlo(self) -> None: ...
    def starttls(self, *, context: Any) -> None: ...
    def quit(self) -> None: ...

    def send_message(self, value: Any, *, to_addrs: tuple[str, ...]) -> Any:
        self.sent.append(value)
        return {}


async def test_the_attachment_loader_receives_the_calls_tenant() -> None:
    seen: list[str] = []

    async def loader(reference: EmailAttachmentReference, context: ToolContext) -> bytes:
        seen.append(context.tenant_id)
        if context.tenant_id != "tenant-a":
            raise PermissionError("not this tenant's artifact")
        return b"report"

    smtp = _Smtp()
    transport = SmtpEmailSender(
        SmtpSettings(host="smtp.example.com"),
        artifact_loader=bind_artifact_loader(loader),
        smtp_factory=lambda settings: smtp,
    )

    async def open_client(secrets: Any) -> MailClient:
        return MailClient(EmailAddress(address="desk@example.org"), transport)

    send = smtp_toolkit(client=open_client, artifact_loader=loader).tools[0]
    attachment = {"artifact_id": "a-1", "filename": "r.pdf", "media_type": "application/pdf"}
    arguments = {**EMAIL, "attachments": [attachment]}

    await send.execute(arguments, fake_tool_context(send, tenant_id="tenant-a"))
    with pytest.raises(PermissionError):
        await send.execute(arguments, fake_tool_context(send, tenant_id="tenant-b"))
    assert seen == ["tenant-a", "tenant-b"]
    assert len(smtp.sent) == 1
    with pytest.raises(EmailDeliveryError, match="inside a send_email tool call"):
        await bind_artifact_loader(loader)(EmailAttachmentReference(**attachment))


async def test_reply_to_is_bound_by_the_domain_allowlist() -> None:
    mailbox = FakeMailbox()
    send = smtp_toolkit(client=mailbox.factory(), allowed_domains=("example.com",)).tools[0]

    with pytest.raises(PolicyDeniedError, match=r"evil\.test"):
        await send.execute(
            {**EMAIL, "reply_to": {"address": "x@evil.test"}}, fake_tool_context(send)
        )
    assert mailbox.sent == []


class _Row(BaseModel):
    id: int


@pytest.mark.parametrize("bad", [0, -1])
async def test_row_limits_below_one_are_refused(bad: int) -> None:
    toolkit = PostgresToolkit(client=FakePostgres().factory)

    with pytest.raises(ConfigurationError, match="at least 1"):
        toolkit.query("q", "SELECT 1 AS id", description="x", row=_Row, max_rows=bad)
    with pytest.raises(ConfigurationError, match="at least 1"):
        await toolkit.fetch_rows("SELECT 1 AS id", [], _Row, label="q", max_rows=bad)


async def test_a_tiny_timeout_never_becomes_no_timeout() -> None:
    fake = FakePostgres().on("SELECT 1", rows=[{"id": 1}])
    toolkit = PostgresToolkit(client=fake.factory)

    await toolkit.fetch_rows("SELECT 1 AS id", [], _Row, label="q", timeout_seconds=0.0001)

    assert fake.calls[0].sql == "SET LOCAL statement_timeout = 1"


class Filter(BaseModel):
    Statement: str


def test_the_passthrough_check_is_case_insensitive_and_nested() -> None:
    schema = {
        "type": "object",
        "properties": {
            "SQL": {"type": "string"},
            "filter": {"type": "object", "properties": {"url": {"type": "string"}}},
        },
    }
    assert passthrough_parameters(schema) == ["SQL", "filter/url"]

    toolkit = Toolkit("db")

    @toolkit.tool(read())
    def search(filter: Filter) -> list[str]:
        """Search."""
        return []

    assert toolkit_contract_problems(toolkit) == (
        "db.search: takes raw passthrough parameters ['Statement']",
    )


mcp = pytest.importorskip("mcp")


def _overlay(**server: Any) -> Any:
    from algen_agent_tools.mcp import McpOverlay

    return McpOverlay.from_mapping(
        {
            "name": "remote",
            "server": {
                "name": "remote",
                "transport": "streamable_http",
                "url": "https://mcp.example.com/mcp",
                "auth_token_ref": "env://REMOTE_TOKEN",
                **server,
            },
            "tools": {
                "lookup": {
                    "effect": "read",
                    "input_schema": {
                        "type": "object",
                        "properties": {"card": {"type": "string", "pattern": "^[0-9]{4}$"}},
                        "required": ["card"],
                    },
                }
            },
        }
    )


def test_an_empty_host_allowlist_is_refused() -> None:
    from algen_agent_tools.mcp import McpOverlay

    data = _overlay().model_dump(mode="json")
    data["allowed_hosts"] = []

    with pytest.raises(ValidationError, match="allowed_hosts"):
        McpOverlay.from_mapping(data)


def test_an_http_server_is_pinned_to_its_own_host() -> None:
    from algen_agent_tools.mcp.session import allowed_hosts

    server = _overlay().server
    assert allowed_hosts(server, None) == ("mcp.example.com",)
    assert allowed_hosts(server, ("a.example.com",)) == ("a.example.com",)


async def test_http_secrets_resolve_through_the_deployment_resolver() -> None:
    from algen_agent_tools.mcp.fake import FakeMcpServer
    from algen_agent_tools.mcp.session import open_session

    overlay = _overlay()
    server = FakeMcpServer().add("lookup", overlay.tools["lookup"].input_schema, lambda a: "ok")
    resolved: list[str] = []

    async def secrets(reference: str) -> str:
        resolved.append(reference)
        return "token-from-vault"

    session = await open_session(overlay, secrets, session_factory=server.session_factory)
    try:
        assert resolved == ["env://REMOTE_TOKEN"]
        assert session._manager._environment == {"REMOTE_TOKEN": "token-from-vault"}
    finally:
        await session.aclose()


async def test_argument_errors_do_not_echo_the_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTE_TOKEN", "t")
    lookup = _overlay().toolkit().tools[0]

    with pytest.raises(ToolExecutionError) as raised:
        await lookup.execute({"card": "4111111111111111"}, fake_tool_context(lookup))

    assert "4111" not in str(raised.value)
    assert str(raised.value) == "invalid arguments for remote.lookup: card fails 'pattern'"
