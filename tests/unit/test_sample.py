from __future__ import annotations

import pytest
from algen_agent_runtime.tools.contracts import SideEffect
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import ValidationError

from algen_agent_tools.sample import sample_toolkit
from algen_agent_tools.testing import assert_toolkit_contract, plugin_tools


def test_sample_toolkit_meets_the_contract() -> None:
    assert_toolkit_contract(sample_toolkit())


async def test_echo_returns_the_text_and_its_length() -> None:
    [echo] = sample_toolkit().tools

    result = await echo.execute({"text": "hello"}, fake_tool_context(echo))

    assert result == {"text": "hello", "length": 5}


async def test_echo_rejects_arguments_outside_its_schema() -> None:
    [echo] = sample_toolkit().tools

    with pytest.raises(ValidationError):
        await echo.execute({"text": "hi", "extra": 1}, fake_tool_context(echo))


def test_installed_entry_point_registers_and_describes() -> None:
    tools = plugin_tools("algen_agent_tools.sample")

    assert set(tools) == {"sample.echo"}
    echo = tools["sample.echo"]
    assert echo.side_effect == SideEffect.READ
    assert echo.required_permissions == ("sample.read",)
    assert echo.input_schema["additionalProperties"] is False


def test_module_reference_describes_the_same_tools() -> None:
    by_module = plugin_tools("algen_agent_tools.sample.plugin:SamplePlugin")

    assert set(by_module) == {"sample.echo"}
