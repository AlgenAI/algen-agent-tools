"""The webhook toolkit against a mocked receiver."""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Literal

import httpx
import pytest
from algen_agent_runtime.exceptions.errors import ConfigurationError, ToolExecutionError
from algen_agent_runtime.tools.contracts import SideEffect, Tool
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import BaseModel, ValidationError

from algen_agent_tools.testing import assert_toolkit_contract
from algen_agent_tools.webhook import (
    WebhookTarget,
    open_webhook_client,
    sign,
    verify,
    webhook_toolkit,
)

URL = "https://hooks.example.com/services/T000/B000/secret-path-token"
SECRET = "whsec-test-signing-secret"


class OpsAlert(BaseModel):
    severity: Literal["info", "warning", "critical"]
    summary: str


ALERT = WebhookTarget(
    action="notify_ops",
    url="env://ALGEN_TOOLS_WEBHOOK_OPS_URL",
    payload=OpsAlert,
    description="Post an alert to the operations channel.",
    signing_secret="env://ALGEN_TOOLS_WEBHOOK_OPS_SECRET",
)
SETTINGS = {
    "env://ALGEN_TOOLS_WEBHOOK_OPS_URL": URL,
    "env://ALGEN_TOOLS_WEBHOOK_OPS_SECRET": SECRET,
}


class Receiver:
    def __init__(self, status: int = 200, *, fail: bool = False) -> None:
        self.requests: list[httpx.Request] = []
        self.status = status
        self.fail = fail

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)
        headers = {"Location": "https://elsewhere.example/x"} if self.status == 302 else {}
        return httpx.Response(self.status, headers=headers, text="receiver body")

    def factory(self, targets: tuple[WebhookTarget, ...] = (ALERT,), **settings: str) -> Any:
        values = {**SETTINGS, **settings}

        async def secrets(reference: str) -> str:
            if reference not in values:
                raise ConfigurationError(f"{reference} is not set")
            return values[reference]

        async def open_client(_: Any) -> Any:
            return await open_webhook_client(
                targets,
                secrets,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)),
            )

        return open_client


def tool(toolkit: Any, action: str = "notify_ops") -> Tool:
    return next(t for t in toolkit.tools if t.definition.name == f"webhook.{action}")


async def deliver(receiver: Receiver, payload: dict[str, Any], **settings: str) -> Any:
    target = tool(webhook_toolkit([ALERT], client=receiver.factory(**settings)))
    return await target.execute(payload, fake_tool_context(target))


def test_each_target_is_one_external_tool() -> None:
    toolkit = webhook_toolkit([ALERT])
    definition = tool(toolkit).definition
    assert definition.side_effect is SideEffect.EXTERNAL
    assert definition.description == "Post an alert to the operations channel."
    assert definition.required_permissions == frozenset({"webhook.write"})
    assert set(toolkit.env) == {
        "ALGEN_TOOLS_WEBHOOK_OPS_URL",
        "ALGEN_TOOLS_WEBHOOK_OPS_SECRET",
    }
    schema = definition.input_schema
    assert set(schema["properties"]) == {"severity", "summary"}
    assert schema["additionalProperties"] is False
    assert_toolkit_contract(toolkit)


async def test_a_delivery_posts_signed_json_with_the_idempotency_key() -> None:
    receiver = Receiver()
    target = tool(webhook_toolkit([ALERT], client=receiver.factory()))
    context = fake_tool_context(target)
    result = await target.execute({"severity": "critical", "summary": "disk full"}, context)
    assert result == {"action": "notify_ops", "status_code": 200}

    [request] = receiver.requests
    assert str(request.url) == URL
    assert json.loads(request.content) == {"severity": "critical", "summary": "disk full"}
    assert request.headers["Idempotency-Key"] == context.idempotency_key
    assert request.headers["Content-Type"] == "application/json"
    assert verify(SECRET, request.headers["X-Algen-Signature"], request.content)
    assert not verify("other-secret", request.headers["X-Algen-Signature"], request.content)
    assert not verify(SECRET, request.headers["X-Algen-Signature"], request.content + b" ")


def test_verify_refuses_old_or_malformed_signatures() -> None:
    body = b"{}"
    assert not verify(SECRET, sign(SECRET, int(time.time()) - 3_600, body), body)
    assert not verify(SECRET, "v1=abc", body)
    assert not verify(SECRET, "t=x,v1=abc", body)


async def test_unsigned_targets_send_no_signature() -> None:
    receiver = Receiver()
    unsigned = WebhookTarget(
        action="notify_ops",
        url="env://ALGEN_TOOLS_WEBHOOK_OPS_URL",
        payload=OpsAlert,
        description="x",
    )
    target = tool(webhook_toolkit([unsigned], client=receiver.factory((unsigned,))))
    await target.execute({"severity": "info", "summary": "ok"}, fake_tool_context(target))
    assert "X-Algen-Signature" not in receiver.requests[0].headers


async def test_the_payload_is_typed_and_closed() -> None:
    for payload in (
        {"severity": "urgent", "summary": "x"},
        {"severity": "info", "summary": "x", "url": "https://evil.example"},
    ):
        with pytest.raises(ValidationError):
            await deliver(Receiver(), payload)


@pytest.mark.parametrize("status", [302, 404, 500])
async def test_non_success_is_an_error_without_the_url_or_body(status: int) -> None:
    receiver = Receiver(status)
    with pytest.raises(ToolExecutionError) as caught:
        await deliver(receiver, {"severity": "info", "summary": "x"})
    assert str(caught.value) == f"webhook 'notify_ops' failed (HTTP {status})"
    assert len(receiver.requests) == 1  # a redirect is not followed


async def test_transport_errors_never_carry_the_url() -> None:
    with pytest.raises(ToolExecutionError) as caught:
        await deliver(Receiver(fail=True), {"severity": "info", "summary": "x"})
    assert str(caught.value) == "webhook notify_ops failed (ConnectError)"
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://hooks.example.com/x", "must be an absolute https URL"),
        ("ftp://hooks.example.com/x", "must be an absolute https URL"),
        ("https://user:pw@hooks.example.com/x", "must not hold credentials"),
    ],
)
async def test_urls_are_checked_without_being_echoed(url: str, message: str) -> None:
    with pytest.raises(ConfigurationError, match=message) as caught:
        await deliver(Receiver(), {"severity": "info", "summary": "x"}, **{ALERT.url: url})
    assert url not in str(caught.value)


async def test_a_signing_secret_that_is_unset_is_an_error() -> None:
    with pytest.raises(ConfigurationError, match="signing secret is unset"):
        await deliver(
            Receiver(), {"severity": "info", "summary": "x"}, **{ALERT.signing_secret: ""}
        )


async def test_request_logs_redact_the_url(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="httpx"):
        await deliver(Receiver(), {"severity": "info", "summary": "x"})
    assert caplog.records
    assert "secret-path-token" not in caplog.text


def test_targets_are_validated() -> None:
    with pytest.raises(ConfigurationError, match="lowercase"):
        WebhookTarget(action="Notify", url=ALERT.url, payload=OpsAlert, description="x")
    with pytest.raises(ConfigurationError, match="env://"):
        WebhookTarget(action="n", url="https://literal.example", payload=OpsAlert, description="x")
    with pytest.raises(ConfigurationError, match="unique"):
        webhook_toolkit([ALERT, ALERT])
    with pytest.raises(ConfigurationError, match="at least one"):
        webhook_toolkit([])
