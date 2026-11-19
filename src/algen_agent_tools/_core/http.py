"""HTTP that reaches only configured hosts and reads bounded responses."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

import httpx

from algen_agent_tools._core.errors import EgressRefused, ResponseTooLarge


class _RedactUrlSecrets(logging.Filter):
    """Replaces registered secrets in ``httpx`` log records, which include each request's URL."""

    def __init__(self) -> None:
        super().__init__()
        self.secrets: Counter[str] = Counter()

    def filter(self, record: logging.LogRecord) -> bool:
        if self.secrets:
            message = record.getMessage()
            redacted = message
            for secret in self.secrets:
                redacted = redacted.replace(secret, "[redacted]")
            if redacted != message:
                record.msg, record.args = redacted, None
        return True


_REDACT = _RedactUrlSecrets()
logging.getLogger("httpx").addFilter(_REDACT)


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise EgressRefused("connector URLs must be absolute http(s) URLs")
    return parts.scheme, parts.hostname.casefold(), parts.port


def _decoded(response: httpx.Response, body: bytes) -> httpx.Response:
    """A response holding ``body``, which ``aiter_bytes`` already decompressed: without the
    ``Content-Encoding`` and ``Content-Length`` that described the compressed bytes, or reading
    it would decompress it again (a ``DecodingError`` on every gzip reply, which Microsoft
    Graph sends)."""
    headers = httpx.Headers(response.headers)
    for name in ("content-encoding", "content-length"):
        headers.pop(name, None)
    return httpx.Response(
        response.status_code, headers=headers, content=body, request=response.request
    )


class HostLockedClient:
    """An ``httpx`` client limited to the origins of the URLs its configuration names.

    Tool arguments never choose a host: a connector builds every URL from ``base_url`` (or
    another configured URL) and a path, and any request to another origin is refused before it
    is sent. Responses are read up to ``max_response_bytes``. Nothing is retried here; retries
    are the Runtime's decision.

    ``url_secrets`` are secrets an API requires in the URL itself (such as a Telegram bot
    token). They are redacted from ``httpx``'s request log lines while this client is open.
    """

    def __init__(
        self,
        base_url: str,
        *,
        also: Iterable[str] = (),
        timeout_seconds: float,
        max_response_bytes: int,
        client: httpx.AsyncClient | None = None,
        url_secrets: Iterable[str] = (),
    ) -> None:
        self._url_secrets = tuple(secret for secret in url_secrets if secret)
        _REDACT.secrets.update(self._url_secrets)
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
        return _decoded(response, bytes(body))

    async def get(self, url: str, **options: Any) -> httpx.Response:
        return await self.request("GET", url, **options)

    async def post(self, url: str, **options: Any) -> httpx.Response:
        return await self.request("POST", url, **options)

    async def download_preauthenticated(
        self,
        url: str,
        *,
        host_suffixes: Iterable[str],
        max_bytes: int,
    ) -> httpx.Response:
        """GET a pre-authenticated download URL a configured API returned (a Microsoft Graph
        ``@microsoft.graph.downloadUrl``), reading at most ``max_bytes``.

        The URL must be ``https`` on the default port, and its host must end with one of
        ``host_suffixes`` (each starting with a dot, such as ``.sharepoint.com``). No
        ``Authorization`` header is sent, redirects are not followed, and the URL's query
        (which carries a short-lived access grant) is redacted from ``httpx``'s log lines."""
        parts = urlsplit(url)
        host = (parts.hostname or "").casefold()
        suffixes = tuple(suffix.casefold() for suffix in host_suffixes)
        if (
            parts.scheme != "https"
            or parts.port not in (None, 443)
            or parts.username is not None
            or not host
            or not any(suffix.startswith(".") and host.endswith(suffix) for suffix in suffixes)
        ):
            raise EgressRefused("download URL is not on a host the connector is configured for")
        secret = parts.query
        if secret:
            _REDACT.secrets[secret] += 1
        try:
            async with self._client.stream("GET", url, follow_redirects=False) as response:
                body = bytearray()
                if response.is_success:
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            raise ResponseTooLarge("download exceeds the configured size limit")
        finally:
            if secret:
                _REDACT.secrets[secret] -= 1
                _REDACT.secrets += Counter()
        return _decoded(response, bytes(body))

    async def aclose(self) -> None:
        _REDACT.secrets.subtract(self._url_secrets)
        _REDACT.secrets += Counter()  # drop secrets no open client uses
        self._url_secrets = ()
        if self._owns_client:
            await self._client.aclose()
