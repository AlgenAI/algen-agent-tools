"""Check a reviewed overlay against its live server, so drift is found before agents hit it.

Run it on a schedule in the project's CI: it exits non-zero when a declared tool disappeared or
its input schema changed, the two cases the overlay refuses at run time.
"""

from __future__ import annotations

import json
import os
from typing import Any

from algen_agent_runtime.exceptions.errors import ConfigurationError

from algen_agent_tools._env import env_name
from algen_agent_tools.mcp.overlay import McpOverlay
from algen_agent_tools.mcp.session import Drift, compare, connect


async def environment_secrets(reference: str) -> str:
    """Resolve an ``env://`` reference from this process's environment, naming only the
    variable when it is unset."""
    name = env_name(reference)
    value = os.environ.get(name)
    if not value:
        raise ConfigurationError(f"{name} is not set")
    return value


async def check(overlay: McpOverlay, *, secrets: Any = None, session_factory: Any = None) -> Drift:
    """Connect to the overlay's server and compare its tools with the reviewed ones."""
    manager, _, advertised = await connect(
        overlay, secrets or environment_secrets, session_factory=session_factory
    )
    try:
        return compare(overlay, advertised)
    finally:
        await manager.aclose()


def report(overlay: McpOverlay, drift: Drift) -> str:
    """A short human-readable report."""
    lines = []
    matching = len(overlay.tools) - len(drift.missing) - len(drift.changed)
    lines.append(
        f"{overlay.name}: {matching} of {len(overlay.tools)} declared tools match the server"
    )
    for label, names in (
        ("no longer offered (refused)", drift.missing),
        (
            "input schema changed "
            + ("(refused)" if overlay.on_drift == "refuse" else "(allowed by on_drift)"),
            drift.changed,
        ),
        ("offered but not declared (not exposed)", drift.undeclared),
    ):
        if names:
            lines.append(f"  {label}: {', '.join(names)}")
    if drift.breaking:
        lines.append("Review the overlay: re-run snapshot, compare, and update the pinned schemas.")
    return "\n".join(lines) + "\n"


def as_json(overlay: McpOverlay, drift: Drift) -> str:
    return (
        json.dumps(
            {
                "overlay": overlay.name,
                "declared": len(overlay.tools),
                "missing": list(drift.missing),
                "changed": list(drift.changed),
                "undeclared": list(drift.undeclared),
                "breaking": drift.breaking,
            },
            indent=2,
        )
        + "\n"
    )
