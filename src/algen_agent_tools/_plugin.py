"""The one way this package turns a toolkit into a Runtime plugin."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from algen_agent_runtime.plugins.sdk import PluginManifest, plugin
from algen_agent_runtime.tools.authoring import Toolkit

from algen_agent_tools import PUBLISHER, TRUSTED_PREFIX, __version__


def toolkit_plugin(
    connector: str,
    build: Callable[[], Toolkit],
    *,
    env: tuple[str, ...] = (),
    capabilities: frozenset[str] = frozenset(),
) -> type:
    """A plugin class that builds a fresh toolkit and registers it.

    ``connector`` is the module name under ``algen_agent_tools`` and the entry point name's
    suffix. ``env`` lists the variables the zero-configuration toolkit reads, so the manifest and
    ``describe`` report them. The toolkit is built in ``register``, so importing the plugin opens
    nothing.
    """

    manifest = PluginManifest(
        name=f"{TRUSTED_PREFIX}.{connector}",
        version=__version__,
        publisher=PUBLISHER,
        env=frozenset(env),
        capabilities=capabilities,
    )

    @plugin(manifest)
    class ToolkitPlugin:
        def register(self, context: Any) -> None:
            build().register(context)

    ToolkitPlugin.__name__ = ToolkitPlugin.__qualname__ = f"{connector.title()}Plugin"
    ToolkitPlugin.__doc__ = f"Registers the {connector} toolkit from algen-agent-tools."
    return ToolkitPlugin
