"""Connecting to an overlay's server, checking it still matches, and calling its tools."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import jsonschema
from algen_agent_runtime.config.settings import SecuritySettings
from algen_agent_runtime.exceptions.errors import (
    ConfigurationError,
    PolicyDeniedError,
    ToolExecutionError,
    ToolNotAttemptedError,
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


@dataclass(frozen=True)
class Drift:
    """How a server's advertised tools differ from an overlay's reviewed ones."""

    missing: tuple[str, ...] = ()
    """Declared tools the server no longer offers."""
    changed: tuple[str, ...] = ()
    """Declared tools whose input schema differs from the reviewed one."""
    undeclared: tuple[str, ...] = ()
    """Tools the server offers that the overlay does not declare (never exposed)."""

    @property
    def breaking(self) -> bool:
        """Whether a declared tool is missing or changed."""
        return bool(self.missing or self.changed)


def compare(overlay: McpOverlay, advertised: dict[str, Any]) -> Drift:
    """Compare the overlay's declared tools with what the server advertises now."""
    from algen_agent_tools.mcp.overlay import canonical

    missing, changed = [], []
    for remote, rule in overlay.tools.items():
        item = advertised.get(remote)
        if item is None:
            missing.append(remote)
        elif canonical(_field(item, "inputSchema") or {}) != canonical(rule.input_schema):
            changed.append(remote)
    return Drift(
        missing=tuple(sorted(missing)),
        changed=tuple(sorted(changed)),
        undeclared=tuple(sorted(set(advertised) - set(overlay.tools))),
    )


class McpSession:
    """An open connection to the overlay's server and the tools it may not call.

    The connection is opened and closed by one task the session owns, whichever task first
    used it and whichever closes it: the MCP client's cancel scopes must be exited in the task
    that entered them, and a workflow can open the session from a node's task and close the
    container from another.
    """

    def __init__(
        self,
        manager: MCPClientManager,
        session: Any,
        refused: dict[str, str],
        *,
        closing: asyncio.Event,
        owner: asyncio.Task[None],
    ) -> None:
        self._manager = manager
        self.session = session
        self.refused = refused
        """Remote tool name to the reason calls to it are refused."""
        self._closing = closing
        self._owner = owner

    async def aclose(self) -> None:
        self._closing.set()
        await asyncio.shield(self._owner)


async def connect(
    overlay: McpOverlay, secrets: SecretResolver, *, session_factory: Any = None
) -> tuple[MCPClientManager, Any, dict[str, Any]]:
    """Connect to the overlay's server and list its tools: the manager (the caller closes
    it), the session and the advertised tools by name."""
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
    return manager, connection.session, advertised


async def open_session(
    overlay: McpOverlay, secrets: SecretResolver, *, session_factory: Any = None
) -> McpSession:
    """Connect, then compare each declared tool with what the server advertises now."""
    loop = asyncio.get_running_loop()
    opened: asyncio.Future[tuple[MCPClientManager, Any, dict[str, Any]]] = loop.create_future()
    closing = asyncio.Event()

    async def own_connection() -> None:
        try:
            manager, session, advertised = await connect(
                overlay, secrets, session_factory=session_factory
            )
        except BaseException as exc:
            if not opened.done():
                opened.set_exception(exc)
            return
        try:
            opened.set_result((manager, session, advertised))
            await closing.wait()
        finally:
            await manager.aclose()

    owner = asyncio.create_task(own_connection(), name=f"mcp-session-{overlay.server.name}")
    try:
        manager, session, advertised = await opened
    except BaseException:
        closing.set()
        await owner
        raise
    drift = compare(overlay, advertised)
    refused = {remote: "the server no longer offers it" for remote in drift.missing}
    for remote in drift.changed:
        reason = "the server changed its input schema since the overlay was reviewed"
        if overlay.on_drift == "refuse":
            refused[remote] = reason
        else:
            logger.warning("MCP tool %s on %s: %s", remote, overlay.server.name, reason)
    if drift.undeclared:
        logger.info(
            "MCP server %s offers %d tool(s) the overlay does not declare; they are not exposed",
            overlay.server.name,
            len(drift.undeclared),
        )
    return McpSession(manager, session, refused, closing=closing, owner=owner)


def call_handler(toolkit: McpToolkit, remote: str, definition: ToolDefinition) -> ToolHandler:
    """The handler for one declared tool."""
    validator = jsonschema.Draft202012Validator(definition.input_schema)

    async def handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        errors = sorted(validator.iter_errors(arguments), key=lambda error: list(error.path))
        if errors:
            first = errors[0]
            where = "/".join(str(part) for part in first.absolute_path) or "the arguments"
            # Name the field and the rule; the jsonschema message would echo the value.
            raise ToolNotAttemptedError(
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
