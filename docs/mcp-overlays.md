# MCP governance overlays

When a well-maintained MCP server already does what an agent needs, this package ships a
governance overlay instead of a native connector. An overlay is a reviewed mapping, kept in the
project, that declares each remote tool the agent may call:

- its **side effect** (`read`, `write`, `external`, `destructive`), and from it the permission,
  idempotency, retries and timeout, through the same Runtime presets native connectors use;
- whether a person must **approve** every call;
- the **input schema** it was reviewed against.

MCP's own tool annotations (`readOnlyHint`, `destructiveHint`, ...) are hints a server sets about
itself. The overlay is the deployment's decision.

| | |
| --- | --- |
| Module | `algen_agent_tools.mcp` |
| Extra | `pip install "algen-agent-tools[mcp]"` (the Runtime's `mcp` extra) |
| Owner | @saurav-samantray |

## What an overlay guarantees

- **Visible before connecting.** Each declared tool is an ordinary Runtime tool, so
  `algen-agent-runtime describe` and Studio list it with its side effect, permission and approval
  rule. The Runtime's own MCP discovery only learns about tools at connect time.
- **Deny by default.** A remote tool the overlay does not declare is never exposed.
- **Schema pinning.** When the client opens, the overlay compares each declared tool with what the
  server advertises. A tool that disappeared, or whose input schema changed, is refused until the
  overlay is reviewed again (`on_drift: allow` relaxes this).
- **Validated arguments.** Arguments are checked against the reviewed schema, with every object
  closed to unknown fields, before anything is sent.
- **Bounded results.** Each tool has a result size limit (256 KB by default) and a timeout; errors
  carry at most 300 characters of the server's message.
- **Secrets by reference.** Tokens, headers and a stdio server's environment are `env://`
  references; a literal value in `server.env` is refused.
- **Host pinning.** An HTTP transport may reach only the server URL's host unless
  `allowed_hosts` says otherwise.

One limit to know: an MCP server receives no idempotency key. Write tools are single-attempt and
the Runtime's ledger stops a replay from calling twice, but a timeout leaves the outcome unknown.
If an action must be exactly-once, that is the case for a native connector.

## Shipped overlays

| Overlay | Plugin | Docs |
| --- | --- | --- |
| GitHub (hosted MCP server) | `algen_agent_tools.github` | [github](connectors/github.md) |

A shipped overlay is a plugin like any connector, listed in the connector catalog. It is checked
against its live server before each release.

## Writing an overlay

Draft one from the live server, then review it:

```bash
cat > server.yaml <<'EOF'
name: github
transport: streamable_http
url: https://api.githubcopilot.com/mcp/
auth_token_ref: env://GITHUB_MCP_TOKEN
EOF
python -m algen_agent_tools.mcp snapshot server.yaml --name github --output github.overlay.yaml
```

The draft pins every advertised tool's schema and leaves each `effect` empty, with the server's
annotation as a comment. It does not load until someone sets every effect. Delete each tool the
agent must not call.

```yaml
name: github                       # tools are github.<tool>, permissions github.read / github.write
server:
  name: github
  transport: streamable_http
  url: https://api.githubcopilot.com/mcp/
  auth_token_ref: env://GITHUB_MCP_TOKEN
tools:
  get_issue:
    effect: read
    description: Read one issue in a repository.   # optional; replaces the server's
    input_schema: {...}                             # pinned by the snapshot
  create_issue:
    effect: write
    approval: true                  # ask a person before every call
    timeout_seconds: 30
    input_schema: {...}
```

| Rule field | Default | Meaning |
| --- | --- | --- |
| `effect` | required | `read`, `write`, `external` or `destructive` |
| `approval` | `true` for destructive, else the agent's approval policy | ask before every call |
| `timeout_seconds` | 30 | per call |
| `permissions` | `<name>.read` or `<name>.write` | explicit permission list |
| `max_result_bytes` | 262144 | result size limit |
| `description` | the server's | what the model reads |
| `input_schema` | required | the reviewed schema |

## Checking an overlay against its server

A server can change a tool at any time. The overlay refuses a changed tool at run time; `check`
tells you sooner:

```bash
export GITHUB_MCP_TOKEN=...          # the overlay's env:// references, from the environment
python -m algen_agent_tools.mcp check github.overlay.yaml
```

```text
github: 11 of 12 declared tools match the server
  input schema changed (refused): create_issue
  offered but not declared (not exposed): delete_repository
Review the overlay: re-run snapshot, compare, and update the pinned schemas.
```

| Exit | Meaning |
| --- | --- |
| 0 | every declared tool is offered with its reviewed schema |
| 1 | a declared tool is missing or its schema changed (with `--strict`, also: the server offers tools the overlay does not declare) |
| 2 | bad arguments or an unreadable overlay |

`--json` prints the same report as JSON. Run it on a schedule in the project's CI, with the
token from the CI's secret store, so a schema change becomes a review, not a failed agent run.
Undeclared tools are never exposed; they are listed so a reviewer can decide whether to add
them.

## Using it

Declare the overlay from your project's plugin:

```python
from pathlib import Path
from algen_agent_tools.mcp import overlay_plugin

GithubTools = overlay_plugin(
    Path(__file__).with_name("github.overlay.yaml"),
    name="acme.github",
    version="1.0.0",
    publisher="acme",
)
```

```yaml
agents:
  - name: triage
    domain_plugins: ["acme.plugins:GithubTools"]
    enabled_tools: [github.get_issue, github.create_issue]
    tool_permissions: [github.read, github.write]
security:
  trusted_plugin_prefixes: [acme]
```

Do not also list the server under the Runtime's `mcp_servers`; the overlay owns the connection.

## Testing

`algen_agent_tools.mcp.fake.FakeMcpServer` advertises tools and answers calls in memory; pass
`overlay.toolkit(session_factory=fake.session_factory)`. Check the overlay with
`assert_toolkit_contract(overlay.toolkit())`.
