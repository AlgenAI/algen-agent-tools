"""Content-minimal failures: an error names what failed and the cause's type, never a payload."""

from __future__ import annotations


def failure(provider: str, operation: str, cause: BaseException) -> str:
    """``"<provider> <operation> failed (<cause type>)"``: safe to log, show and return."""
    return f"{provider} {operation} failed ({type(cause).__name__})"


class EgressRefused(ValueError):
    """A request would leave the hosts the connector's configuration names."""


class ResponseTooLarge(ValueError):
    """A response is larger than the connector's configured limit."""
