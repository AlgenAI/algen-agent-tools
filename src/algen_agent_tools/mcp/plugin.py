from __future__ import annotations

from pathlib import Path
from typing import Any

from algen_agent_runtime.plugins.sdk import PluginManifest, plugin

from algen_agent_tools.mcp.overlay import McpOverlay


def overlay_plugin(
    overlay: McpOverlay | str | Path,
    *,
    name: str,
    version: str,
    publisher: str,
    session_factory: Any = None,
) -> type:
    """A plugin class that registers one overlay's tools.

    ``overlay`` is an :class:`McpOverlay` or the path of its YAML file, read when the plugin
    registers. In a project's plugin module::

        GithubTools = overlay_plugin(
            Path(__file__).with_name("github.overlay.yaml"),
            name="acme.github", version="1.0.0", publisher="acme",
        )
    """

    def load() -> McpOverlay:
        return overlay if isinstance(overlay, McpOverlay) else McpOverlay.from_file(overlay)

    manifest_env = load().env()

    @plugin(
        PluginManifest(
            name=name,
            version=version,
            publisher=publisher,
            env=frozenset(manifest_env),
            capabilities=frozenset({"mcp"}),
        )
    )
    class OverlayPlugin:
        def register(self, context: Any) -> None:
            load().toolkit(session_factory=session_factory).register(context)

    OverlayPlugin.__doc__ = f"Registers the MCP overlay {name}."
    return OverlayPlugin
