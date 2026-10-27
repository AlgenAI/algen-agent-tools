"""Typed outbound webhooks: one external action per configured target.

The deployment fixes each target's URL (an ``env://`` setting) and payload model; the model
fills in only the typed payload. Each delivery is a JSON ``POST`` that carries the Runtime's
idempotency key and, with a signing secret, an HMAC-SHA256 signature over a timestamp and the
body. Redirects are not followed, and the URL never appears in an error or a log line.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from algen_agent_runtime.exceptions.errors import ConfigurationError, ToolExecutionError
from algen_agent_runtime.tools.authoring import Toolkit, external
from algen_agent_runtime.tools.contracts import ToolContext
from pydantic import BaseModel, ConfigDict

from algen_agent_tools import __version__
from algen_agent_tools._core.errors import failure
from algen_agent_tools._core.http import HostLockedClient
from algen_agent_tools._env import SecretResolver, env_name, env_names, resolve_optional

_ACTION = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_BODY_BYTES = 256_000
SIGNATURE_HEADER = "X-Algen-Signature"
IDEMPOTENCY_HEADER = "Idempotency-Key"


class WebhookDelivery(BaseModel):
    """What a delivery reports: the receiver's status, never its response body."""

    action: str
    status_code: int


@dataclass(frozen=True)
class WebhookTarget:
    """One webhook the model may trigger, as the tool ``<toolkit>.<action>``.

    ``url`` and ``signing_secret`` are ``env://`` references resolved when the client opens.
    ``payload`` is the model the tool's input must match; unknown fields are refused.
    """

    action: str
    url: str
    payload: type[BaseModel]
    description: str
    signing_secret: str | None = None
    timeout_seconds: float = 30
    approval: bool = False
    """Ask a person before every delivery; ``False`` leaves it to the agent's approval policy."""
    allow_http: bool = False
    """Allow a plain ``http://`` URL (for a receiver on a private network)."""

    def __post_init__(self) -> None:
        if not _ACTION.fullmatch(self.action):
            raise ConfigurationError(
                f"webhook action {self.action!r} must be lowercase letters, digits and _"
            )
        env_name(self.url)
        if self.signing_secret is not None:
            env_name(self.signing_secret)


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """The signature header value: ``t=<timestamp>,v1=<hex HMAC-SHA256 of "<t>." + body>``."""
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={digest.hexdigest()}"


def verify(secret: str, header: str, body: bytes, *, tolerance_seconds: int = 300) -> bool:
    """Check a signature header as a receiver should: the HMAC matches and the timestamp is
    within ``tolerance_seconds`` of now. Receivers in Python can import this."""
    parts = dict(item.split("=", 1) for item in header.split(",") if "=" in item)
    try:
        timestamp = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(time.time() - timestamp) > tolerance_seconds:
        return False
    expected = sign(secret, timestamp, body).split("v1=", 1)[1]
    return hmac.compare_digest(expected, parts.get("v1", ""))


@dataclass
class _Resolved:
    url: str
    secret: str | None


@dataclass
class WebhookClient:
    """Resolved target URLs and secrets, and the HTTP client limited to their origins."""

    http: Any
    targets: dict[str, _Resolved] = field(default_factory=dict)

    async def aclose(self) -> None:
        await self.http.aclose()


def _check_url(url: str, target: WebhookTarget) -> None:
    parts = urlsplit(url)
    allowed = ("https", "http") if target.allow_http else ("https",)
    if parts.scheme not in allowed or not parts.hostname:
        # Never echo the URL: it often carries a token.
        raise ConfigurationError(
            f"webhook {target.action!r}: the URL must be an absolute {' or '.join(allowed)} URL"
        )
    if parts.username or parts.password:
        raise ConfigurationError(f"webhook {target.action!r}: the URL must not hold credentials")


def _url_secrets(url: str) -> tuple[str, ...]:
    """The forms of ``url`` that can appear in an httpx log line."""
    parsed = httpx.URL(url)
    return tuple({url, str(parsed), parsed.raw_path.decode("ascii", "replace")} - {"", "/"})


def _strict(payload: type[BaseModel], action: str) -> type[BaseModel]:
    if payload.model_config.get("extra") == "forbid":
        return payload
    strict: type[BaseModel] = type(
        f"{action}_payload", (payload,), {"model_config": ConfigDict(extra="forbid")}
    )
    return strict


async def open_webhook_client(
    targets: Sequence[WebhookTarget],
    secrets: SecretResolver,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> WebhookClient:
    """Resolve and check every target's URL and secret, and open the HTTP client limited to
    their origins. ``http_client`` replaces the transport, for tests."""
    resolved: dict[str, _Resolved] = {}
    for target in targets:
        url = await secrets(target.url)
        _check_url(url, target)
        secret = await resolve_optional(secrets, target.signing_secret)
        if target.signing_secret is not None and not secret:
            raise ConfigurationError(f"webhook {target.action!r}: the signing secret is unset")
        resolved[target.action] = _Resolved(url, secret)
    urls = [item.url for item in resolved.values()]
    http = HostLockedClient(
        urls[0],
        also=urls[1:],
        timeout_seconds=max(target.timeout_seconds for target in targets),
        max_response_bytes=64_000,
        client=http_client,
        url_secrets=tuple(s for url in urls for s in _url_secrets(url)),
    )
    return WebhookClient(http, resolved)


def webhook_toolkit(
    targets: Sequence[WebhookTarget],
    name: str = "webhook",
    *,
    client: Any = None,
) -> Toolkit:
    """A toolkit with one external tool per target: ``<name>.<action>`` posts the typed
    payload to the target's configured URL and returns the receiver's status."""
    if not targets:
        raise ConfigurationError("webhook_toolkit needs at least one target")
    actions = [target.action for target in targets]
    if len(set(actions)) != len(actions):
        raise ConfigurationError("webhook actions must be unique")

    async def open_client(secrets: SecretResolver) -> WebhookClient:
        return await open_webhook_client(targets, secrets)

    references = [ref for target in targets for ref in (target.url, target.signing_secret)]
    toolkit = Toolkit(name, env=env_names(*references), client=client or open_client)
    for target in targets:
        _add_target(toolkit, target)
    return toolkit


def _add_target(toolkit: Toolkit, target: WebhookTarget) -> None:
    payload_model = _strict(target.payload, target.action)

    async def deliver(payload: BaseModel, context: ToolContext) -> WebhookDelivery:
        webhook: WebhookClient = await toolkit.client()
        resolved = webhook.targets[target.action]
        body = json.dumps(payload.model_dump(mode="json"), separators=(",", ":")).encode()
        if len(body) > MAX_BODY_BYTES:
            raise ToolExecutionError(f"webhook {target.action!r} payload is too large")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"algen-agent-tools/{__version__}",
            IDEMPOTENCY_HEADER: context.idempotency_key,
        }
        if resolved.secret is not None:
            headers[SIGNATURE_HEADER] = sign(resolved.secret, int(time.time()), body)
        try:
            response = await webhook.http.post(
                resolved.url,
                content=body,
                headers=headers,
                timeout=target.timeout_seconds,
                follow_redirects=False,
            )
        except (httpx.HTTPError, ValueError) as exc:
            # The httpx message names the URL; keep only the cause's type.
            raise ToolExecutionError(failure("webhook", target.action, exc)) from None
        if not 200 <= response.status_code < 300:
            raise ToolExecutionError(
                f"webhook {target.action!r} failed (HTTP {response.status_code})"
            )
        return WebhookDelivery(action=target.action, status_code=response.status_code)

    deliver.__name__ = deliver.__qualname__ = target.action
    deliver.__doc__ = target.description
    deliver.__annotations__ = {
        "payload": payload_model,
        "context": ToolContext,
        "return": WebhookDelivery,
    }
    toolkit.tool(
        external(timeout=target.timeout_seconds, approval=target.approval),
        max_result_bytes=4_000,
        audit_metadata={"content_capture": "disabled", "channel": "webhook"},
    )(deliver)
