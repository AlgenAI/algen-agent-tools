# Runtime compatibility

`algen-agent-tools` is built on the Runtime's tool authoring API (`@tool`, presets, `Toolkit`),
declared plugins and `describe`, all introduced in `algen-agent-runtime` 0.1.0a11. From 0.1.0a2 it
needs 0.1.0a12, whose `pii` guardrail leaves fields declared `format: email` alone, so email tools
run under the default guardrails.

| algen-agent-tools | Supported algen-agent-runtime |
| --- | --- |
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
