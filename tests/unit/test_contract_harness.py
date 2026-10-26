from __future__ import annotations

from typing import Any

import pytest
from algen_agent_runtime.exceptions.errors import ConfigurationError
from algen_agent_runtime.tools.authoring import Toolkit, read, write

from algen_agent_tools.testing import (
    assert_toolkit_contract,
    describe_plugin,
    toolkit_contract_problems,
)


def test_an_empty_toolkit_is_a_problem() -> None:
    assert toolkit_contract_problems(Toolkit("empty")) == ("toolkit 'empty' has no tools",)


def test_a_raw_passthrough_parameter_is_a_problem() -> None:
    toolkit = Toolkit("db")

    @toolkit.tool(read())
    def run(sql: str) -> list[dict[str, Any]]:
        """Run any statement."""
        return []

    problems = toolkit_contract_problems(toolkit)

    assert problems == ("db.run: takes raw passthrough parameters ['sql']",)


def test_a_permission_outside_the_prefix_is_a_problem() -> None:
    toolkit = Toolkit("crm")

    @toolkit.tool(write().replace(permissions=frozenset({"admin.write"})))
    def close(account_id: str) -> bool:
        """Close an account."""
        return True

    with pytest.raises(AssertionError, match="outside the prefix 'crm'"):
        assert_toolkit_contract(toolkit)


def test_a_tool_named_outside_the_toolkit_is_a_problem() -> None:
    toolkit = Toolkit("crm")

    @toolkit.tool(read(), name="other.find")
    def find(domain: str) -> str:
        """Find an account."""
        return domain

    assert toolkit_contract_problems(toolkit) == ("other.find: is not named crm.<action>",)


def test_runtime_contract_problems_are_included() -> None:
    toolkit = Toolkit("crm")

    @toolkit.tool(write().replace(max_attempts=3))
    def update(account_id: str) -> bool:
        """Update an account."""
        return True

    [problem] = toolkit_contract_problems(toolkit)
    assert problem.startswith("crm.update: a write tool is retried 3 times")


def test_describe_plugin_refuses_an_untrusted_reference() -> None:
    with pytest.raises(ConfigurationError, match="not from a trusted namespace"):
        describe_plugin("elsewhere.plugin:Plugin")
