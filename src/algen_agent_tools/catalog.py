"""The connector catalog: what each plugin in this package is, needs and offers, as data.

Each connector declares a :class:`ConnectorInfo` on its plugin. :func:`build_catalog` combines it
with the Runtime's own description of the plugin's tools into a :class:`Catalog`, which is
committed as ``connectors.json`` and shipped with the package. Hosts such as Studio read that
file to list connectors, collect their settings and write an agent's configuration, without
importing or running any connector code.

The catalog doubles as the package's contract snapshot: it records every tool's name, input
schema, side effect and permissions, and a test fails when the committed file drifts from the
code.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from importlib import metadata, resources
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from algen_agent_tools import PUBLISHER, TRUSTED_PREFIX, __version__

CATALOG_VERSION = 1
"""Raised only for a change a reader of an older catalog would misread; new fields are not."""

CATALOG_FILE = "connectors.json"
PACKAGE = "algen-agent-tools"
REPOSITORY = "https://github.com/AlgenAI/algen-agent-tools"

_ENV = re.compile(r"^[A-Z][A-Z0-9_]*$")
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

Category = Literal[
    "database", "development", "documents", "email", "messaging", "utility", "webhook"
]
Maturity = Literal["alpha", "beta", "stable", "deprecated"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Setting(_Frozen):
    """One environment variable a connector reads through an ``env://`` reference."""

    env: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=500)
    secret: bool = False
    """A credential: a host stores it in its secret store and never shows it back."""
    required: bool = True
    default: str | None = Field(default=None, max_length=200)
    """What the connector uses when an optional setting is unset."""
    example: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _check(self) -> Setting:
        if not _ENV.fullmatch(self.env):
            raise ValueError(f"setting {self.env!r} is not an environment variable name")
        if self.secret and (self.default is not None or self.example is not None):
            raise ValueError(f"secret setting {self.env} must not carry a default or example")
        if self.required and self.default is not None:
            raise ValueError(f"required setting {self.env} must not have a default")
        return self


class Egress(_Frozen):
    """One destination the connector opens connections to.

    Either a fixed ``host``, or ``host_setting``: the setting whose value names the host (a host
    name, or a DSN or URL that contains one).
    """

    protocol: Literal["https", "imap", "mysql", "postgres", "smtp"]
    host: str | None = Field(default=None, max_length=253)
    host_setting: str | None = Field(default=None, max_length=128)
    port: int | None = Field(default=None, ge=1, le=65_535)

    @model_validator(mode="after")
    def _check(self) -> Egress:
        if (self.host is None) == (self.host_setting is None):
            raise ValueError("an egress rule names exactly one of host and host_setting")
        return self


class ConnectorInfo(_Frozen):
    """What a connector declares about itself; the tools come from the Runtime, not from here."""

    connector: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    category: Category
    maturity: Maturity = "alpha"
    extra: str | None = Field(default=None, max_length=64)
    """The optional dependency to install; ``None`` when the base package is enough."""
    settings: tuple[Setting, ...] = ()
    egress: tuple[Egress, ...] = ()
    code_only_options: tuple[str, ...] = ()
    """Factory options that need Python today, so a configuration-only host cannot offer them."""

    @model_validator(mode="after")
    def _check(self) -> ConnectorInfo:
        if not _NAME.fullmatch(self.connector):
            raise ValueError(f"connector {self.connector!r} is not a lowercase identifier")
        names = [setting.env for setting in self.settings]
        if len(set(names)) != len(names):
            raise ValueError(f"{self.connector} declares a setting twice")
        unknown = {
            rule.host_setting for rule in self.egress if rule.host_setting is not None
        } - set(names)
        if unknown:
            raise ValueError(f"{self.connector} egress names undeclared settings {sorted(unknown)}")
        return self

    @property
    def env(self) -> tuple[str, ...]:
        """Every setting's variable name, in declaration order."""
        return tuple(setting.env for setting in self.settings)


class CatalogTool(_Frozen):
    name: str
    version: str
    description: str
    side_effect: str
    idempotency: str
    permissions: tuple[str, ...]
    timeout_seconds: float
    max_attempts: int
    requires_approval: bool
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class AgentSecurity(_Frozen):
    trusted_plugin_prefixes: tuple[str, ...]


class ManifestSnippet(_Frozen):
    """The agent configuration that turns the connector on with every tool it offers."""

    domain_plugins: tuple[str, ...]
    enabled_tools: tuple[str, ...]
    tool_permissions: tuple[str, ...]
    security: AgentSecurity


class CatalogConnector(_Frozen):
    plugin: str
    connector: str
    title: str
    summary: str
    category: Category
    maturity: Maturity
    requirement: str
    """The pip requirement that installs the connector, pinned to this release."""
    docs_url: str
    settings: tuple[Setting, ...]
    egress: tuple[Egress, ...]
    code_only_options: tuple[str, ...]
    tools: tuple[CatalogTool, ...]
    manifest: ManifestSnippet


class Catalog(_Frozen):
    catalog_version: int = CATALOG_VERSION
    package: str = PACKAGE
    version: str
    publisher: str = PUBLISHER
    trusted_prefix: str = TRUSTED_PREFIX
    runtime_requirement: str
    connectors: tuple[CatalogConnector, ...]

    def to_json(self) -> str:
        """The canonical text of ``connectors.json``: stable key order, one trailing newline."""
        return json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def _runtime_requirement() -> str:
    for requirement in metadata.requires(PACKAGE) or ():
        if requirement.startswith("algen-agent-runtime") and "extra ==" not in requirement:
            return requirement.replace(" ", "")
    raise LookupError(f"{PACKAGE} does not declare its algen-agent-runtime requirement")


def _connector(plugin: str, info: ConnectorInfo, version: str) -> CatalogConnector:
    from algen_agent_tools.testing import plugin_tools

    tools = tuple(
        CatalogTool(
            name=tool.name,
            version=tool.version,
            description=tool.description,
            side_effect=tool.side_effect.value,
            idempotency=tool.idempotency.value,
            permissions=tool.required_permissions,
            timeout_seconds=tool.timeout_seconds,
            max_attempts=tool.max_attempts,
            requires_approval=tool.requires_approval,
            input_schema=tool.input_schema,
            output_schema=tool.output_schema,
        )
        for tool in sorted(plugin_tools(plugin).values(), key=lambda item: item.name)
    )
    extra = f"[{info.extra}]" if info.extra else ""
    return CatalogConnector(
        plugin=plugin,
        connector=info.connector,
        title=info.title,
        summary=info.summary,
        category=info.category,
        maturity=info.maturity,
        requirement=f"{PACKAGE}{extra}=={version}",
        docs_url=f"{REPOSITORY}/blob/v{version}/docs/connectors/{info.connector}.md",
        settings=info.settings,
        egress=info.egress,
        code_only_options=info.code_only_options,
        tools=tools,
        manifest=ManifestSnippet(
            domain_plugins=(plugin,),
            enabled_tools=tuple(tool.name for tool in tools),
            tool_permissions=tuple(
                sorted({permission for tool in tools for permission in tool.permissions})
            ),
            security=AgentSecurity(trusted_plugin_prefixes=(TRUSTED_PREFIX,)),
        ),
    )


def build_catalog() -> Catalog:
    """The catalog of every plugin this package installs, from the code as it is now.

    It loads each plugin and asks the Runtime to describe it; no client opens and no secret is
    read.
    """
    from algen_agent_runtime.plugins.sdk import ENTRY_POINT_GROUP

    connectors: list[CatalogConnector] = []
    for entry in sorted(metadata.entry_points(group=ENTRY_POINT_GROUP), key=lambda item: item.name):
        if not entry.name.startswith(f"{TRUSTED_PREFIX}."):
            continue
        info = getattr(entry.load(), "connector_info", None)
        if not isinstance(info, ConnectorInfo):
            raise LookupError(f"plugin {entry.name} declares no ConnectorInfo")
        connectors.append(_connector(entry.name, info, __version__))
    return Catalog(
        version=__version__,
        runtime_requirement=_runtime_requirement(),
        connectors=tuple(connectors),
    )


def load_catalog() -> Catalog:
    """The catalog shipped with this installed package."""
    text = resources.files("algen_agent_tools").joinpath(CATALOG_FILE).read_text("utf-8")
    return Catalog.model_validate_json(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m algen_agent_tools",
        description="Print or check the connector catalog.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    catalog = commands.add_parser("catalog", help="print the catalog built from the code")
    action = catalog.add_mutually_exclusive_group()
    action.add_argument("--write", action="store_true", help=f"rewrite the packaged {CATALOG_FILE}")
    action.add_argument(
        "--check",
        action="store_true",
        help=f"fail when the packaged {CATALOG_FILE} differs from the code",
    )
    arguments = parser.parse_args(argv)
    text = build_catalog().to_json()
    if not (arguments.write or arguments.check):
        sys.stdout.write(text)
        return 0
    path = Path(str(resources.files("algen_agent_tools").joinpath(CATALOG_FILE)))
    if arguments.write:
        path.write_text(text, encoding="utf-8")
        return 0
    if not path.is_file() or path.read_text("utf-8") != text:
        sys.stderr.write(
            f"{CATALOG_FILE} is out of date: a connector or tool contract changed. Run "
            "`python -m algen_agent_tools catalog --write` and record the change in "
            "CHANGELOG.md.\n"
        )
        return 1
    return 0
