from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from algen_agent_runtime.exceptions.errors import ConfigurationError, PolicyDeniedError
from algen_agent_runtime.tools.authoring import Toolkit
from algen_agent_runtime.tools.contracts import Idempotency, SideEffect, Tool, ToolContext
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import ValidationError

from algen_agent_tools.gmail import GmailMailboxClient, InboundEmailSummary, gmail_toolkit
from algen_agent_tools.mail import EmailAttachmentReference, FakeMailbox, MailClient
from algen_agent_tools.smtp import SmtpEmailSender, smtp_toolkit
from algen_agent_tools.testing import assert_toolkit_contract, plugin_tools

Factory = Callable[..., Toolkit]
EMAIL = {"to": [{"address": "person@example.com"}], "subject": "Hello", "text_body": "Hi."}


def named(toolkit: Toolkit, action: str) -> Tool:
    return next(tool for tool in toolkit.tools if tool.definition.name.endswith(f".{action}"))


async def no_attachment(reference: EmailAttachmentReference, context: ToolContext) -> bytes:
    return b""


@pytest.mark.parametrize("factory", [smtp_toolkit, gmail_toolkit])
def test_toolkits_meet_the_contract(factory: Factory) -> None:
    assert_toolkit_contract(factory(client=FakeMailbox().factory()))
    assert_toolkit_contract(factory(client=FakeMailbox().factory(), artifact_loader=no_attachment))


@pytest.mark.parametrize("factory", [smtp_toolkit, gmail_toolkit])
def test_send_email_is_external_keyed_and_the_sender_is_not_an_input(factory: Factory) -> None:
    send = named(factory(client=FakeMailbox().factory()), "send_email")
    definition = send.definition

    assert definition.side_effect == SideEffect.EXTERNAL
    assert definition.idempotency == Idempotency.KEYED
    assert definition.retry_policy.max_attempts == 1
    assert definition.audit_metadata["content_capture"] == "disabled"
    assert "sender" not in definition.input_schema["properties"]
    assert "headers" not in definition.input_schema["properties"]
    assert "attachments" not in definition.input_schema["properties"]


@pytest.mark.parametrize("factory", [smtp_toolkit, gmail_toolkit])
def test_every_address_field_declares_the_email_format(factory: Factory) -> None:
    # The Runtime's pii guardrail leaves a single address in a format: email field alone.
    properties = named(
        factory(client=FakeMailbox().factory()), "send_email"
    ).definition.input_schema["properties"]
    lists = [properties[name]["items"] for name in ("to", "cc", "bcc")]
    reply_to = next(
        item for item in properties["reply_to"]["anyOf"] if item.get("type") == "object"
    )

    for address in (*lists, reply_to):
        assert address["properties"]["address"]["format"] == "email"
    assert "format" not in address["properties"]["display_name"]


@pytest.mark.parametrize("factory", [smtp_toolkit, gmail_toolkit])
async def test_send_email_sends_from_the_deployment_address_once_per_key(
    factory: Factory,
) -> None:
    mailbox = FakeMailbox()
    send = named(factory(client=mailbox.factory("desk@example.org")), "send_email")
    context = fake_tool_context(send, idempotency_key="run-1:send")

    first = await send.execute(EMAIL, context)
    replay = await send.execute(EMAIL, context)

    assert first == replay
    assert first["accepted_recipients"] == ["person@example.com"]
    [sent] = mailbox.sent
    assert sent.sender.address == "desk@example.org"
    assert sent.subject == "Hello"


@pytest.mark.parametrize("factory", [smtp_toolkit, gmail_toolkit])
async def test_send_email_refuses_a_sender_or_headers_from_the_model(factory: Factory) -> None:
    send = named(factory(client=FakeMailbox().factory()), "send_email")

    for extra in ({"sender": {"address": "ceo@example.org"}}, {"headers": {"X-A": "b"}}):
        with pytest.raises(ValidationError):
            await send.execute({**EMAIL, **extra}, fake_tool_context(send))


async def test_allowed_domains_bound_the_recipients() -> None:
    mailbox = FakeMailbox()
    send = named(
        smtp_toolkit(client=mailbox.factory(), allowed_domains=("example.com",)), "send_email"
    )
    context = fake_tool_context(send)

    await send.execute(
        {**EMAIL, "cc": [{"address": "team@eu.example.com"}]}, fake_tool_context(send)
    )
    with pytest.raises(PolicyDeniedError, match=r"\['elsewhere.net'\]"):
        await send.execute({**EMAIL, "bcc": [{"address": "x@elsewhere.net"}]}, context)
    assert len(mailbox.sent) == 1


async def test_the_allowed_domains_setting_applies_unless_the_code_sets_its_own() -> None:
    mailbox = FakeMailbox()
    from_setting = mailbox.factory(allowed_domains=("example.com",))
    send = named(smtp_toolkit(client=from_setting), "send_email")
    await send.execute(EMAIL, fake_tool_context(send))
    with pytest.raises(PolicyDeniedError, match=r"\['elsewhere.net'\]"):
        await send.execute(
            {**EMAIL, "to": [{"address": "x@elsewhere.net"}]}, fake_tool_context(send)
        )

    code_wins = named(
        smtp_toolkit(client=from_setting, allowed_domains=("elsewhere.net",)), "send_email"
    )
    await code_wins.execute(
        {**EMAIL, "to": [{"address": "x@elsewhere.net"}]}, fake_tool_context(code_wins)
    )
    assert [message.to[0].address for message in mailbox.sent] == [
        "person@example.com",
        "x@elsewhere.net",
    ]


@pytest.mark.parametrize(("prefix", "factory"), [("SMTP", smtp_toolkit), ("GMAIL", gmail_toolkit)])
async def test_the_allowed_domains_setting_is_read_when_the_client_opens(
    monkeypatch: pytest.MonkeyPatch, prefix: str, factory: Factory
) -> None:
    for name, value in {
        "HOST": "smtp.example.com",
        "CLIENT_ID": "id",
        "CLIENT_SECRET": "secret",
        "REFRESH_TOKEN": "refresh",
        "SENDER": "agent@example.com",
    }.items():
        monkeypatch.setenv(f"ALGEN_TOOLS_{prefix}_{name}", value)
    monkeypatch.delenv(f"ALGEN_TOOLS_{prefix}_ALLOWED_DOMAINS", raising=False)
    unset = factory()
    assert (await unset.client()).allowed_domains == ()
    await unset.aclose()

    monkeypatch.setenv(f"ALGEN_TOOLS_{prefix}_ALLOWED_DOMAINS", " Example.com., partner.example ")
    toolkit = factory()
    assert (await toolkit.client()).allowed_domains == ("example.com", "partner.example")
    await toolkit.aclose()

    monkeypatch.setenv(f"ALGEN_TOOLS_{prefix}_ALLOWED_DOMAINS", "example.com,not a domain")
    with pytest.raises(ConfigurationError, match="not a domain"):
        await factory().client()


async def test_attachments_are_offered_only_with_an_artifact_loader() -> None:
    mailbox = FakeMailbox()
    send = named(
        smtp_toolkit(client=mailbox.factory(), artifact_loader=no_attachment), "send_email"
    )
    attachment = {"artifact_id": "a-1", "filename": "report.pdf", "media_type": "application/pdf"}

    assert "attachments" in send.definition.input_schema["properties"]
    await send.execute({**EMAIL, "attachments": [attachment]}, fake_tool_context(send))
    assert mailbox.sent[0].attachments[0].filename == "report.pdf"


async def test_gmail_list_messages_pages_through_the_inbox() -> None:
    inbox = [
        InboundEmailSummary(provider_message_id=f"m-{n}", subject=f"Subject {n}") for n in range(3)
    ]
    mailbox = FakeMailbox(inbox=inbox)
    list_messages = named(gmail_toolkit(client=mailbox.factory()), "list_messages")
    context = fake_tool_context(list_messages)

    first = await list_messages.execute({"max_results": 2}, context)
    second = await list_messages.execute(
        {"max_results": 2, "page_token": first["next_page_token"]}, context
    )

    assert [item["subject"] for item in first["messages"]] == ["Subject 0", "Subject 1"]
    assert [item["subject"] for item in second["messages"]] == ["Subject 2"]
    assert second["next_page_token"] is None
    assert mailbox.queries == ["is:unread", "is:unread"]
    with pytest.raises(ValidationError):
        await list_messages.execute({"max_results": 500}, context)


def test_plugins_describe_their_tools_and_env() -> None:
    smtp = plugin_tools("algen_agent_tools.smtp")
    gmail = plugin_tools("algen_agent_tools.gmail")

    assert set(smtp) == {"smtp.send_email"}
    assert set(gmail) == {"gmail.list_messages", "gmail.send_email"}
    assert gmail["gmail.list_messages"].side_effect == SideEffect.READ
    assert "ALGEN_TOOLS_SMTP_PASSWORD" in smtp["smtp.send_email"].env
    assert "ALGEN_TOOLS_GMAIL_REFRESH_TOKEN" in gmail["gmail.send_email"].env


async def test_smtp_client_opens_from_env_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("ALGEN_TOOLS_SMTP_SENDER", "desk@example.org")
    for name in ("PORT", "SECURITY", "USERNAME", "PASSWORD"):
        monkeypatch.delenv(f"ALGEN_TOOLS_SMTP_{name}", raising=False)
    toolkit = smtp_toolkit()

    client: MailClient = await toolkit.client()

    assert client.sender.address == "desk@example.org"
    assert isinstance(client.transport, SmtpEmailSender)
    settings: Any = client.transport._settings
    assert (settings.host, settings.port, settings.security) == (
        "smtp.example.org",
        587,
        "starttls",
    )
    assert settings.username is None and settings.password is None


async def test_smtp_client_reads_every_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "HOST": "smtp.example.org",
        "SENDER": "desk@example.org",
        "PORT": "465",
        "SECURITY": "tls",
        "USERNAME": "desk",
        "PASSWORD": "app-password",
    }
    for name, value in values.items():
        monkeypatch.setenv(f"ALGEN_TOOLS_SMTP_{name}", value)

    client: MailClient = await smtp_toolkit().client()

    settings: Any = client.transport._settings
    assert (settings.port, settings.security, settings.username) == (465, "tls", "desk")
    assert settings.password.get_secret_value() == "app-password"


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [("SECURITY", "ssl", "starttls, tls or plain"), ("PORT", "smtp", "must be a number")],
)
async def test_smtp_client_rejects_bad_settings(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str, message: str
) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("ALGEN_TOOLS_SMTP_SENDER", "desk@example.org")
    monkeypatch.setenv(f"ALGEN_TOOLS_SMTP_{name}", value)

    with pytest.raises(ConfigurationError, match=message):
        await smtp_toolkit().client()


async def test_a_missing_required_setting_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALGEN_TOOLS_SMTP_HOST", raising=False)

    with pytest.raises(ConfigurationError, match="ALGEN_TOOLS_SMTP_HOST"):
        await smtp_toolkit().client()


async def test_gmail_client_opens_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "CLIENT_ID": "id",
        "CLIENT_SECRET": "secret",
        "REFRESH_TOKEN": "refresh",
        "SENDER": "inbox@example.org",
    }.items():
        monkeypatch.setenv(f"ALGEN_TOOLS_GMAIL_{name}", value)
    toolkit = gmail_toolkit()

    client: MailClient = await toolkit.client()

    assert isinstance(client.transport, GmailMailboxClient)
    assert client.sender.address == "inbox@example.org"
    await toolkit.aclose()


async def test_closing_the_toolkit_closes_the_transport() -> None:
    mailbox = FakeMailbox()
    toolkit = smtp_toolkit(client=mailbox.factory())
    await toolkit.client()

    await toolkit.aclose()

    assert mailbox.closed
