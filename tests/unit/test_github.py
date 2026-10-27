"""The shipped GitHub overlay: what it exposes, how each tool is governed, and that calls go
through the overlay's checks."""

from __future__ import annotations

from typing import Any

import pytest
from algen_agent_runtime.exceptions.errors import PolicyDeniedError
from algen_agent_runtime.tools.contracts import SideEffect
from algen_agent_runtime.tools.testing import fake_tool_context

from algen_agent_tools.github import github_overlay, github_toolkit
from algen_agent_tools.testing import assert_toolkit_contract, passthrough_parameters, plugin_tools

APPROVED_WRITES = {"pull_request_review_write", "push_files", "create_or_update_file"}
DESTRUCTIVE = {"merge_pull_request", "delete_file"}
NEVER_EXPOSED = {
    "create_repository",
    "fork_repository",
    "run_secret_scanning",
    "ui_get",
    "request_copilot_review",
    "get_teams",
    "get_team_members",
    "list_repository_collaborators",
    "search_users",
}


def test_the_plugin_describes_the_reviewed_tools() -> None:
    tools = plugin_tools("algen_agent_tools.github")
    assert len(tools) == 37
    assert not {f"github.{name}" for name in NEVER_EXPOSED} & set(tools)
    for name, tool in tools.items():
        remote = name.removeprefix("github.")
        if remote in DESTRUCTIVE:
            assert (tool.side_effect, tool.requires_approval) == (SideEffect.DESTRUCTIVE, True)
        elif remote in APPROVED_WRITES:
            assert (tool.side_effect, tool.requires_approval) == (SideEffect.WRITE, True)
        else:
            assert tool.side_effect in (SideEffect.READ, SideEffect.WRITE), name
            assert not tool.requires_approval, name
        access = "read" if tool.side_effect is SideEffect.READ else "write"
        assert set(tool.required_permissions) == {f"github.{access}"}, name
        assert tool.env == ("ALGEN_TOOLS_GITHUB_TOKEN",)
    assert_toolkit_contract(github_toolkit())


def test_reads_are_the_servers_read_only_tools() -> None:
    reads = {r for r, rule in github_overlay().tools.items() if rule.effect == "read"}
    assert {"get_file_contents", "issue_read", "pull_request_read", "search_code"} <= reads
    assert reads.isdisjoint(APPROVED_WRITES | DESTRUCTIVE)


def test_the_overlay_names_only_its_token_and_its_host() -> None:
    overlay = github_overlay()
    assert overlay.env() == ("ALGEN_TOOLS_GITHUB_TOKEN",)
    assert overlay.server.url == "https://api.githubcopilot.com/mcp/"
    assert overlay.on_drift == "refuse"
    for rule in overlay.tools.values():
        assert passthrough_parameters(rule.input_schema) == []


def fake_github(**changes: dict[str, Any]) -> Any:
    from algen_agent_tools.mcp.fake import FakeMcpServer

    server = FakeMcpServer()
    for remote, rule in github_overlay().tools.items():
        server.add(remote, changes.get(remote, rule.input_schema), lambda args: {"ok": True})
    return server


async def test_calls_reach_the_server_with_validated_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("mcp")
    monkeypatch.setenv("ALGEN_TOOLS_GITHUB_TOKEN", "test-token")
    server = fake_github()
    toolkit = github_toolkit(session_factory=server.session_factory)
    tool = next(t for t in toolkit.tools if t.definition.name == "github.get_me")
    assert await tool.execute({}, fake_tool_context(tool)) == {
        "text": '{"ok": true}',
        "data": {"ok": True},
    }
    assert server.calls == [("get_me", {})]


async def test_a_changed_server_schema_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("mcp")
    monkeypatch.setenv("ALGEN_TOOLS_GITHUB_TOKEN", "test-token")
    server = fake_github(get_me={"type": "object", "properties": {"x": {"type": "string"}}})
    toolkit = github_toolkit(session_factory=server.session_factory)
    tool = next(t for t in toolkit.tools if t.definition.name == "github.get_me")
    with pytest.raises(PolicyDeniedError, match="changed its input schema"):
        await tool.execute({}, fake_tool_context(tool))
    assert server.calls == []
