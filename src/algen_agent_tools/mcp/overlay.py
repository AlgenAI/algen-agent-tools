"""A governance overlay for one MCP server.

The overlay is a reviewed mapping, checked into the project, that declares each remote tool the
agent may use: its side effect, permission, approval rule, timeout and the input schema it was
reviewed against. MCP's own annotations are hints a server sets about itself; the overlay is
the deployment's decision.

Each declared tool becomes an ordinary Runtime tool, so ``describe`` and Studio list it with its
side effect before anything connects. A remote tool the overlay does not declare is never
exposed. When the server starts advertising a different schema for a declared tool, calls to it
are refused until the overlay is reviewed again.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from algen_agent_runtime.exceptions.errors import ConfigurationError
from algen_agent_runtime.mcp.contracts import (
    MCPSseServerConfig,
    MCPStdioServerConfig,
    MCPStreamableHttpServerConfig,
)
from algen_agent_runtime.tools.authoring import (
    Toolkit,
    ToolPreset,
    destructive,
    external,
    read,
    write,
)
from algen_agent_runtime.tools.contracts import Tool, ToolDefinition
from algen_agent_runtime.types.contracts import RetryPolicy
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Effect = Literal["read", "write", "external", "destructive"]

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_ENV_REFERENCE = re.compile(r"env://([A-Za-z_][A-Za-z0-9_]*)")
ServerConfig = Annotated[
    MCPStdioServerConfig | MCPSseServerConfig | MCPStreamableHttpServerConfig,
    Field(discriminator="transport"),
]


class ServerHolder(BaseModel):
    """Parses one MCP server configuration, choosing the model by ``transport``."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    server: ServerConfig


# The shape every overlay tool returns: the server's text content and, when it sends one, its
# structured content.
OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "data": {}},
    "required": ["text"],
    "additionalProperties": False,
}


def canonical(schema: dict[str, Any]) -> str:
    """A stable text form of a schema, used to notice when a server changes one."""
    return json.dumps(schema, sort_keys=True, separators=(",", ":"))


def close_objects(schema: Any) -> Any:
    """``schema`` with every object that lists properties closed to unknown fields."""
    if isinstance(schema, list):
        return [close_objects(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    closed = {key: close_objects(value) for key, value in schema.items()}
    if closed.get("type") == "object" and "properties" in closed:
        if closed.get("additionalProperties") in (None, True):
            closed["additionalProperties"] = False
    return closed


class McpToolRule(BaseModel):
    """The deployment's decision about one remote tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    effect: Effect
    input_schema: dict[str, Any] = Field(
        description="The tool's input schema exactly as the server advertised it when this "
        "rule was reviewed. Calls are refused if the server's schema changes."
    )
    description: str | None = Field(
        default=None, description="Replaces the server's description, which the model reads."
    )
    approval: bool | None = Field(
        default=None,
        description="Ask a person before every call. Defaults to true for destructive tools "
        "and to the agent's approval policy otherwise.",
    )
    timeout_seconds: float | None = Field(default=None, gt=0, le=3_600)
    permissions: tuple[str, ...] | None = Field(
        default=None, description="Explicit permissions; default <overlay>.read or .write."
    )
    max_result_bytes: int = Field(default=262_144, ge=1, le=16_777_216)

    @field_validator("input_schema")
    @classmethod
    def _object_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "object":
            raise ValueError("input_schema must be an object schema")
        return value

    def preset(self) -> ToolPreset:
        approval = self.approval
        match self.effect:
            case "read":
                if approval:
                    raise ValueError("a read tool cannot require approval; classify it as write")
                preset = read()
            case "write":
                preset = write(approval=bool(approval))
            case "external":
                preset = external(approval=bool(approval))
            case "destructive":
                preset = destructive(approval=True if approval is None else approval)
        if self.timeout_seconds is not None:
            preset = preset.replace(timeout_seconds=self.timeout_seconds)
        if self.permissions is not None:
            preset = preset.replace(permissions=frozenset(self.permissions))
        return preset


class McpOverlay(BaseModel):
    """One MCP server and the remote tools an agent may call on it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        description="The toolkit name: tools are <name>.<remote tool>, permissions <name>.*.",
    )
    server: ServerConfig
    tools: dict[str, McpToolRule] = Field(min_length=1)
    on_drift: Literal["refuse", "allow"] = Field(
        default="refuse",
        description="What to do when the server advertises a schema other than the reviewed "
        "one: refuse calls to that tool (default) or allow them.",
    )
    allowed_hosts: tuple[str, ...] | None = Field(
        default=None,
        description="Hosts an HTTP transport may reach; default the server URL's host only.",
    )

    @model_validator(mode="after")
    def _check(self) -> McpOverlay:
        if self.server.allowed_tools != frozenset({"*"}) or self.server.tool_policies:
            raise ValueError(
                "declare tools under the overlay's tools, not server.allowed_tools or "
                "server.tool_policies"
            )
        for remote, rule in self.tools.items():
            if not _TOOL_NAME.fullmatch(self.tool_name(remote).split(".", 1)[1]):
                raise ValueError(f"remote tool {remote!r} cannot be named as a Runtime tool")
            try:
                rule.preset()
            except ValueError as exc:
                raise ValueError(f"tool {remote!r}: {exc}") from exc
        if isinstance(self.server, MCPStdioServerConfig):
            for key, value in self.server.env.items():
                if not value.startswith("env://"):
                    raise ValueError(
                        f"server.env[{key!r}] must be an env:// reference, not a literal value"
                    )
        return self

    def tool_name(self, remote: str) -> str:
        return f"{self.name}.{remote.lower().replace('-', '_')}"

    def env(self) -> tuple[str, ...]:
        """Every variable the server configuration references."""
        dumped = json.dumps(self.server.model_dump(mode="json"))
        return tuple(sorted(set(_ENV_REFERENCE.findall(dumped))))

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> McpOverlay:
        return cls.model_validate(mapping)

    @classmethod
    def from_file(cls, path: str | Path) -> McpOverlay:
        """Load an overlay from a YAML or JSON file."""
        try:
            mapping = yaml.safe_load(Path(path).read_text())
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigurationError(f"could not read MCP overlay {path}: {exc}") from exc
        if not isinstance(mapping, dict):
            raise ConfigurationError(f"MCP overlay {path} must be a mapping")
        return cls.model_validate(mapping)

    def toolkit(self, *, session_factory: Any = None) -> McpToolkit:
        """The toolkit for this overlay; ``session_factory`` replaces the real connection."""
        from algen_agent_tools.mcp.session import open_session

        async def open_client(secrets: Any) -> Any:
            return await open_session(self, secrets, session_factory=session_factory)

        toolkit = McpToolkit(self.name, env=self.env(), client=open_client)
        for remote, rule in self.tools.items():
            toolkit.add(self._tool(toolkit, remote, rule))
        return toolkit

    def _tool(self, toolkit: McpToolkit, remote: str, rule: McpToolRule) -> Tool:
        from algen_agent_tools.mcp.session import call_handler

        preset = rule.preset()
        definition = ToolDefinition(
            name=self.tool_name(remote),
            version="1.0.0",
            description=rule.description or f"Call {remote} on the {self.server.name} MCP server.",
            input_schema=close_objects(rule.input_schema),
            output_schema=OUTPUT_SCHEMA,
            required_permissions=(
                preset.permissions
                if preset.permissions is not None
                else frozenset({f"{self.name}.{preset.access}"})
            ),
            side_effect=preset.side_effect,
            idempotency=preset.idempotency,
            timeout_seconds=preset.timeout_seconds,
            retry_policy=(
                RetryPolicy(max_attempts=preset.max_attempts)
                if preset.max_attempts is not None
                else RetryPolicy()
            ),
            max_result_bytes=rule.max_result_bytes,
            requires_approval=preset.requires_approval,
            audit_metadata={
                "classification_source": "overlay",
                "mcp_server": self.server.name,
                "mcp_tool": remote,
                # The remote server receives no idempotency key; the Runtime's ledger and the
                # single attempt are what keep a write from repeating.
                "remote_idempotency": "none",
            },
        )
        return Tool(definition, call_handler(toolkit, remote, definition))


class McpToolkit(Toolkit):
    """A toolkit whose tools are declared by an :class:`McpOverlay`."""

    def __init__(self, name: str, *, env: tuple[str, ...], client: Callable[..., Any]) -> None:
        super().__init__(name, env=env, client=client)
        self._overlay_tools: list[Tool] = []

    def add(self, tool: Tool) -> None:
        self._overlay_tools.append(tool)

    @property
    def tools(self) -> tuple[Tool, ...]:
        return (*super().tools, *self._overlay_tools)

    def register(self, context: Any) -> None:
        super().register(context)
        for tool in self._overlay_tools:
            context.tools.register(tool)
