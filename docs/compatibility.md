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
moved into this package in 0.2.0a7, so the `documents` extra installs PyMuPDF directly.

| algen-agent-tools | Supported algen-agent-runtime |
| --- | --- |
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
