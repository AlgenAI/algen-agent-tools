# Agent Instructions for Algen Agent Tools

When writing code, tests, documentation, or Git commits in `algen-agent-tools`, follow these
mandatory rules. They apply to people too.

## 1. Developer Certificate of Origin (DCO) Signoff
- Every commit must have a `Signed-off-by:` trailer matching the author's git name and email.
- Always use `git commit -s`. Rebase unsigned commits with `git rebase --signoff <base-sha>`.

## 2. What belongs here
- Generic connectors only: no hiring, payroll, HR, text-to-SQL or other product logic. Those go
  in domain packs.
- Never edit the Runtime from here. A connector needs only the Runtime's public API:
  `algen_agent_runtime.tools.authoring`, `.tools.contracts`, `.tools.testing`, `.plugins.sdk`,
  `.mcp` and `.orchestration.describe`. Document inspection lives here
  (`algen_agent_tools.documents.inspection`); do not import the Runtime's deprecated
  `.security` extractors.
- New connectors follow `docs/connector-policy.md` and `docs/writing-a-connector.md`.

## 3. Tool rules
- Narrow, typed actions; never a raw passthrough (free-form SQL, URLs, shell, vendor bodies).
- Governance comes from a Runtime preset (`read`, `write`, `external`, `destructive`,
  `long_running`). Do not hand-set side effect, idempotency or retries.
- Every toolkit passes `algen_agent_tools.testing.assert_toolkit_contract`.
- A tool's name, input schema, side effect and permission are public contract: changing them
  needs a CHANGELOG entry and an upgrade note.

## 4. Optional dependencies and secrets
- One extra per connector. Import its package lazily inside the client opener, after resolving
  `env://` secrets, and raise a `ConfigurationError` that names the extra when it is missing.
- The base CI job installs only `.[dev]`. Tests that need an extra use
  `pytest.importorskip("<package>")`; test the missing-extra error with
  `monkeypatch.setitem(sys.modules, "<package>", None)`.
- Secrets are `env://` references only. Never log, return or embed a secret in an error.
- Never commit credentials. Gitleaks runs in CI.

## 5. Tests and checks
- Each connector has a fake; unit tests never touch the network.
- Live tests are marked `live` and run only with `ALGEN_TOOLS_LIVE=1`.
- Before you finish: `make check` (ruff format, ruff check, mypy strict, pytest).
