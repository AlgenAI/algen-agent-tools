"""An in-memory MCP session for offline tests of an overlay."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

ToolBody = Callable[[dict[str, Any]], Any]


@dataclass
class FakeMcpServer:
    """Advertises tools and answers calls.

    ``server.add("get_ticket", schema, lambda args: {...})``; a body returning a ``dict`` is sent
    as structured content (and its JSON as text), a ``str`` as text. Raising inside a body
    reports an MCP error result. Use :meth:`session_factory` as the overlay's
    ``session_factory``.
    """

    tools: dict[str, tuple[dict[str, Any], ToolBody]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    page_size: int = 50
    connections: int = 0

    def add(self, name: str, input_schema: dict[str, Any], body: ToolBody) -> FakeMcpServer:
        self.tools[name] = (input_schema, body)
        return self

    async def session_factory(self, config: Any, stack: AsyncExitStack) -> FakeMcpServer:
        self.connections += 1
        return self

    async def list_tools(self, cursor: str | None = None) -> ListToolsResult:
        names = sorted(self.tools)
        start = int(cursor or 0)
        page = names[start : start + self.page_size]
        following = start + self.page_size
        return ListToolsResult(
            tools=[
                Tool(name=name, description=f"Fake {name}", inputSchema=self.tools[name][0])
                for name in page
            ],
            nextCursor=str(following) if following < len(names) else None,
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        self.calls.append((name, arguments))
        _, body = self.tools[name]
        try:
            value = body(arguments)
        except Exception as exc:
            return CallToolResult(content=[TextContent(type="text", text=str(exc))], isError=True)
        if isinstance(value, dict):
            import json

            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(value))],
                structuredContent=value,
            )
        return CallToolResult(content=[TextContent(type="text", text=str(value))])
