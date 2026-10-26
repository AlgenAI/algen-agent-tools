"""Draft an overlay from a live MCP server, for a person to review.

The draft pins every advertised tool's input schema and leaves each ``effect`` empty, with the
server's own annotation as a comment. It does not load until someone classifies every tool and
deletes the ones the agent must not call.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml
from algen_agent_runtime.config.settings import SecuritySettings
from algen_agent_runtime.mcp.client import MCPClientManager

from algen_agent_tools.mcp.overlay import ServerHolder
from algen_agent_tools.mcp.session import _field, allowed_hosts, list_remote_tools

_HEADER = """\
# MCP governance overlay, drafted from the server's advertised tools.
# Review before use:
#   - delete every tool the agent must not call;
#   - set each remaining tool's effect: read, write, external or destructive;
#   - set approval: true where a person must approve every call.
# The server's annotations are hints it sets about itself, not a classification.
"""


def suggestion(annotations: Any) -> str:
    """The effect the server's own annotations hint at, for a reviewer's comment."""
    if annotations is None:
        return "no annotations"
    if _field(annotations, "destructiveHint"):
        return "destructive (destructiveHint)"
    if _field(annotations, "readOnlyHint"):
        return "read (readOnlyHint)"
    if _field(annotations, "openWorldHint"):
        return "external (openWorldHint)"
    return "write (no read-only hint)"


async def draft(name: str, server: dict[str, Any], *, session_factory: Any = None) -> str:
    """The YAML text of a draft overlay for ``server``."""
    config = ServerHolder.model_validate({"server": server}).server
    manager = MCPClientManager(
        [config],
        security=SecuritySettings(allowed_http_hosts=allowed_hosts(config, None)),
        session_factory=session_factory,
    )
    try:
        connection = await manager.connect(config.name)
        advertised = await list_remote_tools(connection.session)
    finally:
        await manager.aclose()
    body = yaml.safe_dump(
        {"name": name, "server": server}, sort_keys=False, default_flow_style=False
    )
    lines = [_HEADER, body.rstrip(), "tools:"]
    for remote in sorted(advertised):
        item = advertised[remote]
        rule = {
            remote: {
                "effect": None,
                "description": _field(item, "description") or "",
                "input_schema": _field(item, "inputSchema") or {"type": "object"},
            }
        }
        block = yaml.safe_dump(rule, sort_keys=False, default_flow_style=False).rstrip()
        lines.append(f"  # {remote}: server suggests {suggestion(_field(item, 'annotations'))}")
        lines.extend(f"  {line}" for line in block.splitlines())
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m algen_agent_tools.mcp",
        description="Draft an MCP governance overlay from a live server.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot", help="connect and draft an overlay")
    snapshot.add_argument("server", type=Path, help="YAML file with one MCP server config")
    snapshot.add_argument("--name", required=True, help="the overlay (toolkit) name")
    snapshot.add_argument("--output", type=Path, help="write here instead of stdout")
    arguments = parser.parse_args(argv)
    server = yaml.safe_load(arguments.server.read_text())
    if not isinstance(server, dict):
        parser.error(f"{arguments.server} must hold one MCP server mapping")
    text = asyncio.run(draft(arguments.name, server))
    if arguments.output:
        arguments.output.write_text(text)
    else:
        sys.stdout.write(text)
    return 0
