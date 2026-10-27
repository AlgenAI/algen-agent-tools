"""The connector catalog is generated from the code, shipped with the package, and is the
contract snapshot: any change to a tool's name, schema, side effect or permissions shows up as a
diff in ``connectors.json``."""

from __future__ import annotations

import json
from importlib import metadata, resources
from pathlib import Path

import pytest
from algen_agent_runtime.plugins.sdk import ENTRY_POINT_GROUP
from pydantic import ValidationError

import algen_agent_tools
from algen_agent_tools import catalog
from algen_agent_tools.catalog import (
    CATALOG_FILE,
    CATALOG_VERSION,
    Catalog,
    CatalogConnector,
    ConnectorInfo,
    Egress,
    Setting,
    build_catalog,
    load_catalog,
)

ROOT = Path(__file__).resolve().parents[2]
PLUGINS = sorted(
    entry.name
    for entry in metadata.entry_points(group=ENTRY_POINT_GROUP)
    if entry.name.startswith(f"{algen_agent_tools.TRUSTED_PREFIX}.")
)


def test_the_shipped_catalog_matches_the_code() -> None:
    shipped = resources.files("algen_agent_tools").joinpath(CATALOG_FILE).read_text("utf-8")
    assert shipped == build_catalog().to_json(), (
        "connectors.json is out of date: a connector or tool contract changed. Run "
        "`python -m algen_agent_tools catalog --write` and record the change in CHANGELOG.md."
    )


def test_the_catalog_loads_and_lists_every_plugin() -> None:
    loaded = load_catalog()
    assert loaded.catalog_version == CATALOG_VERSION
    assert loaded.version == algen_agent_tools.__version__
    assert loaded.trusted_prefix == algen_agent_tools.TRUSTED_PREFIX
    assert [connector.plugin for connector in loaded.connectors] == PLUGINS


@pytest.mark.parametrize("plugin", PLUGINS)
def test_every_plugin_declares_matching_info(plugin: str) -> None:
    loaded = metadata.entry_points(group=ENTRY_POINT_GROUP)[plugin].load()
    info = loaded.connector_info
    assert isinstance(info, ConnectorInfo)
    assert plugin == f"{algen_agent_tools.TRUSTED_PREFIX}.{info.connector}"
    assert set(info.env) == set(loaded.manifest.env)
    assert (ROOT / "docs" / "connectors" / f"{info.connector}.md").is_file()
    if info.extra is not None:
        assert info.extra in (
            metadata.metadata("algen-agent-tools").get_all("Provides-Extra") or []
        )


@pytest.mark.parametrize("connector", load_catalog().connectors, ids=lambda item: item.plugin)
def test_the_manifest_snippet_enables_exactly_the_connectors_tools(
    connector: CatalogConnector,
) -> None:
    snippet = connector.manifest
    assert snippet.domain_plugins == (connector.plugin,)
    assert snippet.enabled_tools == tuple(tool.name for tool in connector.tools)
    assert set(snippet.tool_permissions) == {
        p for tool in connector.tools for p in tool.permissions
    }
    assert snippet.security.trusted_plugin_prefixes == (algen_agent_tools.TRUSTED_PREFIX,)
    assert connector.requirement.endswith(f"=={algen_agent_tools.__version__}")


def test_the_catalog_text_is_canonical() -> None:
    text = build_catalog().to_json()
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True) + "\n"
    assert Catalog.model_validate_json(text) == build_catalog()


def test_check_passes_on_the_shipped_file_and_fails_on_drift(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert catalog.main(["catalog", "--check"]) == 0
    drifted = build_catalog().model_copy(update={"connectors": ()})
    monkeypatch.setattr(catalog, "build_catalog", lambda: drifted)
    assert catalog.main(["catalog", "--check"]) == 1
    assert "catalog --write" in capsys.readouterr().err


def test_catalog_prints_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    assert catalog.main(["catalog"]) == 0
    assert capsys.readouterr().out == build_catalog().to_json()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"env": "lower_case", "description": "x"}, "environment variable"),
        ({"env": "A_TOKEN", "description": "x", "secret": True, "example": "t"}, "secret"),
        ({"env": "A_PORT", "description": "x", "default": "1"}, "default"),
    ],
)
def test_settings_are_validated(arguments: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Setting.model_validate(arguments)


def test_egress_names_one_destination_and_only_declared_settings() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        Egress(protocol="https")
    with pytest.raises(ValidationError, match="exactly one"):
        Egress(protocol="https", host="a.example", host_setting="A_HOST")
    with pytest.raises(ValidationError, match="undeclared settings"):
        ConnectorInfo(
            connector="crm",
            title="CRM",
            summary="x",
            category="utility",
            egress=(Egress(protocol="https", host_setting="CRM_HOST"),),
        )


def test_connector_info_refuses_duplicate_settings_and_bad_names() -> None:
    setting = Setting(env="CRM_TOKEN", description="x", secret=True)
    with pytest.raises(ValidationError, match="twice"):
        ConnectorInfo(
            connector="crm", title="CRM", summary="x", category="utility", settings=(setting,) * 2
        )
    with pytest.raises(ValidationError, match="lowercase"):
        ConnectorInfo(connector="Crm", title="CRM", summary="x", category="utility")
