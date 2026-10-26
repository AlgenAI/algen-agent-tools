# Changelog

All notable changes to Algen Agent Tools are recorded here. The project follows
[Semantic Versioning](https://semver.org/); during the `0.x` series public contracts are
experimental, and every breaking change carries an upgrade note.

A tool's name, input schema, side effect and permission are part of the public contract.

## [Unreleased]

## [0.1.0a1] - Unreleased

### Added

- The package: `algen-agent-tools`, importable as `algen_agent_tools`, supporting
  `algen-agent-runtime>=0.1.0a11,<0.2`. Each connector is a Runtime `Toolkit` registered by a
  plugin in the `algen_agent_runtime.plugins` entry point group under the trusted prefix
  `algen_agent_tools`, with one optional extra per connector and an `all` extra.
- `algen_agent_tools.sample`: a dependency-free reference toolkit (`sample.echo`) used to check
  an install end to end and as the template for new connectors.
- PostgreSQL (`algen_agent_tools.postgres`, extra `postgres`): `PostgresToolkit.query` declares
  a named, parameterised query as a typed tool, so the model never writes SQL. Reads run in a
  read-only transaction and stream through a cursor that fails past a row limit; every
  statement runs under a `statement_timeout` equal to the tool's timeout; writes can bind the
  call's idempotency key for exactly-once SQL; unknown input fields are refused.
  `fetch_rows` serves custom read tools. The plugin registers `postgres.list_tables` and
  `postgres.describe_table`, limited to the configured schemas. `FakePostgres` for tests.
- SMTP (`algen_agent_tools.smtp`) and Gmail (`algen_agent_tools.gmail`): `smtp.send_email`,
  `gmail.send_email` (external, keyed, one attempt) and `gmail.list_messages` (read). The sender
  is fixed by the deployment, the model cannot add headers, an optional domain allowlist bounds
  recipients, and attachments are offered only with an artifact loader. The transports
  (`SmtpEmailSender`, `GmailMailboxClient`) and message models (`algen_agent_tools.mail`) move
  from `algen_agent_runtime.communications` unchanged. `FakeMailbox` for tests.
- MCP governance overlays (`algen_agent_tools.mcp`, extra `mcp`): `McpOverlay` declares, per
  remote tool, its side effect, approval rule, timeout, permissions, result limit and the input
  schema it was reviewed against. Each declared tool is an ordinary Runtime tool, so `describe`
  and Studio list it before anything connects; undeclared tools are never exposed; arguments are
  validated against the closed schema; a tool the server dropped or whose schema changed is
  refused (`on_drift: allow` relaxes this); stdio environment values must be `env://`
  references; HTTP transports reach only the server's host. `overlay_plugin` declares an
  overlay file from a project's plugin, `python -m algen_agent_tools.mcp snapshot` drafts one
  from a live server for review, and `FakeMcpServer` serves tests.
- `docs/migrating-from-runtime.md` maps the Runtime's deprecated `communications` connectors
  and `database_tool` to this package.
- `algen_agent_tools.testing`: `assert_toolkit_contract` and `toolkit_contract_problems` (the
  Runtime's tool contract plus naming, permission-prefix and no-passthrough rules),
  `describe_plugin` and `plugin_tools` (the Runtime inventory for one plugin, without calling a
  tool).
- CI: a PostgreSQL service-container job runs the integration tests; live email tests run only
  with `ALGEN_TOOLS_LIVE=1`.
- CI: lint, strict typing and tests on Python 3.12 and 3.13; the suite against the lowest and
  highest supported Runtime; coverage; package build and a clean-install check that describes
  every installed plugin; DCO, CodeQL, pip-audit, Bandit, Gitleaks and dependency review.
- Project policies: connector entry criteria, security review checklist, versioning and
  deprecation policy (`docs/connector-policy.md`), contributor and agent guides, security policy
  and governance.
