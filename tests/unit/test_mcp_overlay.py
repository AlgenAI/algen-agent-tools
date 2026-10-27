from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from algen_agent_runtime.exceptions.errors import (
    ConfigurationError,
    PolicyDeniedError,
    ToolExecutionError,
)
from algen_agent_runtime.tools.contracts import Idempotency, SideEffect
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import ValidationError

from algen_agent_tools.mcp import McpOverlay, close_objects
from algen_agent_tools.testing import assert_toolkit_contract, describe_plugin

pytest.importorskip("mcp")

from algen_agent_tools.mcp.check import check, report
from algen_agent_tools.mcp.fake import FakeMcpServer
from algen_agent_tools.mcp.snapshot import draft, main

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "mcp"
OVERLAY_FILE = FIXTURES / "ticket_desk.overlay.yaml"


def mapping() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(OVERLAY_FILE.read_text())
    return loaded


def fake_desk() -> FakeMcpServer:
    rules = mapping()["tools"]
    server = FakeMcpServer()
    server.add(
        "get_ticket",
        rules["get_ticket"]["input_schema"],
        lambda args: {"id": args["ticket_id"], "title": "Printer is on fire"},
    )
    server.add("create_ticket", rules["create_ticket"]["input_schema"], lambda args: "T-2")
    server.add("delete_ticket", rules["delete_ticket"]["input_schema"], lambda args: 1 / 0)
    server.add(
        "run_shell",
        {"type": "object", "properties": {"command": {"type": "string"}}},
        lambda args: "never",
    )
    return server


def named(toolkit: Any, name: str) -> Any:
    return next(tool for tool in toolkit.tools if tool.definition.name == name)


def test_overlay_tools_meet_the_contract() -> None:
    assert_toolkit_contract(McpOverlay.from_file(OVERLAY_FILE).toolkit())


def test_each_declared_tool_carries_the_overlay_classification() -> None:
    toolkit = McpOverlay.from_file(OVERLAY_FILE).toolkit()
    get, create, delete = (
        named(toolkit, f"desk.{n}") for n in ("get_ticket", "create_ticket", "delete_ticket")
    )

    assert len(toolkit.tools) == 3
    assert get.definition.side_effect == SideEffect.READ
    assert get.definition.required_permissions == frozenset({"desk.read"})
    assert create.definition.side_effect == SideEffect.WRITE
    assert create.definition.idempotency == Idempotency.KEYED
    assert create.definition.retry_policy.max_attempts == 1
    assert create.definition.timeout_seconds == 20
    assert not create.definition.requires_approval
    assert delete.definition.side_effect == SideEffect.DESTRUCTIVE
    assert delete.definition.requires_approval
    assert delete.definition.required_permissions == frozenset({"desk.write"})
    assert get.definition.input_schema["additionalProperties"] is False
    assert get.definition.audit_metadata["classification_source"] == "overlay"
    assert toolkit.env == ("ALGEN_TOOLS_TEST_DESK_TOKEN",)


def test_a_sample_servers_tools_describe_with_the_declared_side_effects() -> None:
    reference = "tests.fixtures.mcp.plugin:DeskPlugin"
    inventory = describe_plugin(reference, trusted_prefixes=("tests.fixtures",))

    tools = {tool.name: tool for tool in inventory.tools if tool.source == reference}
    assert {name: tool.side_effect for name, tool in tools.items()} == {
        "desk.get_ticket": SideEffect.READ,
        "desk.create_ticket": SideEffect.WRITE,
        "desk.delete_ticket": SideEffect.DESTRUCTIVE,
    }
    assert tools["desk.delete_ticket"].requires_approval
    assert all(tool.env == ("ALGEN_TOOLS_TEST_DESK_TOKEN",) for tool in tools.values())
    [plugin] = inventory.plugins
    assert plugin.capabilities == ("mcp",)


async def test_calls_go_to_the_remote_tool_with_structured_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_TEST_DESK_TOKEN", "token")
    server = fake_desk()
    toolkit = McpOverlay.from_file(OVERLAY_FILE).toolkit(session_factory=server.session_factory)
    get = named(toolkit, "desk.get_ticket")
    create = named(toolkit, "desk.create_ticket")

    ticket = await get.execute({"ticket_id": "T-1"}, fake_tool_context(get))
    opened = await create.execute({"title": "New"}, fake_tool_context(create))

    assert ticket["data"] == {"id": "T-1", "title": "Printer is on fire"}
    assert opened == {"text": "T-2"}
    assert server.calls == [
        ("get_ticket", {"ticket_id": "T-1"}),
        ("create_ticket", {"title": "New"}),
    ]
    assert server.connections == 1
    await toolkit.aclose()


async def test_arguments_outside_the_reviewed_schema_never_reach_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_TEST_DESK_TOKEN", "token")
    server = fake_desk()
    get = named(
        McpOverlay.from_file(OVERLAY_FILE).toolkit(session_factory=server.session_factory),
        "desk.get_ticket",
    )

    for arguments in ({}, {"ticket_id": 7}, {"ticket_id": "T-1", "debug": True}):
        with pytest.raises(ToolExecutionError, match="invalid arguments"):
            await get.execute(arguments, fake_tool_context(get))
    assert server.calls == []


async def test_a_server_error_is_reported_without_echoing_too_much(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_TEST_DESK_TOKEN", "token")
    delete = named(
        McpOverlay.from_file(OVERLAY_FILE).toolkit(session_factory=fake_desk().session_factory),
        "desk.delete_ticket",
    )

    with pytest.raises(ToolExecutionError, match=r"desk\.delete_ticket failed: division by zero"):
        await delete.execute({"ticket_id": "T-1"}, fake_tool_context(delete))


async def test_an_oversized_result_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_TEST_DESK_TOKEN", "token")
    data = mapping()
    data["tools"]["create_ticket"]["max_result_bytes"] = 10
    server = fake_desk().add(
        "create_ticket", data["tools"]["create_ticket"]["input_schema"], lambda args: "x" * 100
    )
    create = named(
        McpOverlay.from_mapping(data).toolkit(session_factory=server.session_factory),
        "desk.create_ticket",
    )

    with pytest.raises(ToolExecutionError, match="over its limit of 10"):
        await create.execute({"title": "t"}, fake_tool_context(create))


async def test_a_changed_or_missing_remote_tool_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_TEST_DESK_TOKEN", "token")
    server = fake_desk()
    changed = copy.deepcopy(server.tools["create_ticket"][0])
    changed["properties"]["assignee"] = {"type": "string"}
    server.add("create_ticket", changed, lambda args: "T-9")
    del server.tools["get_ticket"]
    toolkit = McpOverlay.from_file(OVERLAY_FILE).toolkit(session_factory=server.session_factory)
    create, get = named(toolkit, "desk.create_ticket"), named(toolkit, "desk.get_ticket")

    with pytest.raises(PolicyDeniedError, match="changed its input schema"):
        await create.execute({"title": "t"}, fake_tool_context(create))
    with pytest.raises(PolicyDeniedError, match="no longer offers it"):
        await get.execute({"ticket_id": "T-1"}, fake_tool_context(get))
    assert server.calls == []


async def test_drift_can_be_allowed_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_TEST_DESK_TOKEN", "token")
    server = fake_desk()
    changed = copy.deepcopy(server.tools["create_ticket"][0])
    changed["description"] = "reworded"
    server.add("create_ticket", changed, lambda args: "T-9")
    create = named(
        McpOverlay.from_mapping({**mapping(), "on_drift": "allow"}).toolkit(
            session_factory=server.session_factory
        ),
        "desk.create_ticket",
    )

    assert await create.execute({"title": "t"}, fake_tool_context(create)) == {"text": "T-9"}


async def test_tool_listing_follows_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_TEST_DESK_TOKEN", "token")
    server = fake_desk()
    server.page_size = 1
    get = named(
        McpOverlay.from_file(OVERLAY_FILE).toolkit(session_factory=server.session_factory),
        "desk.get_ticket",
    )

    assert (await get.execute({"ticket_id": "T-1"}, fake_tool_context(get)))["data"]["id"] == "T-1"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda m: m["tools"]["get_ticket"].update(effect="maybe"), "effect"),
        (lambda m: m["tools"]["get_ticket"].update(approval=True), "cannot require approval"),
        (lambda m: m["tools"]["get_ticket"]["input_schema"].update(type="string"), "object schema"),
        (lambda m: m["server"].update(allowed_tools=["get_ticket"]), "not server.allowed_tools"),
        (lambda m: m["server"]["env"].update(DESK_TOKEN="literal-secret"), "env:// reference"),
        (lambda m: m["tools"].update({"Bad Name!": m["tools"]["get_ticket"]}), "cannot be named"),
        (lambda m: m.update(tools={}), "at least 1"),
    ],
)
def test_invalid_overlays_are_refused(change: Any, message: str) -> None:
    data = mapping()
    change(data)

    with pytest.raises(ValidationError, match=message):
        McpOverlay.from_mapping(data)


def test_close_objects_closes_nested_objects_and_keeps_explicit_maps() -> None:
    schema = {
        "type": "object",
        "properties": {
            "filter": {"type": "object", "properties": {"q": {"type": "string"}}},
            "labels": {"type": "object", "additionalProperties": {"type": "string"}},
        },
    }

    closed = close_objects(schema)

    assert closed["additionalProperties"] is False
    assert closed["properties"]["filter"]["additionalProperties"] is False
    assert closed["properties"]["labels"]["additionalProperties"] == {"type": "string"}


async def test_snapshot_drafts_a_reviewable_overlay(tmp_path: Path) -> None:
    server = fake_desk()
    text = await draft(
        "desk",
        {"name": "ticket-desk", "transport": "stdio", "command": "python"},
        session_factory=server.session_factory,
    )

    assert "# run_shell: server suggests" in text
    loaded = yaml.safe_load(text)
    assert loaded["tools"]["get_ticket"]["effect"] is None
    assert loaded["tools"]["get_ticket"]["input_schema"] == server.tools["get_ticket"][0]
    with pytest.raises(ValidationError, match="effect"):
        McpOverlay.from_mapping(loaded)


def test_overlay_from_a_missing_file_is_a_configuration_error(tmp_path: Path) -> None:
    from algen_agent_runtime.exceptions.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="could not read MCP overlay"):
        McpOverlay.from_file(tmp_path / "missing.yaml")


async def test_against_the_real_sample_server_over_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_TEST_DESK_TOKEN", "token")
    data = mapping()
    data["server"].update(command=sys.executable, cwd=str(FIXTURES.parents[2]))
    toolkit = McpOverlay.from_mapping(data).toolkit()
    get, create = named(toolkit, "desk.get_ticket"), named(toolkit, "desk.create_ticket")
    try:
        ticket = await get.execute({"ticket_id": "T-1"}, fake_tool_context(get))
        opened = await create.execute({"title": "Coffee machine"}, fake_tool_context(create))
    finally:
        await toolkit.aclose()

    assert ticket["data"] == {"id": "T-1", "title": "Printer is on fire"}
    assert opened["data"] == {"id": "T-2", "title": "Coffee machine"}


def test_snapshot_cli_writes_a_draft(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    server_file = tmp_path / "server.yaml"
    server_file.write_text(
        yaml.safe_dump(
            {
                "name": "ticket-desk",
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "tests.fixtures.mcp.sample_server"],
                "cwd": str(FIXTURES.parents[2]),
            }
        )
    )
    output = tmp_path / "draft.yaml"

    assert main(["snapshot", str(server_file), "--name", "desk", "--output", str(output)]) == 0
    assert set(yaml.safe_load(output.read_text())["tools"]) == {
        "create_ticket",
        "delete_ticket",
        "get_ticket",
        "run_shell",
    }


async def test_check_reports_missing_changed_and_undeclared_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_TEST_DESK_TOKEN", "token")
    overlay = McpOverlay.from_file(OVERLAY_FILE)
    server = fake_desk()
    drift = await check(overlay, session_factory=server.session_factory)
    assert (drift.missing, drift.changed, drift.undeclared) == ((), (), ("run_shell",))
    assert not drift.breaking

    changed = copy.deepcopy(server.tools["create_ticket"][0])
    changed["properties"]["assignee"] = {"type": "string"}
    server.add("create_ticket", changed, lambda args: "T-9")
    del server.tools["get_ticket"]
    drift = await check(overlay, session_factory=server.session_factory)
    assert (drift.missing, drift.changed) == (("get_ticket",), ("create_ticket",))
    assert drift.breaking
    text = report(overlay, drift)
    assert "desk: 1 of 3 declared tools match the server" in text
    assert "no longer offered (refused): get_ticket" in text
    assert "input schema changed (refused): create_ticket" in text


async def test_check_names_an_unset_variable_without_a_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALGEN_TOOLS_TEST_DESK_TOKEN", raising=False)
    with pytest.raises(ConfigurationError, match="ALGEN_TOOLS_TEST_DESK_TOKEN is not set"):
        await check(McpOverlay.from_file(OVERLAY_FILE), session_factory=fake_desk().session_factory)


def _local_overlay(tmp_path: Path, **changes: Any) -> Path:
    data = mapping()
    data["server"].update(command=sys.executable, cwd=str(FIXTURES.parents[2]))
    for remote, schema in changes.items():
        data["tools"][remote]["input_schema"] = schema
    path = tmp_path / "desk.overlay.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_check_cli_against_the_real_sample_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ALGEN_TOOLS_TEST_DESK_TOKEN", "token")
    reviewed = _local_overlay(tmp_path)
    assert main(["check", str(reviewed)]) == 0
    assert "offered but not declared (not exposed): run_shell" in capsys.readouterr().out
    assert main(["check", str(reviewed), "--strict"]) == 1
    capsys.readouterr()

    stale = _local_overlay(tmp_path, get_ticket={"type": "object", "properties": {}})
    assert main(["check", str(stale), "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "overlay": "desk",
        "declared": 3,
        "missing": [],
        "changed": ["get_ticket"],
        "undeclared": ["run_shell"],
        "breaking": True,
    }
