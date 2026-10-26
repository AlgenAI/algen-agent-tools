"""Every plugin this package installs registers only sound tools, opens nothing while
registering, and describes the same way through the Runtime."""

from __future__ import annotations

from importlib import metadata
from typing import Any

import pytest
from algen_agent_runtime.plugins.sdk import ENTRY_POINT_GROUP
from algen_agent_runtime.tools.contracts import Tool
from algen_agent_runtime.tools.testing import tool_contract_problems

from algen_agent_tools import TRUSTED_PREFIX
from algen_agent_tools.testing import RAW_PASSTHROUGH_PARAMETERS, plugin_tools

PLUGINS = sorted(
    entry.name
    for entry in metadata.entry_points(group=ENTRY_POINT_GROUP)
    if entry.name.startswith(f"{TRUSTED_PREFIX}.")
)


class _Registry:
    def __init__(self) -> None:
        self.items: list[Any] = []

    def register(self, item: Any, *args: Any, **kwargs: Any) -> None:
        self.items.append(item)


class RecordingContext:
    """A plugin context that records registrations and fails if anything resolves a secret."""

    def __init__(self) -> None:
        self.tools = _Registry()
        self.mcp_servers = _Registry()
        self.resources: list[object] = []
        self.env: set[str] = set()

    async def secrets(self, reference: str) -> str:
        raise AssertionError(f"a plugin resolved {reference} while registering")

    def declare_env(self, *names: str) -> None:
        self.env.update(names)

    def add_resource(self, resource: object) -> None:
        self.resources.append(resource)


def register(name: str) -> RecordingContext:
    [entry] = [e for e in metadata.entry_points(group=ENTRY_POINT_GROUP) if e.name == name]
    context = RecordingContext()
    entry.load()().register(context)
    return context


def test_the_package_ships_plugins() -> None:
    assert f"{TRUSTED_PREFIX}.sample" in PLUGINS


@pytest.mark.parametrize("name", PLUGINS)
def test_every_tool_meets_the_contract(name: str) -> None:
    connector = name.removeprefix(f"{TRUSTED_PREFIX}.")
    tools: list[Tool] = register(name).tools.items
    assert tools, f"{name} registered no tools"
    for tool in tools:
        definition = tool.definition
        assert tool_contract_problems(tool) == (), definition.name
        assert definition.name.startswith(f"{connector}."), definition.name
        assert all(p.startswith(f"{connector}.") for p in definition.required_permissions)
        assert not RAW_PASSTHROUGH_PARAMETERS & set(definition.input_schema["properties"])


@pytest.mark.parametrize("name", PLUGINS)
def test_describe_matches_registration_and_declared_env(name: str) -> None:
    context = register(name)
    described = plugin_tools(name)
    manifest = metadata.entry_points(group=ENTRY_POINT_GROUP)[name].load().manifest

    assert set(described) == {tool.definition.name for tool in context.tools.items}
    assert context.env == set(manifest.env)
    for tool in described.values():
        assert set(tool.env) == set(manifest.env)
