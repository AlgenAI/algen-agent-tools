# Changelog

All notable changes to Algen Agent Tools are recorded here. The project follows
[Semantic Versioning](https://semver.org/); during the `0.x` series public contracts are
experimental, and every breaking change carries an upgrade note.

A tool's name, input schema, side effect and permission are part of the public contract.

## [Unreleased]

### Added

- Documents (`algen_agent_tools.documents`, extra `documents`): `documents_toolkit(artifact_loader=...)`
  offers `documents.extract_text` (read), the readable text of a PDF, DOCX, text or Markdown
  artifact with the Runtime's document inspection findings (hidden text, instructions aimed at
  the model, risky DOCX packaging) and a `pass`/`review`/`quarantine` disposition. Hidden text is
  never returned, and a quarantined document's text is withheld unless
  `quarantine="return"`. Built in code, as it needs the deployment's artifact loader; no plugin
  yet. The `documents` extra installs `algen-agent-runtime[documents]` (PyMuPDF, AGPL-3.0) for
  PDFs and is part of `all`.
- AGENTS.md: `algen_agent_runtime.security.documents` joins the Runtime API connectors may use.

## [0.2.0a1] - 2026-10-27

### Added

- The connector catalog: `algen_agent_tools/connectors.json`, generated from the code and
  shipped in the wheel, lists every connector with its title, category, maturity, pinned pip
  requirement, docs link, settings (which are secrets, required, defaults), egress hosts, options
  that still need Python, every tool's full description (input and output schema, side effect,
  idempotency, permissions, timeout, approval) and the agent configuration that turns it on.
  Hosts such as Studio read it to list and enable connectors without running connector code.
  `python -m algen_agent_tools catalog` prints it, `--write` regenerates it and `--check` fails
  when it is stale; `algen_agent_tools.catalog.load_catalog()` reads the shipped copy. Each
  release also attaches it to the GitHub Release.
- `ConnectorInfo`, `Setting` and `Egress` (`algen_agent_tools.catalog`): what each connector
  declares about itself on its plugin (`connector_info`). Plugin manifests take their `env`
  from it.
- Contract tests: the committed catalog is the snapshot of every tool's public contract and must
  match the code, and the import paths the Runtime's deprecation messages and the migration
  guide point to must stay importable.
- `algen_agent_tools.attachments`: the file handling every connector that sends or receives
  files shares. `AttachmentRef` (an artifact reference; `EmailAttachmentReference` now extends
  it with an unchanged schema), the deployment-provided `ArtifactLoader` and `ArtifactSink`,
  `AttachmentPolicy` (size limit, allowed types, and a check that a file's leading bytes match
  its declared type) and `add_get_attachment_tool`, which stores a fetched file as an artifact
  and returns only its reference. No connector offers `get_attachment` yet.
- `ALGEN_TOOLS_SMTP_ALLOWED_DOMAINS` and `ALGEN_TOOLS_GMAIL_ALLOWED_DOMAINS`: the recipient
  domain allowlist as an optional, comma-separated setting, so a deployment configured without
  code (such as from Studio) can bound who an agent writes to. `allowed_domains=` in code still
  takes precedence; a malformed domain fails when the client opens. `FakeMailbox.factory` takes
  `allowed_domains` to test it.
- `InboundEmailSummary` and `InboundEmailAttachment` are also importable from
  `algen_agent_tools.mail`, for every mailbox connector; `algen_agent_tools.gmail` still exports
  them.

### Changed

- Gmail runs on the package's shared HTTP and OAuth code: every request is limited to the
  configured API and token hosts, and a response larger than twice the attachment limit (plus
  1 MB) is refused. Tools, schemas and error messages are unchanged.

- Tested against `algen-agent-runtime` 0.1.0a13; the supported range is unchanged
  (`>=0.1.0a12,<0.2`).

### Upgrade notes

- No tool's name, input schema, side effect or permissions changed; agent configurations keep
  working as they are.
- The SMTP and Gmail plugins declare one more environment variable,
  `ALGEN_TOOLS_<SMTP|GMAIL>_ALLOWED_DOMAINS`, so `describe` lists it. It is optional: leave it
  unset to keep today's behaviour (any recipient domain).
- Gmail now refuses requests to any host other than its configured API and token URLs, and
  responses larger than twice `max_attachment_bytes` plus 1 MB. Deployments using the default
  settings are unaffected.
- Connector authors: `toolkit_plugin` (internal) now takes a `ConnectorInfo` instead of a name
  and `env`; regenerate `connectors.json` with `python -m algen_agent_tools catalog --write`
  when a connector changes. See docs/writing-a-connector.md.

## [0.1.0a2] - 2026-10-27

### Changed

- Email tools run under the Runtime's default guardrails. Every address field in
  `smtp.send_email` and `gmail.send_email` (`to`, `cc`, `bcc`, `reply_to`) declares
  `"format": "email"` in its input schema, and the Runtime 0.1.0a12 `pii` guardrail leaves a
  single address in such a field unredacted. Names, the subject and the bodies are still
  screened, and the Runtime still refuses a call in which it redacts anything.
- Requires `algen-agent-runtime>=0.1.0a12,<0.2`.

### Upgrade notes

- Agents that left `pii` out of `guardrail_policy.tool_policies` only so email could be sent can
  remove that setting and keep the default guardrails. A body or subject that contains an email
  address or phone number is now refused by `pii`; keep such details out of messages, or keep
  the old setting for that agent.
- The input schema of each `send_email` tool gains `"format": "email"` on address fields; the
  accepted values are unchanged.

## [0.1.0a1] - 2026-10-27

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
  recipients and Reply-To, and attachments are offered only with an artifact loader that receives
  the call's `ToolContext` to check the artifact's tenant. The transports
  (`SmtpEmailSender`, `GmailMailboxClient`) and message models (`algen_agent_tools.mail`) move
  from `algen_agent_runtime.communications`, with one fix: the Gmail transport now delivers Bcc
  recipients (it put no Bcc header in the raw message, so Gmail never sent to them). `FakeMailbox` for tests. An agent that
  sends email leaves the `pii` guardrail out of `guardrail_policy.tool_policies`; with it, the
  Runtime refuses the redacted arguments (documented on each connector page).
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
- An end-to-end test runs an agent through the Runtime's executor, guardrails and approval
  flow: the email is sent once after approval and never after rejection.
- CI: a PostgreSQL service-container job runs the integration tests; live email tests run only
  with `ALGEN_TOOLS_LIVE=1`.
- CI: lint, strict typing and tests on Python 3.12 and 3.13; the suite against the lowest and
  highest supported Runtime; coverage; package build and a clean-install check that describes
  every installed plugin; DCO, CodeQL, pip-audit, Bandit, Gitleaks and dependency review.
- Brand assets (`docs/assets/brand`): the Tools logo and light and dark README banners in the
  Algen palette, and `docs/releasing.md`.
- Project policies: connector entry criteria, security review checklist, versioning and
  deprecation policy (`docs/connector-policy.md`), contributor and agent guides, security policy
  and governance.
