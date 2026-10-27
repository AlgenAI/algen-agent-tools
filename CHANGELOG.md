# Changelog

All notable changes to Algen Agent Tools are recorded here. The project follows
[Semantic Versioning](https://semver.org/); during the `0.x` series public contracts are
experimental, and every breaking change carries an upgrade note.

A tool's name, input schema, side effect and permission are part of the public contract.

## [Unreleased]

## [0.2.0a4] - 2026-10-27

### Added

- Webhook (`algen_agent_tools.webhook`, extra `webhook`, no packages): `webhook_toolkit` builds
  one external tool, `webhook.<action>`, for each `WebhookTarget`. The deployment fixes the
  target's URL (an `env://` setting, https unless `allow_http`) and its payload model. The
  model fills in only the typed payload, and unknown fields are refused. Each delivery is a
  JSON `POST` with an `Idempotency-Key` and, given a signing secret, an
  `X-Algen-Signature: t=<ts>,v1=<HMAC-SHA256>` header; `verify()` checks it on the receiving
  side. Redirects aren't followed, the receiver's body is never returned, and the URL (often a
  token) stays out of errors and `httpx` logs. Built in code; no plugin.

- SQLite (`algen_agent_tools.sqlite`, extra `sqlite`, no packages): `sqlite.list_tables` and
  `sqlite.describe_table` (read), plus code-declared typed queries with `?` placeholders. The
  file opens read-only unless the toolkit is built with `read_only=False`. Reads run with
  `PRAGMA query_only`. A progress handler interrupts a statement that runs past the tool's
  timeout. Setting: `ALGEN_TOOLS_SQLITE_PATH`. Part of `all`.
- MySQL / MariaDB (`algen_agent_tools.mysql`, extra `mysql`, which installs `aiomysql[rsa]`):
  `mysql.list_tables` and `mysql.describe_table` (read) on the DSN's database, plus
  code-declared typed queries with `%s` placeholders. Reads run in `START TRANSACTION READ
  ONLY`. The server enforces the tool's timeout (`max_execution_time` on MySQL,
  `max_statement_time` on MariaDB, detected at connect), lock waits are capped, and a
  client-side deadline drops a stuck connection. Errors carry only the MySQL error number.
  `FakeMysql` serves offline tests, and a new CI job runs integration tests against MySQL 8.4.
  Setting: `ALGEN_TOOLS_MYSQL_DSN`. Part of `all`.
- The catalog's `Egress.protocol` adds `mysql`.
- `algen_agent_tools.sql`: the shared database core, with `SqlToolkit`, `Dialect`,
  `TooManyRows` and the catalog models. `PostgresToolkit` is now a `SqlToolkit` with a
  `PostgresDialect`. Its API, tools and errors are unchanged, and too many rows now raises
  `TooManyRows`, a `ToolExecutionError`.

- GitHub (`algen_agent_tools.github`, extra `github`): the first shipped MCP overlay, for GitHub's
  hosted MCP server. It declares 37 of the server's 46 tools: 22 read and 10 write; 3 write
  tools that need approval on every call (`pull_request_review_write`, `push_files`,
  `create_or_update_file`); and 2 destructive (`merge_pull_request`, `delete_file`). Repository
  creation, `run_secret_scanning`, the MCP-Apps UI tool, Copilot review requests and the people
  directory are not exposed. The schemas were pinned from the live server on 2026-10-27. Settings:
  `ALGEN_TOOLS_GITHUB_TOKEN`. Part of `all`.
- Catalog category `development`.

### Upgrade notes

- No existing tool's name, input schema, side effect or permissions changed. `connectors.json`
  entries for the existing connectors are unchanged apart from the version.
- `PostgresToolkit` now runs on the shared `algen_agent_tools.sql` core. Its constructor,
  `query()`, `fetch_rows()`, tools and messages are the same. One difference: more rows than the
  limit now raises `TooManyRows`, a subclass of `ToolExecutionError`, so existing `except`
  clauses still catch it.
- The new connectors are opt-in: GitHub, MySQL and SQLite load only when an agent lists their
  plugin, and the webhook sender exists only when built in code. The `all` extra now also
  installs `aiomysql[rsa]` (MySQL, with `cryptography`) and the Runtime's MCP client (GitHub).
- The connector catalog adds the `development` category and the `mysql` egress protocol. Hosts
  that validate the catalog against a fixed list of values should accept these.

## [0.2.0a3] - 2026-10-27

### Added

- Messaging (`algen_agent_tools.messaging`): the shared base for chat platforms. It provides
  models (`InboundMessage`, `Conversation`, `Participant`, `MessageAttachment`,
  `DeliveryReceipt`), a `MessagingPlatform` that declares `Capability` flags and id patterns, the
  `MessagingProvider` protocol, and `messaging_toolkit`. That toolkit adds
  `<name>.list_conversations`, `<name>.list_messages` and `<name>.get_attachment` (read) and
  `<name>.send_message` (external) as a platform supports them, and limits every tool to an
  allowlist of conversations. `FakeMessaging` serves offline tests.
- Telegram (`algen_agent_tools.telegram`, extra `telegram`): one bot through the Bot API.
  `telegram.list_messages` (read) returns the bot's pending updates without confirming them.
  `telegram.send_message` (external) sends plain text with link previews off.
  `telegram.get_attachment` (read) is available when the toolkit is built with an
  `artifact_sink`. Settings: `ALGEN_TOOLS_TELEGRAM_BOT_TOKEN` and the optional
  `ALGEN_TOOLS_TELEGRAM_ALLOWED_CHATS`. Part of `all`.
- Discord (`algen_agent_tools.discord`, extra `discord`): one bot in one server through the
  REST API, on the shared messaging tools. It offers `discord.list_conversations` and
  `discord.list_messages` (read; newest first, with backward paging), `discord.send_message`
  (external) and, when built with an `artifact_sink`, `discord.get_attachment` (read). Every
  channel is checked to be a text channel or thread of `ALGEN_TOOLS_DISCORD_GUILD_ID`, so direct
  messages and other servers are refused. Sends never ping anyone, suppress link embeds, and
  carry an enforced nonce made from the idempotency key. Part of `all`.
- `python -m algen_agent_tools.mcp check <overlay>`: connects to an overlay's server and reports
  declared tools that are no longer offered or whose input schema changed, and tools the server
  offers that the overlay does not declare. It exits 1 on drift (with `--strict`, also on new
  tools), so projects can run it on a schedule. `--json` prints a JSON report.
  `algen_agent_tools.mcp.check.check()` returns the same result as a `Drift`. The overlay
  toolkit and `check` share one comparison.
- `HostLockedClient(url_secrets=...)` (internal): redacts secrets that an API requires in the
  URL from `httpx` request log lines.

### Upgrade notes

- No existing tool's name, input schema, side effect or permissions changed.
- Two new connectors, Telegram and Discord, are opt-in: nothing loads them until an agent
  lists `algen_agent_tools.telegram` or `algen_agent_tools.discord` in `domain_plugins`. Both are
  part of the `all` extra and add no packages.
- The MCP overlay toolkit now uses the shared `connect()` and `compare()` steps; refusals of
  missing or changed tools are unchanged.

## [0.2.0a2] - 2026-10-27

### Added

- Outlook (`algen_agent_tools.outlook`, extra `outlook`): one Microsoft 365 mailbox through
  Microsoft Graph, with delegated (refresh token) or application access.
  `outlook.list_messages` (read; a folder, Outlook search or unread only, opaque page tokens
  that always rebuild the request for the configured mailbox), `outlook.get_message` (read),
  `outlook.send_email` (external, one attempt; attachments inline up to 3 MB) and, with an
  `artifact_sink`, `outlook.get_attachment` (read; file attachments only).
- `algen_agent_tools.mail.add_mailbox_read_tools`: the `get_message` and `get_attachment` tools
  Gmail and Outlook share. `attachments.id_field` and `add_get_attachment_tool(id_pattern=...)`
  let a provider accept its own id characters.
- `_core.auth.OAuthClientCredentials` (internal): app-only tokens, sharing the refresh-token
  flow's cache.

- `gmail.get_message` (read): one message with its headers, `cc`, and its body as plain text
  (the text part, or the HTML part without markup, scripts or styles), cut to `max_chars` with
  `body_truncated` set when cut.
- `gmail.get_attachment` (read), offered when `gmail_toolkit` gets an `artifact_sink`: saves one
  attachment as an artifact within `attachment_policy` and returns only its reference. The file
  name and type come from the message; the attachment is named by its stable `part_id` (or its
  attachment id).
- `InboundEmailDetail` and `html_to_text` in `algen_agent_tools.mail`. `FakeMailbox` serves
  `get_message` and, from `files=`, `get_attachment`.
- `algen_agent_tools.attachments.ID_PATTERN`: provider ids in tool inputs cannot contain a slash
  or be `.` or `..`.
- Documents (`algen_agent_tools.documents`, extra `documents`): `documents_toolkit(artifact_loader=...)`
  offers `documents.extract_text` (read), the readable text of a PDF, DOCX, text or Markdown
  artifact with the Runtime's document inspection findings (hidden text, instructions aimed at
  the model, risky DOCX packaging) and a `pass`/`review`/`quarantine` disposition. Hidden text is
  never returned, and a quarantined document's text is withheld unless
  `quarantine="return"`. Built in code, as it needs the deployment's artifact loader; no plugin
  yet. The `documents` extra installs `algen-agent-runtime[documents]` (PyMuPDF, AGPL-3.0) for
  PDFs and is part of `all`.
- AGENTS.md: `algen_agent_runtime.security.documents` joins the Runtime API connectors may use.

### Changed

- `gmail.list_messages` results give each attachment a `part_id` (additive output field).
- Gmail URL-encodes message and attachment ids into a single path segment.
- The Gmail toolkit declares `artifact_sink` and `attachment_policy` among its code-only options
  in the connector catalog.

### Upgrade notes

- Agents that enable every Gmail tool by listing them should add `gmail.get_message` to
  `enabled_tools` to use it; it needs only `gmail.read` and the `gmail.readonly` scope.
- No existing tool's name, input schema, side effect or permissions changed. The one output
  change, `part_id` on Gmail list results, is additive.
- `gmail.get_attachment` and `outlook.get_attachment` appear only when the toolkit is built in
  code with an `artifact_sink`; plugin-only deployments see no new write path.
- The `all` extra now includes `documents`, which installs PyMuPDF (AGPL-3.0). Deployments that
  cannot ship AGPL code should install the extras they use instead of `all`.

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
