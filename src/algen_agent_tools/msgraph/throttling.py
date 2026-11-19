"""Microsoft Graph throttling: ``429 Too Many Requests`` and ``503`` with ``Retry-After``.

Connectors do not retry (retries are the Runtime's decision, or the host's for a batch job):
they raise an error carrying how long Graph asked the caller to wait, so a backfill or import
can sleep that long and continue."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

THROTTLED_STATUSES = frozenset({429, 503})
DEFAULT_RETRY_AFTER_SECONDS = 10.0
"""The wait when Graph throttles without a usable ``Retry-After``."""
MAX_RETRY_AFTER_SECONDS = 3_600.0


class GraphThrottled(Exception):
    """Graph throttled the request (HTTP 429 or 503). Wait ``retry_after_seconds`` before the
    next request. Connectors raise it mixed into their own error type, so callers that catch
    the connector's error still catch it."""

    retry_after_seconds: float = DEFAULT_RETRY_AFTER_SECONDS
    status_code: int = 429


def retry_after_seconds(headers: httpx.Headers, *, now: datetime | None = None) -> float:
    """The wait ``Retry-After`` asks for (seconds or an HTTP date), bounded to an hour, or
    :data:`DEFAULT_RETRY_AFTER_SECONDS` when it is missing or unreadable."""
    value = (headers.get("retry-after") or "").strip()
    if not value:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        seconds = float(value)
    except ValueError:
        try:
            moment = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return DEFAULT_RETRY_AFTER_SECONDS
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        seconds = (moment - (now or datetime.now(UTC))).total_seconds()
    if seconds != seconds:  # NaN
        return DEFAULT_RETRY_AFTER_SECONDS
    return min(MAX_RETRY_AFTER_SECONDS, max(0.0, seconds))


def throttled[E: Exception](error: E, response: httpx.Response) -> E:
    """``error`` as a :class:`GraphThrottled` too, with the response's wait. Its type stays a
    subclass of the connector's own error type."""
    kind = type(error)
    marked = _THROTTLED.get(kind)
    if marked is None:
        marked = type(
            kind.__name__,
            (kind, GraphThrottled),
            {"__module__": kind.__module__, "__qualname__": kind.__qualname__},
        )
        _THROTTLED[kind] = marked
    copy: E = marked.__new__(marked)  # type: ignore[assignment]
    copy.args = error.args
    copy.__dict__.update(error.__dict__)
    copy.__cause__, copy.__context__ = error.__cause__, error.__context__
    copy.retry_after_seconds = retry_after_seconds(response.headers)  # type: ignore[attr-defined]
    copy.status_code = response.status_code  # type: ignore[attr-defined]
    return copy


_THROTTLED: dict[type[Exception], type[Exception]] = {}


def throttled_response(cause: BaseException) -> httpx.Response | None:
    """The response of an ``HTTPStatusError`` that is Graph throttling, else ``None``."""
    if isinstance(cause, httpx.HTTPStatusError):
        if cause.response.status_code in THROTTLED_STATUSES:
            return cause.response
    return None
