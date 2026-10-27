"""Curated, governed connectors for Algen Agent Runtime agents.

Each connector is a :class:`~algen_agent_runtime.tools.authoring.Toolkit` registered through a
plugin, so an agent names it in ``domain_plugins`` and Studio lists its tools from
``algen-agent-runtime describe``.
"""

__version__ = "0.1.0a2"

PUBLISHER = "AlgenAI"
"""The publisher recorded on every plugin manifest in this package."""

TRUSTED_PREFIX = "algen_agent_tools"
"""Add this to ``security.trusted_plugin_prefixes`` to load the package's plugins."""

__all__ = ["PUBLISHER", "TRUSTED_PREFIX", "__version__"]
