"""Governance overlays for MCP servers.

When a well-maintained MCP server exists, an overlay replaces a native connector: it declares
each remote tool's side effect, permission, approval rule and reviewed input schema, so the
Runtime governs the calls and Studio lists the tools before anything connects.

Needs the ``mcp`` extra for real transports: ``pip install "algen-agent-tools[mcp]"``.
"""

from algen_agent_tools.mcp.overlay import (
    OUTPUT_SCHEMA,
    McpOverlay,
    McpToolkit,
    McpToolRule,
    close_objects,
)
from algen_agent_tools.mcp.plugin import overlay_plugin
from algen_agent_tools.mcp.session import McpSession

__all__ = [
    "OUTPUT_SCHEMA",
    "McpOverlay",
    "McpSession",
    "McpToolRule",
    "McpToolkit",
    "close_objects",
    "overlay_plugin",
]
