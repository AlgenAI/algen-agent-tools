from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Live tests call real services, so they run only when ALGEN_TOOLS_LIVE=1 asks for them."""
    if os.getenv("ALGEN_TOOLS_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="live test; set ALGEN_TOOLS_LIVE=1 to run it")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)
