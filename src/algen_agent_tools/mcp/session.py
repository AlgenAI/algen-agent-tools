"""Connecting to an overlay's server, checking it still matches, and calling its tools."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import jsonschema
from algen_agent_runtime.config.settings import SecuritySettings
from algen_agent_runtime.exceptions.errors import (
    ConfigurationError,
    PolicyDeniedError,
    ToolExecutionError,
)
from algen_agent_runtime.mcp.client import MCPClientManager
from algen_agent_runtime.mcp.contracts import MCPStdioServerConfig
from algen_agent_runtime.tools.contracts import ToolContext, ToolDefinition, ToolHandler

from algen_agent_tools._env import SecretResolver

if TYPE_CHECKING:
    from algen_agent_tools.mcp.overlay import McpOverlay, McpToolkit

logger = logging.getLogger(__name__)


def _field(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


async def list_remote_tools(session: Any) -> dict[str, Any]:
    """Every tool the server advertises, by name, following pagination."""
    tools: dict[str, Any] = {}
    cursor: str | None = None
    while True:
        page = await session.list_tools(cursor=cursor)
        for item in _field(page, "tools") or ():
            name = _field(item, "name")
            if name:
                tools[str(name)] = item
        cursor = _field(page, "nextCursor")
        if not cursor:
            return tools


def allowed_hosts(server: Any, configured: tuple[str, ...] | None) -> tuple[str, ...]:
    """The hosts an HTTP transport may reach: the configured ones, else the URL's own host.

    Never empty for an HTTP server, since the Runtime treats an empty allowlist as any host.
    """
    if configured:
        return configured
    url = getattr(server, "url", None)
    if not url:
        return ()
    host = urlparse(url).hostname
    if not host:
        raise ConfigurationError(f"MCP server {server.name!r} has no host in its URL")
    return (host,)


class McpSession:
    """An open connection to the overlay's server and the tools it may not call."""

    def __init__(self, manager: MCPClientManager, session: Any, refused: dict[str, str]) -> None:
        self._manager = manager
        self.session = session
        self.refused = refused
        """Remote tool name to the reason calls to it are refused."""

    async def aclose(self) -> None:
        await self._manager.aclose()


async def open_session(
    overlay: McpOverlay, secrets: SecretResolver, *, session_factory: Any = None
) -> McpSession:
    """Connect, then compare each declared tool with what the server advertises now."""
    from algen_agent_tools.mcp.overlay import canonical

    server = overlay.server
    # Every env:// reference resolves through the deployment's secret resolver, never ambient
    # process state: stdio env values here, HTTP tokens and headers through the manager.
    environment = {name: await secrets(f"env://{name}") for name in overlay.env()}
    if isinstance(server, MCPStdioServerConfig) and server.env:
        resolved = {
            key: environment[value.removeprefix("env://")] for key, value in server.env.items()
        }
        server = server.model_copy(update={"env": resolved})
    manager = MCPClientManager(
        [server],
        environment=environment,
        security=SecuritySettings(allowed_http_hosts=allowed_hosts(server, overlay.allowed_hosts)),
        session_factory=session_factory,
    )
    try:
        connection = await manager.connect(server.name)
        advertised = await list_remote_tools(connection.session)
    except BaseException:
        await manager.aclose()
        raise
    refused: dict[str, str] = {}
    for remote, rule in overlay.tools.items():
        item = advertised.get(remote)
        if item is None:
            refused[remote] = "the server no longer offers it"
            continue
        current = _field(item, "inputSchema") or {}
        if canonical(current) != canonical(rule.input_schema):
            reason = "the server changed its input schema since the overlay was reviewed"
            if overlay.on_drift == "refuse":
                refused[remote] = reason
            else:
                logger.warning("MCP tool %s on %s: %s", remote, server.name, reason)
    undeclared = sorted(set(advertised) - set(overlay.tools))
    if undeclared:
        logger.info(
            "MCP server %s offers %d tool(s) the overlay does not declare; they are not exposed",
            server.name,
            len(undeclared),
        )
    return McpSession(manager, connection.session, refused)


def call_handler(toolkit: McpToolkit, remote: str, definition: ToolDefinition) -> ToolHandler:
    """The handler for one declared tool."""
    validator = jsonschema.Draft202012Validator(definition.input_schema)

    async def handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        errors = sorted(validator.iter_errors(arguments), key=lambda error: list(error.path))
        if errors:
            first = errors[0]
            where = "/".join(str(part) for part in first.absolute_path) or "the arguments"
            # Name the field and the rule; the jsonschema message would echo the value.
            raise ToolExecutionError(
                f"invalid arguments for {definition.name}: {where} fails {first.validator!r}"
            )
        connection: McpSession = await toolkit.client()
        if remote in connection.refused:
            raise PolicyDeniedError(
                f"{definition.name} is refused: {connection.refused[remote]}; review the "
                "MCP overlay"
            )
        try:
            async with asyncio.timeout(definition.timeout_seconds):
                result = await connection.session.call_tool(remote, arguments)
        except TimeoutError as exc:
            raise ToolExecutionError(
                f"{definition.name} timed out after {definition.timeout_seconds}s"
            ) from exc
        except Exception as exc:
            raise ToolExecutionError(f"{definition.name} failed ({type(exc).__name__})") from exc
        text = "\n".join(
            str(_field(item, "text"))
            for item in _field(result, "content") or ()
            if _field(item, "text")
        )
        if _field(result, "isError"):
            raise ToolExecutionError(f"{definition.name} failed: {text[:300] or 'server error'}")
        response: dict[str, Any] = {"text": text}
        structured = _field(result, "structuredContent")
        if structured is not None:
            response["data"] = structured
        size = len(json.dumps(response, ensure_ascii=False).encode())
        if size > definition.max_result_bytes:
            raise ToolExecutionError(
                f"{definition.name} returned {size} bytes, over its limit of "
                f"{definition.max_result_bytes}"
            )
        return response

    return handler
