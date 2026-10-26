# Algen Agent Tools

Curated, governed connectors for agents built on
[Algen Agent Runtime](https://github.com/AlgenAI/algen-agent-runtime).

Each connector is a Runtime `Toolkit` of narrow, typed actions, registered through a declared
plugin. An agent names the plugin in `agent.yaml`, the Runtime checks every tool it enables, and
Algen Agent Studio lists each tool with its side effect, permissions and environment variables
from `algen-agent-runtime describe`, without running project code.

> **Status:** pre-release (`0.1.0aN`). Public contracts may change between alpha releases; every
> change is recorded in [CHANGELOG.md](CHANGELOG.md) with an upgrade note.

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
pip install "algen-agent-tools"            # the sample toolkit only
```

The package supports `algen-agent-runtime>=0.1.0a11,<0.2`; see
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
| Sample | `algen_agent_tools.sample` | none | `sample.echo` (read) | [sample](docs/connectors/sample.md) |

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

See [CONTRIBUTING.md](CONTRIBUTING.md) and [writing a connector](docs/writing-a-connector.md).

## Security

Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md). Do not open a public
issue.

## License

[Apache License 2.0](LICENSE).
