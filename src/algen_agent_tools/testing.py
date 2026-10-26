"""The contract harness every connector in this package passes.

``assert_toolkit_contract`` checks a toolkit's tools with the Runtime's own
:func:`~algen_agent_runtime.tools.testing.assert_tool_contract` plus this package's rules.
``describe_plugin`` loads a plugin the way an agent would and returns the Runtime's inventory
for it, without calling any tool or opening any client.
"""

from __future__ import annotations

from collections.abc import Iterable

from algen_agent_runtime.config.settings import AppSettings, SecuritySettings
from algen_agent_runtime.orchestration.describe import (
    ComponentInventory,
    ToolDescription,
    describe_components,
)
from algen_agent_runtime.tools.authoring import Toolkit
from algen_agent_runtime.tools.testing import tool_contract_problems

from algen_agent_tools import TRUSTED_PREFIX

RAW_PASSTHROUGH_PARAMETERS = frozenset({"sql", "statement", "url", "endpoint", "command"})
"""Parameter names that suggest a free-form passthrough rather than a narrow, typed action."""


def toolkit_contract_problems(
    toolkit: Toolkit, *, longest_wait_seconds: float | None = None
) -> tuple[str, ...]:
    """Every problem with a toolkit; empty when it meets the package's contract.

    On top of the Runtime's tool contract: the toolkit has at least one tool, every tool is
    named ``<toolkit>.<action>``, every permission sits under the toolkit's permission prefix,
    and no tool takes a raw passthrough parameter such as ``sql`` or ``url``.
    """
    problems: list[str] = []
    if not toolkit.tools:
        problems.append(f"toolkit {toolkit.name!r} has no tools")
    for tool in toolkit.tools:
        definition = tool.definition
        name = definition.name
        problems.extend(
            f"{name}: {problem}"
            for problem in tool_contract_problems(tool, longest_wait_seconds=longest_wait_seconds)
        )
        if not name.startswith(f"{toolkit.name}."):
            problems.append(f"{name}: is not named {toolkit.name}.<action>")
        foreign = sorted(
            permission
            for permission in definition.required_permissions
            if not permission.startswith(f"{toolkit.permission_prefix}.")
        )
        if foreign:
            problems.append(
                f"{name}: permissions {foreign} are outside the prefix "
                f"{toolkit.permission_prefix!r}"
            )
        passthrough = sorted(
            RAW_PASSTHROUGH_PARAMETERS & set(definition.input_schema.get("properties", {}))
        )
        if passthrough:
            problems.append(f"{name}: takes raw passthrough parameters {passthrough}")
    return tuple(problems)


def assert_toolkit_contract(toolkit: Toolkit, *, longest_wait_seconds: float | None = None) -> None:
    """Fail with every problem :func:`toolkit_contract_problems` finds."""
    problems = toolkit_contract_problems(toolkit, longest_wait_seconds=longest_wait_seconds)
    if problems:
        raise AssertionError(
            f"toolkit {toolkit.name!r} breaks the connector contract:\n- " + "\n- ".join(problems)
        )


def describe_plugin(
    reference: str, *, trusted_prefixes: Iterable[str] = (TRUSTED_PREFIX,)
) -> ComponentInventory:
    """The Runtime inventory for one agent that names ``reference`` in ``domain_plugins``.

    ``reference`` is an entry point name (``algen_agent_tools.sample``) or ``module:attribute``.
    """
    settings = AppSettings.model_validate(
        {
            "providers": {"offline": {"type": "mock", "default_model": "deterministic"}},
            "agents": [
                {
                    "name": "connector-check",
                    "version": "1.0.0",
                    "description": "Loads one connector plugin for its inventory.",
                    "system_instructions": "Unused.",
                    "default_model": {"name": "deterministic", "provider": "offline"},
                    "domain_plugins": [reference],
                }
            ],
            "security": SecuritySettings(trusted_plugin_prefixes=tuple(trusted_prefixes)),
        }
    )
    return describe_components(settings)


def plugin_tools(reference: str) -> dict[str, ToolDescription]:
    """The tools ``reference`` registers, by name, as ``describe`` reports them."""
    return {
        tool.name: tool for tool in describe_plugin(reference).tools if tool.source == reference
    }
