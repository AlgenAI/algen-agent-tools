# Runtime compatibility

`algen-agent-tools` is built on the Runtime's tool authoring API (`@tool`, presets, `Toolkit`),
declared plugins and `describe`, all introduced in `algen-agent-runtime` 0.1.0a11. From 0.1.0a2 it
needs 0.1.0a12, whose `pii` guardrail leaves fields declared `format: email` alone, so email tools
run under the default guardrails. From 0.2.0a6 it needs 0.1.0a18: 0.1.0a14's document
inspection reads XLSX, PPTX, HTML, CSV and TSV for `documents.extract_text`, and 0.1.0a18's
`cache_context` keys cached SQL results on the values `context_params` binds. From 0.2.0a7 it
needs 0.1.0a20: 0.1.0a19 records a tool's `PolicyDeniedError` or `ToolNotAttemptedError` as not
attempted, keeps declared `format: email` result fields out of `pii` redaction, and gives tools the
run's attachments (`runtime_attachment_loader`); 0.1.0a20 keeps a toolkit's configuration error
(which extra to install, which variable to set) when its client cannot open. Document inspection
moved into this package in 0.2.0a7, so the `documents` extra installs PyMuPDF directly. From
0.2.0a8 it needs 0.1.0a23 (unchanged in 0.2.0a9 to 0.2.0a12; 0.2.0a11 and later also run on 0.2): 0.1.0a22 and 0.1.0a23 fix two ways an approval-required workflow tool
step could run without its approval, and run synchronous tools in a worker thread within their
timeout.

From 0.2.0a13 it needs 0.2.0b5:

- The MCP overlay masks the credentials it sends when a server echoes one back, with the
  Runtime's `algen_agent_runtime.security.redact_held` (0.2.0b5), as the Runtime's own MCP tools
  do.
- Mail and messaging listings are `volatile`; the Runtime's tool-result cache honours that flag
  only from 0.2.0b2 (before, it stored and served such results).
- Workflow `tool:` steps that call these connectors work on PostgreSQL only from 0.2.0b5
  (migration 024). A cancelled call no longer leaves its ledger record `started` (0.2.0b5), and
  0.2.0b4 fixes approval decisions of `pending` or `expired` that ran gated tools.

Deployments still on Runtime 0.1.0a23 to 0.2.0b4 keep `algen-agent-tools==0.2.0a12`: with the
Runtime pinned there, pip chooses it by itself, and it was tested on 0.1.0a23, 0.2.0b1, 0.2.0b2
and 0.2.0b5.

| algen-agent-tools | Supported algen-agent-runtime |
| --- | --- |
| 0.2.0a14 | `>=0.2.0b5,<0.3` (tested on 0.2.0b5) |
| 0.2.0a13 | `>=0.2.0b5,<0.3` (tested on 0.2.0b5) |
| 0.2.0a12 | `>=0.1.0a23,<0.3` (tested on 0.1.0a23, 0.2.0b1, 0.2.0b2 and 0.2.0b5) |
| 0.2.0a11 | `>=0.1.0a23,<0.3` (tested on 0.1.0a23 and 0.2.0b1) |
| 0.2.0a10 | `>=0.1.0a23,<0.2` (tested on 0.1.0a23) |
| 0.2.0a9 | `>=0.1.0a23,<0.2` (tested on 0.1.0a23) |
| 0.2.0a8 | `>=0.1.0a23,<0.2` (tested on 0.1.0a23) |
| 0.2.0a7 | `>=0.1.0a20,<0.2` (tested on 0.1.0a20) |
| 0.2.0a6 | `>=0.1.0a18,<0.2` (tested on 0.1.0a18) |
| 0.2.0a5 | `>=0.1.0a12,<0.2` (tested on 0.1.0a12 and 0.1.0a13) |
| 0.2.0a4 | `>=0.1.0a12,<0.2` (tested on 0.1.0a12 and 0.1.0a13) |
| 0.2.0a3 | `>=0.1.0a12,<0.2` (tested on 0.1.0a12 and 0.1.0a13) |
| 0.2.0a2 | `>=0.1.0a12,<0.2` (tested on 0.1.0a12 and 0.1.0a13) |
| 0.2.0a1 | `>=0.1.0a12,<0.2` (tested on 0.1.0a12 and 0.1.0a13) |
| 0.1.0a2 | `>=0.1.0a12,<0.2` |
| 0.1.0a1 | `>=0.1.0a11,<0.2` |

## How the range is kept honest

- `pyproject.toml` pins the range; a release never widens it without a CI run against the new
  bound.
- CI runs the full suite twice: against the lowest supported Runtime (installed with
  `==` the lower bound) and against the highest release inside the range (installed with
  `--pre --upgrade`).
- A Runtime release that breaks a connector is fixed here, and the range's upper bound is
  lowered until it is.

## Plugin API

Every plugin in this package targets the Runtime's plugin core API version `1`
(`PluginManifest.core_api_version`). A Runtime that changes that version requires a new release
of this package.
