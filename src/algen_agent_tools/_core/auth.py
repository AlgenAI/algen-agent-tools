"""Access tokens for HTTP connectors, cached until shortly before they expire."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import SecretStr

from algen_agent_tools._core.errors import not_attempted
from algen_agent_tools._core.http import HostLockedClient

REFRESH_MARGIN_SECONDS = 60
"""A token is renewed this long before it expires, so a call never starts with a stale one."""

RefreshTokenSink = Callable[[SecretStr], Awaitable[None]]
"""Stores a rotated refresh token in the host's secret store (for the next process start)."""

logger = logging.getLogger(__name__)


class _CachedToken:
    """An OAuth 2.0 access token from one grant, shared by concurrent calls.

    ``on_error`` turns a failure's cause into the connector's own exception, so the message
    names the type and never the token endpoint's response.
    """

    def __init__(
        self,
        http: HostLockedClient,
        *,
        token_uri: str,
        on_error: Callable[[BaseException], Exception],
    ) -> None:
        self._http = http
        self._token_uri = token_uri
        self._on_error = on_error
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def _grant(self) -> dict[str, str]:
        raise NotImplementedError

    async def _accepted(self, payload: Any) -> None:
        """Called with each successful token response."""

    def _fresh(self) -> str | None:
        if self._token and time.monotonic() < self._expires_at - REFRESH_MARGIN_SECONDS:
            return self._token
        return None

    async def token(self) -> str:
        if fresh := self._fresh():
            return fresh
        async with self._lock:
            if fresh := self._fresh():
                return fresh
            try:
                response = await self._http.post(self._token_uri, data=self._grant())
                response.raise_for_status()
                payload = response.json()
                token = str(payload["access_token"])
                expires_in = max(60, int(payload.get("expires_in", 3600)))
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                # A token is fetched before the call it authorizes: that call was not sent.
                raise not_attempted(self._on_error(exc)) from exc
            await self._accepted(payload)
            self._token = token
            self._expires_at = time.monotonic() + expires_in
            return token


class OAuthRefresh(_CachedToken):
    """An access token from a refresh token (delegated access to one user's data).

    A provider that rotates refresh tokens returns a new one with the access token and
    invalidates the old one: the new one is used for the next refresh, and handed to
    ``on_rotate`` so the host can store it (otherwise the next process start uses a token that
    no longer works). A failing ``on_rotate`` is logged, without the token, and the call goes on.
    """

    def __init__(
        self,
        http: HostLockedClient,
        *,
        token_uri: str,
        client_id: str,
        client_secret: SecretStr,
        refresh_token: SecretStr,
        on_error: Callable[[BaseException], Exception],
        scope: str | None = None,
        on_rotate: RefreshTokenSink | None = None,
    ) -> None:
        super().__init__(http, token_uri=token_uri, on_error=on_error)
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._scope = scope
        self._on_rotate = on_rotate

    async def _accepted(self, payload: Any) -> None:
        rotated = payload.get("refresh_token") if isinstance(payload, dict) else None
        if not isinstance(rotated, str) or not rotated:
            return
        if rotated == self._refresh_token.get_secret_value():
            return
        self._refresh_token = SecretStr(rotated)
        if self._on_rotate is None:
            logger.warning(
                "the OAuth provider rotated the refresh token; it is kept in memory only, so "
                "pass on_refresh_token to store it"
            )
            return
        try:
            await self._on_rotate(self._refresh_token)
        except Exception as exc:
            logger.warning("storing the rotated refresh token failed (%s)", type(exc).__name__)

    def _grant(self) -> dict[str, str]:
        grant = {
            "client_id": self._client_id,
            "client_secret": self._client_secret.get_secret_value(),
            "refresh_token": self._refresh_token.get_secret_value(),
            "grant_type": "refresh_token",
        }
        if self._scope:
            grant["scope"] = self._scope
        return grant


class OAuthClientCredentials(_CachedToken):
    """An app-only access token from the client's own credentials (no user)."""

    def __init__(
        self,
        http: HostLockedClient,
        *,
        token_uri: str,
        client_id: str,
        client_secret: SecretStr,
        scope: str,
        on_error: Callable[[BaseException], Exception],
    ) -> None:
        super().__init__(http, token_uri=token_uri, on_error=on_error)
        self._client_id = client_id
        self._client_secret = client_secret
        self._scope = scope

    def _grant(self) -> dict[str, str]:
        return {
            "client_id": self._client_id,
            "client_secret": self._client_secret.get_secret_value(),
            "scope": self._scope,
            "grant_type": "client_credentials",
        }
