"""The private kernel: host-locked HTTP, cached OAuth tokens and the current tool call."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

import httpx
import pytest
from algen_agent_runtime.tools.authoring import Toolkit, read
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import SecretStr

from algen_agent_tools._core.auth import OAuthRefresh
from algen_agent_tools._core.context import current_call, tool_call
from algen_agent_tools._core.errors import EgressRefused, ResponseTooLarge, failure
from algen_agent_tools._core.http import HostLockedClient

BASE = "https://api.example.com/v1"
TOKEN_URI = "https://auth.example.com/token"


def locked(handler: httpx.MockTransport, *, limit: int = 1_000) -> HostLockedClient:
    return HostLockedClient(
        BASE,
        also=(TOKEN_URI,),
        timeout_seconds=5,
        max_response_bytes=limit,
        client=httpx.AsyncClient(transport=handler),
    )


def answer(body: bytes = b'{"ok": true}', status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, content=body))


async def test_requests_reach_only_the_configured_origins() -> None:
    http = locked(answer())
    assert http.url("/items/1") == "https://api.example.com/v1/items/1"
    assert (await http.get(http.url("items"))).json() == {"ok": True}
    assert (await http.post(TOKEN_URI)).status_code == 200
    for url in (
        "https://evil.example.com/v1/items",
        "http://api.example.com/v1/items",  # another scheme is another origin
        "https://api.example.com:8443/v1/items",
        "file:///etc/passwd",
        "/relative",
    ):
        with pytest.raises(EgressRefused):
            await http.get(url)


async def test_responses_are_read_within_the_limit() -> None:
    assert (await locked(answer(b"x" * 1_000)).get(BASE)).content == b"x" * 1_000
    with pytest.raises(ResponseTooLarge):
        await locked(answer(b"x" * 1_001)).get(BASE)


async def test_errors_keep_their_status_for_raise_for_status() -> None:
    response = await locked(answer(b"nope", status=503)).get(BASE)
    with pytest.raises(httpx.HTTPStatusError):
        response.raise_for_status()


async def test_a_client_it_created_is_closed_and_a_given_one_is_not() -> None:
    given = httpx.AsyncClient(transport=answer())
    await HostLockedClient(BASE, timeout_seconds=1, max_response_bytes=1, client=given).aclose()
    assert not given.is_closed
    owned = HostLockedClient(BASE, timeout_seconds=1, max_response_bytes=1)
    await owned.aclose()
    assert owned._client.is_closed


def oauth(transport: httpx.MockTransport) -> OAuthRefresh:
    return OAuthRefresh(
        locked(transport),
        token_uri=TOKEN_URI,
        client_id="client",
        client_secret=SecretStr("client-secret"),
        refresh_token=SecretStr("refresh-secret"),
        on_error=lambda exc: RuntimeError(failure("Example", "OAuth token request", exc)),
    )


async def test_a_token_is_fetched_once_and_shared_by_concurrent_calls() -> None:
    calls: list[bytes] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.content)
        await asyncio.sleep(0)
        return httpx.Response(200, json={"access_token": "access", "expires_in": 3600})

    tokens = oauth(httpx.MockTransport(handler))
    assert await asyncio.gather(*(tokens.token() for _ in range(5))) == ["access"] * 5
    assert len(calls) == 1
    assert b"grant_type=refresh_token" in calls[0]


async def test_a_token_near_expiry_is_renewed() -> None:
    issued = iter(("first", "second"))
    tokens = oauth(
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"access_token": next(issued), "expires_in": 60}
            )
        )
    )
    assert await tokens.token() == "first"
    assert await tokens.token() == "second"  # 60 s is inside the renewal margin


async def test_a_rotated_refresh_token_is_used_next_and_handed_to_the_host() -> None:
    """Providers that rotate refresh tokens invalidate the old one (#35 item 4)."""
    sent: list[str] = []
    issued = iter(("refresh-2", "refresh-3"))

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(parse_qs(request.content.decode())["refresh_token"][0])
        return httpx.Response(
            200, json={"access_token": "access", "expires_in": 60, "refresh_token": next(issued)}
        )

    stored: list[str] = []

    async def store(token: SecretStr) -> None:
        stored.append(token.get_secret_value())

    tokens = OAuthRefresh(
        locked(httpx.MockTransport(handler)),
        token_uri=TOKEN_URI,
        client_id="client",
        client_secret=SecretStr("client-secret"),
        refresh_token=SecretStr("refresh-1"),
        on_error=lambda exc: RuntimeError(failure("Example", "OAuth token request", exc)),
        on_rotate=store,
    )
    await tokens.token()
    await tokens.token()  # 60 s is inside the renewal margin: refreshes again

    assert sent == ["refresh-1", "refresh-2"]
    assert stored == ["refresh-2", "refresh-3"]


async def test_a_host_that_cannot_store_the_rotated_token_does_not_fail_the_call(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(token: SecretStr) -> None:
        raise OSError("vault unavailable")

    tokens = OAuthRefresh(
        locked(
            httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"access_token": "access", "refresh_token": "refresh-2"}
                )
            )
        ),
        token_uri=TOKEN_URI,
        client_id="client",
        client_secret=SecretStr("client-secret"),
        refresh_token=SecretStr("refresh-1"),
        on_error=lambda exc: RuntimeError(failure("Example", "OAuth token request", exc)),
        on_rotate=broken,
    )
    assert await tokens.token() == "access"
    assert "OSError" in caplog.text and "refresh-2" not in caplog.text


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(400, json={"error": "invalid_grant", "detail": "refresh-secret"}),
        httpx.Response(200, json={"no_token": True}),
        httpx.Response(200, content=b"not json"),
    ],
)
async def test_token_failures_name_only_the_cause_type(response: httpx.Response) -> None:
    tokens = oauth(httpx.MockTransport(lambda request: response))
    with pytest.raises(RuntimeError) as raised:
        await tokens.token()
    assert str(raised.value).startswith("Example OAuth token request failed (")
    assert "secret" not in str(raised.value)


def test_the_current_call_is_set_only_inside_the_block() -> None:
    toolkit = Toolkit("demo")

    @toolkit.tool(read())
    def ping() -> str:
        """Ping."""
        return "pong"

    outer, inner = fake_tool_context(ping), fake_tool_context(ping, idempotency_key="inner")
    assert current_call() is None
    with tool_call(outer):
        assert current_call() is outer
        with tool_call(inner):
            assert current_call() is inner
        assert current_call() is outer
    assert current_call() is None
