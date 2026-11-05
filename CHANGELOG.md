# Changelog

All notable changes to Algen Agent Tools are recorded here. The project follows
[Semantic Versioning](https://semver.org/); during the `0.x` series public contracts are
experimental, and every breaking change carries an upgrade note.

A tool's name, input schema, side effect and permission are part of the public contract.

## [Unreleased]

### Added

- Outlook `.msg` files in `documents.extract_text` and `extractor_for` with the new `msg` extra
  (olefile, BSD) (#41): Tools reads the sender, recipients, date, subject, body and attachments
  from the OLE streams, rebuilds the email and inspects it as `.eml`, with the same limits;
  attached Outlook messages are read as attached emails (`MsgDocumentExtractor`,
  `msg_to_email`). An RTF-only body is the new coverage finding `document.body_unread`
  (`UnreadContent(kind="body")`). The GPL `extract-msg` package is not used.
- Quoted history (#42): `get_message(..., include="latest")` and `get_thread(..., include=...)`
  on Gmail, IMAP and Outlook return the sender's own text, without the history the reply quotes
  or its signature, and set the new `quoted_text_removed`. The rules are deterministic and
  conservative (attribution lines in six languages, Outlook header blocks, trailing `>` runs,
  `-- ` signatures); forwarded messages and inline replies are kept
  (`algen_agent_tools.mail.quotes.latest_text`).
- Gmail and IMAP message bodies join every inline text part (at most five) instead of only the
  first, so text a client split around inline images is not lost.
- Documents (#45): `extractor_for(filename, data=...)` and `documents.extract_text` go by the
  content's signature when it names another type than the file name (`sniff_suffix`): a
  misnamed JPEG, a renamed `.docx`, a PDF named `.txt`. Text, CSV, TSV, Markdown and HTML fall
  back from UTF-8 to Windows-1252 (and read UTF-16 with a byte-order mark) instead of replacing
  accented letters (`decode_text`). A PDF over the page limit is read up to it with the new
  coverage finding `document.pages_skipped` (`PdfDocumentExtractor(on_excess="truncate")`).
- OpenDocument text, spreadsheets and slides (ODT, ODS, ODP) in `documents.extract_text` and
  `extractor_for` (#45), standard library only (`OdfDocumentExtractor`), with the Office rules:
  hidden and tiny text, hidden sheets, rows, columns and slides are left out and reported, a
  sheet is cut at 5,000 rows, comments and notes are skipped, Basic macros and remote objects
  are findings, and encrypted files are refused.

### Changed

- A PDF over 50 pages is no longer refused by `documents.extract_text`: its first 50 pages are
  read and the result is `partial` and `truncated`.
- `get_thread` now defaults to `include="latest"`: each message's body is the sender's own
  text. Pass `include="full"` for whole bodies, as 0.2.0a9 returned.

## [0.2.0a9] - 2026-11-05

Mail reads: attachments in one call, whole threads, `.eml` files, IMAP search and folders.
Requires `algen-agent-runtime>=0.1.0a23,<0.2`, as 0.2.0a8 did.

### Upgrade notes

- The mail plugins (Gmail, IMAP, Outlook) describe new read tools: `get_thread` on all three
  and `list_folders` on IMAP. An agent that enables tools by name is unaffected; one that
  enables a whole plugin now also gets these read tools.
- On a plain IMAP server `thread_id` is no longer empty (see Changed).
- The "unsupported document type" message now lists EML; match on the `DocumentRefused` type,
  not its text.

### Added

- `.eml` email files in `documents.extract_text` and `extractor_for` (#41): the headers and body,
  then each attachment read by the extractor for its type, with attached emails read at most two
  levels deep and at most 20 attachments per message (`EmailDocumentExtractor`). A finding in an
  attachment quarantines the message and names the attachment in the new `part` field. Results
  gain `attachments` (`ExtractedDocument.parts`), and attachments that are skipped or unreadable
  are new coverage findings, `document.attachment_skipped` and `document.attachment_unread`
  (`UnreadContent(kind="attachment")`). Standard library only; `.msg` is not read yet.
- `<mailbox>.read_attachment(message_id, attachment_id)` (read) on the Gmail, IMAP and Outlook
  toolkits built with `documents=DocumentReader()` (#44): one call downloads an attachment,
  checks it against `attachment_policy` and returns its inspected text (`AttachmentText`, the
  `documents.extract_text` result plus `artifact`), so the file never passes through the model.
  A quarantined attachment's text is withheld; with an `artifact_sink` the file is also saved.
  `DocumentReader` is the read behind `documents.extract_text`, now public.
- `<mailbox>.get_thread(thread_id, max_messages=20, max_chars_per_message=4000)` (read) on the
  Gmail, IMAP and Outlook toolkits and plugins (#42): a thread's messages oldest first, at most
  50, keeping the newest (`EmailThread`, with `truncated`). Gmail uses `threads.get`, Outlook
  the `conversationId`, IMAP on Gmail `X-GM-THRID`, and plain IMAP the References chain across
  the configured folders. `get_message` results gain `message_id_header`, `in_reply_to` and
  `references`. Quoted history is not split off yet.
- IMAP (#43): `imap.list_messages` takes `text_contains` (IMAP `TEXT`, headers and body) and,
  on Gmail servers only, `gmail_query` (`X-GM-RAW`). The folders setting accepts special-use
  names (`\Sent`, `\All`, `\Archive`, `\Drafts`, `\Junk`, `\Trash`, `\Flagged`) resolved
  with `LIST` when the connection opens, and the new `imap.list_folders` (read) lists the
  readable folders with their special use. With `\All` configured on Gmail, a message that left
  the inbox is still found by its id. The Gmail page documents `in:sent` and `in:anywhere`.

### Changed

- On a plain (non-Gmail) IMAP server, `thread_id` was always empty; it is now `r.<base64url>`
  of the thread's first Message-ID, which `imap.get_thread` takes. The IMAP connector fetches
  the Message-ID, In-Reply-To and References headers with each message's metadata.

## [0.2.0a8] - 2026-11-05

Needs `algen-agent-runtime>=0.1.0a23`.

### Upgrade notes

- The supported Runtime range is now `>=0.1.0a23,<0.2`. 0.1.0a22 and 0.1.0a23 fix two ways an
  approval-required workflow tool step could run without its approval (security fixes), run
  synchronous tools in a worker thread within their timeout, and keep a timed-out synchronous
  tool's concurrency slot until its thread ends. 0.1.0a21 retries a model's malformed tool call
  instead of failing the run. Every Tools tool is `async` except the sample `echo`, which is
  thread-safe, so no tool changes are needed.

- Content that was not read no longer makes a document `review`
  ([#40](https://github.com/AlgenAI/algen-agent-tools/issues/40)). `document.ocr_required`,
  `document.images_excluded`, `document.ocr_no_text` and `document.truncated` are now coverage
  findings, and the disposition comes from security findings only. A scanned PDF, an image-only
  DOCX, a cover note over a scan or an XLSX past the row limit is now `pass`, with `coverage`
  `none` or `partial`, where it was `review`. The finding kinds and severities are unchanged.
  Code that treated `review` as "not fully read" checks `coverage != "full"` (or `needs_ocr`)
  instead. `pass` with `coverage="none"` means nothing was read, not that the content is safe.

### Added

- `coverage` (`full`, `partial` or `none`), `unread` (`UnreadContent(kind, page)`) and
  `needs_ocr` on `ExtractedDocument`; `category` (`security` or `coverage`) on
  `DocumentSecurityFinding`. `documents.extract_text` results gain `coverage`, `needs_ocr` and
  each finding's `category` (#40).
- An OCR hook for document inspection
  ([#39](https://github.com/AlgenAI/algen-agent-tools/issues/39), step 1). A host passes a
  `TextRecognizer` (any object with `recognize(image, *, media_type) -> RecognizedText`) as
  `extractor_for(filename, ocr=..., ocr_max_pages=3)` or `documents_toolkit(ocr=...)`. With one,
  PNG, JPEG, WebP, GIF, BMP and TIFF images are read by the new `ImageDocumentExtractor` (every
  frame of a GIF or TIFF counts toward its pixel limit, at most 64 frames), and PDF
  pages that have images and almost no text are rendered with PyMuPDF (DPI and pixel caps) and
  recognized, up to `ocr_max_pages` per PDF. Recognized text is a `DocumentSpan` with
  `source="ocr"` (and the recognizer's `confidence`) and passes the same prompt-injection checks,
  so an instruction in a scan quarantines. OCRed pages no longer report
  `document.ocr_required`; scanned pages past the limit report it per page, and an image or page
  with no recognized text reports `document.ocr_no_text` (low). Without a recognizer, behaviour
  is unchanged: images are refused and nothing is rendered.
- The `ocr` extra (pytesseract and Pillow) with `TesseractRecognizer(languages=("eng",),
  timeout=30, max_frames=64)`, a recognizer backed by the Tesseract program, which the host
  installs (#39, step 2). It is never the default. Building one fails, naming the fix, when the
  extra, the program or a language is missing. The `all` extra includes `ocr`.
- `DocumentSpan.source` (`"text"` or `"ocr"`, default `"text"`) and `DocumentSpan.confidence`.
- With a recognizer, `documents.extract_text`'s description says images and scanned pages are
  read by OCR. Without one it is unchanged.

### Known limitations

- Images inside DOCX and PPTX files are not recognized; they still report
  `document.ocr_required` or `document.images_excluded`.
- A recognizer still running when the tool's 120 s timeout fires keeps running in its worker
  thread until it returns; `TesseractRecognizer(timeout=...)` bounds each frame.
- A recognized PDF page keeps the little text it had (a stamp or a page number), so that text
  can appear twice.
- The `documents` extra stays on PyMuPDF 1.x: 2.x also installs onnxruntime and layout packages
  that declare no license.

## [0.2.0a7] - 2026-10-31

Needs `algen-agent-runtime>=0.1.0a20`.

### Upgrade notes

- The supported Runtime range is now `>=0.1.0a20,<0.2`. 0.1.0a19 treats a tool's
  `PolicyDeniedError` as not attempted, keeps declared `format: email` result fields out of `pii`
  redaction and gives tools the run's attachments; 0.1.0a20 keeps a toolkit's configuration
  error message (0.1.0a19 replaced it with the error's type).
- Document inspection moved into this package (`algen_agent_tools.documents.inspection`,
  re-exported from `algen_agent_tools.documents`). Code importing
  `algen_agent_runtime.security.documents` (deprecated in Runtime 0.1.0a19) switches to
  `from algen_agent_tools.documents import extractor_for, ExtractedDocument, ...`. The
  `documents` extra now installs PyMuPDF directly instead of `algen-agent-runtime[documents]`.
- DOCX and PPTX documents with text and embedded images report `document.images_excluded`
  (low), so `documents.extract_text` returns `review` for them instead of `pass`.
- Mail tool results gain `sender_address`, `sender_name`, `recipient_addresses` and
  `cc_addresses`. The existing display-string fields are unchanged.
- A connector's error for a refusal before the send (see Fixed) is now also a
  `ToolNotAttemptedError`; its type and message are unchanged, so existing `except` clauses still
  catch it.

### Added

- `algen_agent_tools.documents.inspection`: the PDF, DOCX, XLSX, PPTX, HTML, CSV, TSV, text and
  Markdown extractors and their contracts, moved from the Runtime with its tests
  ([#36](https://github.com/AlgenAI/algen-agent-tools/issues/36)).
- `algen_agent_tools.attachments.runtime_attachment_loader`: an artifact loader over the run's own
  attachments (`RunRequest.attachments`), so `documents.extract_text` reads files attached to the
  run without host wiring. It refuses any artifact not attached to the run.
- Structured address fields on mail results (Gmail, Outlook, IMAP), declared `format: email`, so
  under the default `pii` guardrail an agent can reply to the sender it read
  ([#35](https://github.com/AlgenAI/algen-agent-tools/issues/35) item 2).
- `on_refresh_token=` on `GmailMailboxClient` and `OutlookMailboxClient` (`on_rotate` on
  `OAuthRefresh`) to store a rotated refresh token.

### Fixed

- Refusals before anything is sent are recorded as not attempted, not "may have happened", so a
  retry runs instead of asking for manual reconciliation (#35 item 1): a token that could not be
  fetched, a request to an unconfigured host, an attachment policy refusal, a missing artifact
  loader or attachments over the size limit (Outlook, Gmail, SMTP), invalid MCP tool arguments, a
  too-large webhook payload, and a definite rejection by the provider (a 4xx other than 408 from
  Graph, Gmail, Telegram, Discord or a webhook receiver; an SMTP connection, login, sender,
  recipient or data refusal). Timeouts, dropped connections and 5xx stay indeterminate.
- An MCP overlay session opened in one task and closed from another (a workflow node, then the
  container) no longer fails with "exit cancel scope in a different task" (#35 item 3).
- `OAuthRefresh` uses a rotated refresh token for the next refresh instead of the one it was
  built with, which providers that rotate tokens had invalidated (#35 item 4).

### Known limitations

- An MCP tool result marked as an error stays indeterminate: the server reported it after
  receiving the call.
- A rotated refresh token is kept in memory unless the host passes `on_refresh_token`.

## [0.2.0a6] - 2026-10-30

### Added

- `context_params` on `SqlToolkit.query`, so on the PostgreSQL, MySQL and SQLite toolkits
  ([#30](https://github.com/AlgenAI/algen-agent-tools/issues/30)): a declared query binds the
  call's `tenant_id`, `user_id`, `run_id` or `step_id` from its `ToolContext`, after the
  `params` fields and before the idempotency key, to keep a query to the caller's own rows. The
  values are not tool input, so the model cannot choose them; unknown or repeated names, and a
  `params` field whose name or alias shadows one, are refused when the query is declared. New
  `algen_agent_tools.sql.ContextValue` and `CONTEXT_VALUES`. Queries without `context_params`
  bind exactly as before. See docs/connectors/postgres.md#the-callers-rows.
  The tool declares the bound values as its `cache_context`
  ([algen-agent-runtime#70](https://github.com/AlgenAI/algen-agent-runtime/issues/70)), so the
  Runtime's tool-result cache keys each result on them and never serves one caller's rows to
  another, at any cache scope. An end-to-end test runs this through the Runtime's executor.
- `documents.extract_text` reads Excel (`.xlsx`), PowerPoint (`.pptx`), HTML (`.html`, `.htm`),
  CSV (`.csv`) and TSV (`.tsv`) documents, as well as PDF, DOCX, text and Markdown
  ([#28](https://github.com/AlgenAI/algen-agent-tools/issues/28)). Parsing and inspection are
  the Runtime's (`algen_agent_runtime.security.documents`, 0.1.0a14), with the same guarantees
  as DOCX: hidden sheets, rows, columns, slides, shapes and HTML elements are left out of the
  text and reported as `hidden_text.*` findings; Office packages get the zip-bomb, DTD, macro
  and remote-content checks; visible text goes through the prompt-injection check. The new
  formats need no extra. The tool's description lists them.

### Changed

- `DocumentText.truncated` is also true when the Runtime reports a `document.truncated`
  finding, for example an XLSX sheet longer than 5,000 rows, so the model knows the text is
  incomplete. Before, it only said the text was cut to `max_chars`.
- The supported Runtime range is `algen-agent-runtime>=0.1.0a18,<0.2` (was `>=0.1.0a12`), for
  the package and its `documents`, `github` and `mcp` extras: 0.1.0a14 for the document
  formats, 0.1.0a18 for `cache_context`. The connector catalog's
  `runtime_requirement` is regenerated.

### Upgrade notes

- Upgrade `algen-agent-runtime` to 0.1.0a18 or later.
- Don't scope the tool-result cache to `session`: on Runtime 0.1.0a18 it fails every cacheable
  tool call ([algen-agent-runtime#79](https://github.com/AlgenAI/algen-agent-runtime/issues/79)).
- `documents.extract_text` accepts files it used to refuse with "only PDF, DOCX, TXT, and
  Markdown documents are supported"; an agent policy that relied on that refusal to keep
  spreadsheets, decks or web pages away from the model must now filter by file name or media
  type itself. Its name, input schema, side effect (read) and permission (`documents.read`) are
  unchanged.

## [0.2.0a5] - 2026-10-28

### Added

- IMAP (`algen_agent_tools.imap`, extra `imap`, no packages: the standard library's `imaplib`),
  for mailboxes read with an app password ([#26](https://github.com/AlgenAI/algen-agent-tools/issues/26)).
  The tools are `imap.list_messages` and `imap.get_message` (read), plus
  `imap.get_attachment` (read) when the toolkit is built with an artifact sink. They return the
  shared `InboundEmailSummary` / `InboundEmailDetail` contracts. The connector is read-only:
  folders open with `EXAMINE`, bodies are fetched with `BODY.PEEK`, and nothing is marked as
  seen. Listing takes typed filters (`from_address`, `subject`, `since`, `before`,
  `unread_only`) rather than raw IMAP search. Only the folders in `ALGEN_TOOLS_IMAP_FOLDERS`
  (default `INBOX`) can be read. On Gmail, message and thread ids are Gmail's own (the same ids
  the Gmail API returns); elsewhere they carry the folder's UIDVALIDITY, so a stale id is an
  error rather than another message. `sender_auth` trusts the topmost Authentication-Results
  header from `mx.google.com` on Gmail, or from `ALGEN_TOOLS_IMAP_AUTHSERV_ID`. Bodies and
  attachments are fetched by part, within byte limits. TLS is always on and the certificate
  is verified. `FakeImapServer` answers as `imaplib` does, for offline tests. Part of `all`.
- The catalog's `Egress.protocol` adds `imap`.
- `InboundEmailSummary.sender_auth` (`pass`, `fail` or `unknown`, default `unknown`): the
  receiving server's SPF/DKIM/DMARC verdict on the sender. Gmail fills it from the topmost
  `Authentication-Results` header it stamped itself (`mx.google.com`), and IMAP does the same
  (above); Outlook leaves it `unknown`. `algen_agent_tools.mail.authentication_verdict(header, trusted_server)` and
  `topmost_verdict(headers, trusted_server)` expose the rule for other mailbox readers.
  `gmail.list_messages` and `gmail.get_message` gain the field in their results; inputs, side
  effects and permissions are unchanged. The connector catalog is regenerated.

### Upgrade notes

- No existing tool's name, input schema, side effect or permissions changed.
- `gmail.list_messages` and `gmail.get_message` results gain `sender_auth`. It is an additive
  field, but a consumer that validates results against a closed schema must accept it.
- The IMAP connector is opt-in and loads only when an agent lists its plugin. The `all` extra
  includes it, at no extra cost in packages.
- The connector catalog adds the `imap` egress protocol. Hosts that validate the catalog against
  a fixed list of values should accept it.

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
