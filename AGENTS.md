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
  `.mcp`, `.orchestration.describe`, `.exceptions` and the redaction helpers in `.security`
  (`redact_held`, `redact_error_text`, `redact_secrets`, `redact_pii`). Document inspection lives here
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

## 6. Building on algen-agent-runtime and algen-agent-tools

**The rule.** Use the Runtime's capabilities wherever they exist, and keep only connector code
here. Before writing a mechanism (redaction, retries, timeouts, caching, concurrency limits,
recording what was sent), check whether the Runtime already does it.

**Where to look.** In the Runtime repository (or its tags, `git show v0.2.0b5:<path>`):
`CHANGELOG.md` (each release's upgrade notes and "You can now remove"), `docs/tool-authoring.md`
(not attempted, failures as data, `volatile`, `cache_context`, credentials, timeouts and
cancellation), `docs/operations.md` "Cancellation", and the `algen-agent-connector` skill
(`algen-agent-runtime skills install --skill algen-agent-connector`). This package's own guide is
`docs/writing-a-connector.md`, "Using what the Runtime already does".

**Upgrade checklist (each Runtime release).**
1. Read the Runtime CHANGELOG from the release the floor names up to the new one.
2. Replace any copy of what the Runtime now provides; say what and since which version in the
   CHANGELOG.
3. Decide the floor: raise it only for an API or a fix the connectors need, and record why in
   `docs/compatibility.md`, the README and the docs page.
4. Install the new Runtime in a fresh venv (`pip install --isolated`) and run `make check`,
   `python -I scripts/smoke_describe.py`, and the PostgreSQL and MySQL `integration` tests against
   throwaway containers.
5. Regenerate `connectors.json` (`python -m algen_agent_tools catalog --write`).

**Kept on purpose.**
- `_core/errors.py` (`not_attempted`, `rejected`, `EgressRefused`): marks a connector's own error
  type as the Runtime's `ToolNotAttemptedError`, keeping the type callers catch.
- `_core/http.py` (`HostLockedClient`): pins each connector to its configured hosts and bounds
  responses. The Runtime's `OutboundNetwork` guards tools that take a URL; connectors take none.
- `_core/threads.py` (`run_exclusive`): a client shared by several tools; the Runtime's
  `max_concurrency` is per tool, and its thread pool covers only synchronous handlers.
- The MCP overlay's own call path (`mcp/session.py`): it pins reviewed schemas and refuses drift,
  which the Runtime's MCP discovery does not; it masks echoed credentials with the Runtime's
  `redact_held` and keeps the same per-call timeout as the Runtime's MCP tools.
- `documents.inspection.redact_pii`: the extractors' own placeholders (`[EMAIL REDACTED]`, address
  and demographic lines), part of this package's document contract since it moved here from the
  Runtime in 0.2.0a7. It is not the guardrail's `algen_agent_runtime.security.redact_pii`.

**Pins.** `algen-agent-runtime>=0.2.0b5,<0.3` (and the same range for the `mcp` and `github`
extras), since 0.2.0a13. CI tests both ends of the range (`scripts/runtime_requirement.py`).
