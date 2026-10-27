from __future__ import annotations

from importlib import resources
from typing import Any

import yaml

from algen_agent_tools.mcp.overlay import McpOverlay, McpToolkit

TOKEN = "env://ALGEN_TOOLS_GITHUB_TOKEN"
OVERLAY_FILE = "github.overlay.yaml"


def github_overlay() -> McpOverlay:
    """The reviewed overlay shipped with this package. Copy and edit it to declare fewer tools
    or change an approval rule; load the copy with ``McpOverlay.from_file``."""
    text = resources.files("algen_agent_tools.github").joinpath(OVERLAY_FILE).read_text("utf-8")
    return McpOverlay.from_mapping(yaml.safe_load(text))


def github_toolkit(*, session_factory: Any = None) -> McpToolkit:
    """The ``github`` toolkit: the overlay's tools, connecting to GitHub's MCP server with the
    token ``ALGEN_TOOLS_GITHUB_TOKEN`` names when a tool first runs."""
    return github_overlay().toolkit(session_factory=session_factory)
