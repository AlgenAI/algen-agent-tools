"""Packaging rules: every connector is an entry point under the trusted prefix, with its own
extra, a plugin that builds without opening anything, and documentation."""

from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path

from algen_agent_runtime.plugins.sdk import ENTRY_POINT_GROUP, PluginManifest

import algen_agent_tools

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
ENTRY_POINTS = PYPROJECT["project"]["entry-points"][ENTRY_POINT_GROUP]
EXTRAS = PYPROJECT["project"]["optional-dependencies"]
# Connectors that need no third-party package beyond the Runtime ship no extra of their own.
DEPENDENCY_FREE = {"sample"}


def connectors() -> set[str]:
    return {name.removeprefix(f"{algen_agent_tools.TRUSTED_PREFIX}.") for name in ENTRY_POINTS}


def test_every_entry_point_is_under_the_trusted_prefix() -> None:
    for name, target in ENTRY_POINTS.items():
        assert name.startswith(f"{algen_agent_tools.TRUSTED_PREFIX}."), name
        assert target.startswith(f"{name}.plugin:"), (name, target)


def test_every_connector_has_its_own_extra_and_docs() -> None:
    for connector in connectors() - DEPENDENCY_FREE:
        assert connector in EXTRAS, f"{connector} has no optional extra"
        assert (ROOT / "docs" / "connectors" / f"{connector}.md").is_file(), connector


def test_installed_entry_points_match_pyproject() -> None:
    installed = {
        entry.name: entry.value
        for entry in metadata.entry_points(group=ENTRY_POINT_GROUP)
        if entry.name.startswith(f"{algen_agent_tools.TRUSTED_PREFIX}.")
    }
    assert installed == ENTRY_POINTS, "reinstall the package: pip install -e '.[dev]'"


def test_every_plugin_manifest_names_its_entry_point_and_version() -> None:
    for entry in metadata.entry_points(group=ENTRY_POINT_GROUP):
        if not entry.name.startswith(f"{algen_agent_tools.TRUSTED_PREFIX}."):
            continue
        manifest = entry.load().manifest
        assert isinstance(manifest, PluginManifest)
        assert manifest.name == entry.name
        assert manifest.version == algen_agent_tools.__version__
        assert manifest.publisher == algen_agent_tools.PUBLISHER


def test_version_matches_changelog() -> None:
    changelog = (ROOT / "CHANGELOG.md").read_text()
    assert f"## [{algen_agent_tools.__version__}]" in changelog
