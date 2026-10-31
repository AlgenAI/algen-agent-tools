"""Content-minimal failures: an error names what failed and the cause's type, never a payload.

A failure before anything was sent (a refused recipient, a missing artifact loader, a token
that could not be fetched, a definite rejection by the provider) is marked *not attempted*, so
the Runtime records the call ``failed`` rather than ``indeterminate`` and a retry runs instead
of asking for manual reconciliation. Timeouts and server errors stay unmarked: the request may
have been carried out.
"""

from __future__ import annotations

from algen_agent_runtime.exceptions.errors import ToolNotAttemptedError

_NOT_ATTEMPTED: dict[type[Exception], type[Exception]] = {}


def failure(provider: str, operation: str, cause: BaseException) -> str:
    """``"<provider> <operation> failed (<cause type>)"``: safe to log, show and return."""
    return f"{provider} {operation} failed ({type(cause).__name__})"


def not_attempted[E: Exception](error: E) -> E:
    """A copy of ``error`` that is also a :class:`ToolNotAttemptedError`.

    The copy keeps the error's type, message and attributes, so code catching the connector's
    own error (an ``EmailDeliveryError``, say) still catches it; the Runtime's executor sees
    that nothing was sent.
    """
    if isinstance(error, ToolNotAttemptedError):
        return error
    kind = type(error)
    marked = _NOT_ATTEMPTED.get(kind)
    if marked is None:
        marked = type(
            kind.__name__,
            (kind, ToolNotAttemptedError),
            {"__module__": kind.__module__, "__qualname__": kind.__qualname__},
        )
        _NOT_ATTEMPTED[kind] = marked
    copy: E = marked.__new__(marked)  # type: ignore[assignment]
    copy.args = error.args
    copy.__dict__.update(error.__dict__)
    copy.__cause__, copy.__context__ = error.__cause__, error.__context__
    return copy


def rejected(status_code: int) -> bool:
    """Whether an HTTP status is a definite refusal: a 4xx other than 408 (the server gave up
    waiting for the request, so it may not have read it all)."""
    return 400 <= status_code < 500 and status_code != 408


class EgressRefused(ValueError, ToolNotAttemptedError):
    """A request would leave the hosts the connector's configuration names. Nothing is sent."""


class ResponseTooLarge(ValueError):
    """A response is larger than the connector's configured limit."""
