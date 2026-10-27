"""The one way this package turns a toolkit into a Runtime plugin."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from algen_agent_runtime.plugins.sdk import PluginManifest, plugin
from algen_agent_runtime.tools.authoring import Toolkit

from algen_agent_tools import PUBLISHER, TRUSTED_PREFIX, __version__
from algen_agent_tools.catalog import ConnectorInfo


def toolkit_plugin(
    info: ConnectorInfo,
    build: Callable[[], Toolkit],
    *,
    capabilities: frozenset[str] = frozenset(),
) -> type:
    """A plugin class that builds a fresh toolkit and registers it.

    ``info.connector`` is the module name under ``algen_agent_tools`` and the entry point name's
    suffix. ``info.settings`` lists the variables the zero-configuration toolkit reads, so the
    manifest and ``describe`` report them; the info itself is the plugin's ``connector_info``,
    which the catalog reads. The toolkit is built in ``register``, so importing the plugin opens
    nothing.
    """

    connector = info.connector
    manifest = PluginManifest(
        name=f"{TRUSTED_PREFIX}.{connector}",
        version=__version__,
        publisher=PUBLISHER,
        env=frozenset(info.env),
        capabilities=capabilities,
    )

    @plugin(manifest)
    class ToolkitPlugin:
        connector_info = info

        def register(self, context: Any) -> None:
            build().register(context)

    ToolkitPlugin.__name__ = ToolkitPlugin.__qualname__ = f"{connector.title()}Plugin"
    ToolkitPlugin.__doc__ = f"Registers the {connector} toolkit from algen-agent-tools."
    return ToolkitPlugin
