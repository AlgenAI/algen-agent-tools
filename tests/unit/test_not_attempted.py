"""Refusals before anything is sent are recorded as not attempted (#35 item 1).

The Runtime records a failed external call as "may have happened" (``indeterminate``) unless
the tool says nothing was sent: ``ToolNotAttemptedError``, or, from Runtime 0.1.0a19, a
``PolicyDeniedError``. Each case below is a refusal before the side effect, or a definite
rejection by the provider; timeouts and server errors stay indeterminate.
"""

from __future__ import annotations

import smtplib
from typing import Any

import httpx
import pytest
from algen_agent_runtime.exceptions.errors import (
    PolicyDeniedError,
    ToolExecutionError,
    ToolNotAttemptedError,
)
from algen_agent_runtime.policies.engine import CompositePolicyEngine
from algen_agent_runtime.tools.contracts import ToolExecutionStatus
from algen_agent_runtime.tools.executor import ToolExecutor
from algen_agent_runtime.tools.registry import ToolRegistry
from algen_agent_runtime.tools.testing import fake_tool_context

from algen_agent_tools._core.errors import EgressRefused, not_attempted, rejected
from algen_agent_tools.mail import EmailAddress, EmailDeliveryError, EmailMessage
from algen_agent_tools.mail.fake import FakeMailbox
from algen_agent_tools.messaging import MessagingError, OutgoingMessage
from algen_agent_tools.smtp import smtp_toolkit
from algen_agent_tools.smtp.sender import SmtpEmailSender, SmtpSettings
from tests.unit.test_discord import DiscordApi
from tests.unit.test_outlook import Graph
from tests.unit.test_telegram import BotApi
from tests.unit.test_webhook import Receiver, deliver

MESSAGE = EmailMessage(
    sender=EmailAddress(address="agent@contoso.com"),
    to=(EmailAddress(address="ana@contoso.com"),),
    subject="Hello",
    text_body="Hi.",
)


def test_a_marked_error_keeps_its_type_and_message() -> None:
    for error in (EmailDeliveryError("refused"), RuntimeError("refused")):
        marked = not_attempted(error)
        assert isinstance(marked, type(error)) and isinstance(marked, ToolNotAttemptedError)
        assert str(marked) == "refused"
        assert not_attempted(marked) is marked
    assert isinstance(EgressRefused("x"), ToolNotAttemptedError)


@pytest.mark.parametrize(
    ("status", "definite"), [(400, True), (403, True), (404, True), (429, True), (408, False)]
)
def test_only_4xx_other_than_408_is_a_definite_rejection(status: int, definite: bool) -> None:
    assert rejected(status) is definite
    assert not rejected(500) and not rejected(503) and not rejected(200)


async def test_a_refused_recipient_is_recorded_failed_and_can_be_retried() -> None:
    """The allowlist's PolicyDeniedError, through the Runtime's executor."""
    mailbox = FakeMailbox()
    registry = ToolRegistry()
    for tool in smtp_toolkit(client=mailbox.factory(), allowed_domains=("example.com",)).tools:
        registry.register(tool)
    send = registry.get("smtp.send_email")
    executor = ToolExecutor(registry, CompositePolicyEngine())
    context = fake_tool_context(send)
    arguments = {"to": [{"address": "x@elsewhere.net"}], "subject": "Hi", "text_body": "Hi."}

    with pytest.raises(PolicyDeniedError):
        await executor.execute("smtp.send_email", arguments, context)

    record = await executor.executions.get(context.idempotency_key, context.tenant_id)
    assert record is not None and record.status is ToolExecutionStatus.FAILED
    assert mailbox.sent == []


@pytest.mark.parametrize(("status", "definite"), [(400, True), (403, True), (503, False)])
async def test_outlook_send_rejections(status: int, definite: bool) -> None:
    client = Graph(fail=status).client()
    with pytest.raises(EmailDeliveryError) as raised:
        await client.send(MESSAGE, idempotency_key="k")
    assert isinstance(raised.value, ToolNotAttemptedError) is definite


async def test_outlook_refusals_before_the_send_are_not_attempted() -> None:
    def refuse_token(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    graph = Graph()
    graph.handler = refuse_token  # type: ignore[method-assign]
    with pytest.raises(EmailDeliveryError) as raised:
        await graph.client().send(MESSAGE, idempotency_key="k")
    assert isinstance(raised.value, ToolNotAttemptedError)

    with_file = MESSAGE.model_copy(
        update={
            "attachments": (
                {"artifact_id": "a", "filename": "a.pdf", "media_type": "application/pdf"},
            )
        }
    )
    with_file = EmailMessage.model_validate(with_file.model_dump())
    with pytest.raises(EmailDeliveryError, match="artifact loader") as raised:
        await Graph().client().send(with_file, idempotency_key="k")
    assert isinstance(raised.value, ToolNotAttemptedError)


class _Smtp:
    def __init__(self, error: Exception) -> None:
        self.error = error

    def ehlo(self) -> None: ...

    def starttls(self, **options: Any) -> None: ...

    def login(self, *args: Any) -> None: ...

    def send_message(self, *args: Any, **options: Any) -> Any:
        raise self.error

    def quit(self) -> None: ...

    def close(self) -> None: ...


@pytest.mark.parametrize(
    ("error", "definite"),
    [
        (smtplib.SMTPRecipientsRefused({"a@b.c": (550, b"no")}), True),
        (smtplib.SMTPSenderRefused(550, b"no", "a@b.c"), True),
        (smtplib.SMTPDataError(554, b"rejected"), True),
        (smtplib.SMTPServerDisconnected("gone"), False),
        (TimeoutError(), False),
    ],
)
async def test_smtp_refusals(error: Exception, definite: bool) -> None:
    sender = SmtpEmailSender(
        SmtpSettings(host="smtp.example.com", security="tls"),
        smtp_factory=lambda settings: _Smtp(error),
    )
    with pytest.raises(EmailDeliveryError) as raised:
        await sender.send(MESSAGE, idempotency_key="k")
    assert isinstance(raised.value, ToolNotAttemptedError) is definite


@pytest.mark.parametrize(("code", "definite"), [(400, True), (403, True), (502, False)])
async def test_telegram_send_rejections(code: int, definite: bool) -> None:
    api = BotApi(error={"ok": False, "error_code": code, "description": "x"})
    with pytest.raises(MessagingError) as raised:
        await api.client().send(
            OutgoingMessage(conversation_id="42", text="hi"), idempotency_key="k"
        )
    assert isinstance(raised.value, ToolNotAttemptedError) is definite


@pytest.mark.parametrize(("status", "definite"), [(403, True), (500, False)])
async def test_discord_rejections(status: int, definite: bool) -> None:
    client = DiscordApi(status=status).client()
    with pytest.raises(MessagingError) as raised:
        await client.send(
            OutgoingMessage(conversation_id="300000000000000001", text="hi"), idempotency_key="k"
        )
    assert isinstance(raised.value, ToolNotAttemptedError) is definite


@pytest.mark.parametrize(("status", "definite"), [(400, True), (422, True), (500, False)])
async def test_webhook_rejections(status: int, definite: bool) -> None:
    with pytest.raises(ToolExecutionError) as raised:
        await deliver(Receiver(status), {"summary": "Disk full", "severity": "critical"})
    assert isinstance(raised.value, ToolNotAttemptedError) is definite
