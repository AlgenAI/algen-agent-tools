"""The tool call in progress, for code a tool reaches only indirectly (such as a transport's
artifact loader) that must still check the caller's tenant."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from algen_agent_runtime.tools.contracts import ToolContext

_CALL: ContextVar[ToolContext | None] = ContextVar("algen_agent_tools_call", default=None)


@contextmanager
def tool_call(context: ToolContext) -> Iterator[None]:
    """Make ``context`` the current call for the duration of the block."""
    token = _CALL.set(context)
    try:
        yield
    finally:
        _CALL.reset(token)


def current_call() -> ToolContext | None:
    """The context of the tool call in progress, or ``None`` outside one."""
    return _CALL.get()
