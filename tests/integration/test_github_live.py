"""Check the shipped GitHub overlay against GitHub's live MCP server. Opt-in only.

Run with ``ALGEN_TOOLS_LIVE=1`` and ``ALGEN_TOOLS_GITHUB_TOKEN`` set to a personal access token;
the check only lists tools, so a token with no repository access is enough.
"""

from __future__ import annotations

import os

import pytest

from algen_agent_tools.github import github_overlay

pytestmark = pytest.mark.live


async def test_the_shipped_overlay_matches_the_live_server() -> None:
    pytest.importorskip("mcp")
    if not os.getenv("ALGEN_TOOLS_GITHUB_TOKEN"):
        pytest.skip("set ALGEN_TOOLS_GITHUB_TOKEN")
    from algen_agent_tools.mcp.check import check

    drift = await check(github_overlay())
    assert not drift.breaking, drift
