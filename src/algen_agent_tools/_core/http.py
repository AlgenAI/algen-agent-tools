"""HTTP that reaches only configured hosts and reads bounded responses."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import httpx

from algen_agent_tools._core.errors import EgressRefused, ResponseTooLarge


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise EgressRefused("connector URLs must be absolute http(s) URLs")
    return parts.scheme, parts.hostname.casefold(), parts.port


class HostLockedClient:
    """An ``httpx`` client limited to the origins of the URLs its configuration names.

    Tool arguments never choose a host: a connector builds every URL from ``base_url`` (or
    another configured URL) and a path, and any request to another origin is refused before it
    is sent. Responses are read up to ``max_response_bytes``. Nothing is retried here; retries
    are the Runtime's decision.
    """

    def __init__(
        self,
        base_url: str,
        *,
        also: Iterable[str] = (),
        timeout_seconds: float,
        max_response_bytes: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._origins = frozenset(_origin(url) for url in (base_url, *also))
        self._max_response_bytes = max_response_bytes
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)
        self._owns_client = client is None

    def url(self, path: str) -> str:
        """``path`` under the base URL."""
        return f"{self.base_url}/{path.lstrip('/')}"

    async def request(self, method: str, url: str, **options: Any) -> httpx.Response:
        """Send one request to a configured origin and read its body within the limit."""
        if _origin(url) not in self._origins:
            raise EgressRefused("request to a host the connector is not configured for")
        async with self._client.stream(method, url, **options) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > self._max_response_bytes:
                    raise ResponseTooLarge("response exceeds the configured size limit")
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=bytes(body),
            request=response.request,
        )

    async def get(self, url: str, **options: Any) -> httpx.Response:
        return await self.request("GET", url, **options)

    async def post(self, url: str, **options: Any) -> httpx.Response:
        return await self.request("POST", url, **options)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
