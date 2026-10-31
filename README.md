<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/AlgenAI/algen-agent-tools/refs/heads/main/docs/assets/brand/banner-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/AlgenAI/algen-agent-tools/refs/heads/main/docs/assets/brand/banner-light.svg">
  <img alt="Algen Agent Tools — Typed tools for agents. Governed by default." src="https://raw.githubusercontent.com/AlgenAI/algen-agent-tools/refs/heads/main/docs/assets/brand/banner-light.svg">
</picture>

# Algen Agent Tools

[![Quality](https://github.com/AlgenAI/algen-agent-tools/actions/workflows/quality.yml/badge.svg)](https://github.com/AlgenAI/algen-agent-tools/actions/workflows/quality.yml)
[![Security](https://github.com/AlgenAI/algen-agent-tools/actions/workflows/security.yml/badge.svg)](https://github.com/AlgenAI/algen-agent-tools/actions/workflows/security.yml)
[![PyPI](https://img.shields.io/pypi/v/algen-agent-tools.svg)](https://pypi.org/project/algen-agent-tools/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Curated, governed connectors for agents built on
[Algen Agent Runtime](https://github.com/AlgenAI/algen-agent-runtime).

Each connector is a Runtime `Toolkit` of narrow, typed actions, registered through a declared
plugin. An agent names the plugin in `agent.yaml`, the Runtime checks every tool it enables, and
Algen Agent Studio lists each tool with its side effect, permissions and environment variables
from `algen-agent-runtime describe`, without running project code.

> [!IMPORTANT]
> `0.2.0a7` is a pre-release. Tool contracts may change between alpha releases; every change is
> recorded in [CHANGELOG.md](CHANGELOG.md) with an upgrade note. Review the
> [security policy](SECURITY.md) and each connector's guarantees before deployment. What must be
> true before the beta and 1.0 is tracked in [beta and GA criteria](docs/ga-criteria.md).

## Why a separate package

- The Runtime stays small and domain-agnostic; vendor APIs change faster than its core.
- Connectors get their own release cadence, owners and security review.
- Every connector is a plugin, so Studio sees it with no extra work.

| Layer | Package | Contains |
| --- | --- | --- |
| Core | `algen-agent-runtime` | Contracts, `@tool`, presets, `Toolkit`, plugins, `describe`, MCP and HTTP adapters |
| Connectors | `algen-agent-tools` | Generic toolkits, one optional extra each |
| Domain packs | Their own packages | Product-specific tools (hiring, text-to-SQL, HR, ...) |

## Install

Python 3.12 or 3.13. Install the package with the extra for each connector you use:

```bash
pip install "algen-agent-tools[postgres]"        # one connector
pip install "algen-agent-tools[all]"             # every connector
```

The package supports `algen-agent-runtime>=0.1.0a20,<0.2`; see
[compatibility](docs/compatibility.md).

## Use a connector

Name the connector's plugin in `domain_plugins`, trust the package's prefix, enable the tools
and grant their permissions:

```yaml
agents:
  - name: assistant
    version: 1.0.0
    description: Checks that tools are wired up
    system_instructions: Use sample.echo when asked to echo text.
    default_model: {name: m, provider: demo}
    domain_plugins: [algen_agent_tools.sample]
    enabled_tools: [sample.echo]
    tool_permissions: [sample.read]
security:
  trusted_plugin_prefixes: [algen_agent_tools]
```

Then check the project the way Studio will:

```bash
algen-agent-runtime check agent.yaml
algen-agent-runtime describe --config agent.yaml --json
```

Each zero-configuration plugin reads its settings from `env://` references at the moment its
client first opens, never at import. To configure a connector in code (another name, other
environment variables, a fake for tests), build its toolkit in your own plugin:

```python
from algen_agent_runtime.plugins.sdk import PluginManifest, plugin
from algen_agent_tools.sample import sample_toolkit


@plugin(PluginManifest(name="acme.tools", version="1.0.0", publisher="acme"))
class AcmeTools:
    def register(self, context):
        sample_toolkit(name="diagnostics").register(context)
```

## Connectors

| Connector | Plugin | Extra | Tools | Docs |
| --- | --- | --- | --- | --- |
| PostgreSQL | `algen_agent_tools.postgres` | `postgres` | `postgres.list_tables`, `postgres.describe_table` (read), plus your own typed queries | [postgres](docs/connectors/postgres.md) |
| SQLite | `algen_agent_tools.sqlite` | `sqlite` | `sqlite.list_tables`, `sqlite.describe_table` (read), plus your own typed queries | [sqlite](docs/connectors/sqlite.md) |
| MySQL / MariaDB | `algen_agent_tools.mysql` | `mysql` | `mysql.list_tables`, `mysql.describe_table` (read), plus your own typed queries | [mysql](docs/connectors/mysql.md) |
| SMTP | `algen_agent_tools.smtp` | `smtp` | `smtp.send_email` (external) | [smtp](docs/connectors/smtp.md) |
| Gmail | `algen_agent_tools.gmail` | `gmail` | `gmail.list_messages`, `gmail.get_message` (read), `gmail.send_email` (external) | [gmail](docs/connectors/gmail.md) |
| IMAP | `algen_agent_tools.imap` | `imap` | `imap.list_messages`, `imap.get_message` (read), with an artifact sink `imap.get_attachment` (read) | [imap](docs/connectors/imap.md) |
| Outlook | `algen_agent_tools.outlook` | `outlook` | `outlook.list_messages`, `outlook.get_message` (read), `outlook.send_email` (external) | [outlook](docs/connectors/outlook.md) |
| Telegram | `algen_agent_tools.telegram` | `telegram` | `telegram.list_messages` (read), `telegram.send_message` (external) | [telegram](docs/connectors/telegram.md) |
| GitHub | `algen_agent_tools.github` | `github` | 37 reviewed tools from GitHub's MCP server: 22 read, 15 write (5 need approval) | [github](docs/connectors/github.md) |
| Discord | `algen_agent_tools.discord` | `discord` | `discord.list_conversations`, `discord.list_messages` (read), `discord.send_message` (external) | [discord](docs/connectors/discord.md) |
| Documents | none: built in code with an artifact loader | `documents` | `documents.extract_text` (read) | [documents](docs/connectors/documents.md) |
| Webhook | none: built in code with typed targets | `webhook` | one `webhook.<action>` (external) per target | [webhook](docs/connectors/webhook.md) |
| Sample | `algen_agent_tools.sample` | none | `sample.echo` (read) | [sample](docs/connectors/sample.md) |

### MCP servers

For services with a well-maintained MCP server, `algen_agent_tools.mcp` adds a
[governance overlay](docs/mcp-overlays.md) instead of a native client: a reviewed mapping that
declares each remote tool's side effect, permission, approval rule and pinned input schema. The
tools appear in `describe` and Studio before anything connects, undeclared tools are never
exposed, and a tool whose schema changes is refused until it is reviewed again.

```bash
pip install "algen-agent-tools[mcp]"
python -m algen_agent_tools.mcp snapshot server.yaml --name github --output github.overlay.yaml
```

Moving from the Runtime's built-in email and database connectors? See
[migrating from the Runtime](docs/migrating-from-runtime.md).

Proposed connectors follow the [connector policy](docs/connector-policy.md): at least two
projects need it, it holds no domain logic, it has an owner and it passed a security review.
When a well-maintained MCP server exists, the package ships a governance overlay instead of a
native client.

## Rules every connector follows

- Narrow, typed actions (`list_open_invoices`, `post_message`), never a raw passthrough such as
  free-form SQL or arbitrary HTTP.
- Side effect, permission, idempotency, retries and timeout come from a Runtime preset.
  Anything that writes, sends or deletes is keyed by the idempotency key and tried once.
- Secrets are `env://` references only, resolved when a client opens.
- An in-memory fake for offline tests; live tests run only when `ALGEN_TOOLS_LIVE=1`.
- Every toolkit passes `algen_agent_tools.testing.assert_toolkit_contract`.

## Development

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
make check
```

See [CONTRIBUTING.md](CONTRIBUTING.md), [writing a connector](docs/writing-a-connector.md) and
[the release process](docs/releasing.md).

## Security

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md). Do not open a public
issue.

## License

[Apache License 2.0](LICENSE).
