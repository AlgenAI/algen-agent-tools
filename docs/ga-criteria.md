# Beta and general availability (1.0) criteria

Owner: AlgenAI Maintainers  
Status: Active tracking — pre-release (`0.2.0a6`)  
Last reviewed: 2026-10-30

`1.0.0` is a promise, not a connector count: a tool's name, input schema, side effect and
permission stay compatible across `1.x`, upgrades are documented and safe, and every `stable`
connector has been proven against its real service. This checklist tracks what must be true
before that promise is made, and before the beta that freezes the contracts.

The package is built on Algen Agent Runtime's public API (`tools.authoring`, `tools.contracts`,
`tools.testing`, `plugins.sdk`, `mcp`, `security.documents`), so it cannot promise more than the
Runtime does. Its milestones follow the Runtime's
[GA criteria](https://github.com/AlgenAI/algen-agent-runtime/blob/main/docs/ga-criteria.md), one
step behind.

## Release path

| Release | Purpose | Exit condition |
| --- | --- | --- |
| `0.2.0aN` | New connectors and contract changes, each with a CHANGELOG entry and upgrade note | Every section under "Beta" is checked |
| `0.3.0b1` … `0.3.0bN` | Beta: public surface frozen, experimental interfaces marked, on Runtime `0.2.0b1` or later | Every section below except "Stability evidence" and "Runtime 1.0" is checked |
| `0.3.0` | Supported pre-1.0 release | No breaking change for the stability window |
| `1.0.0rc1` … | Release candidates, on Runtime `1.0.0rc` or later | Only fixes; the support and deprecation policy is published |
| `1.0.0` | General availability | Every box below is checked |

A breaking change after `0.3.0b1` restarts the stability window. Version numbers here are a plan;
the release that enters beta may be named differently, but it is a new minor with a `b` suffix.

## Beta: the contracts are frozen

### Runtime

- [ ] The Runtime has released `0.2.0b1` (its public API candidate is frozen).
- [ ] The supported range covers it (today `>=0.1.0a18,<0.2` excludes it), and the
      `runtime-range` CI job passes against both bounds.
- [ ] Nothing here imports a Runtime interface the Runtime marks experimental or deprecated.

### Public surface

The catalog snapshot (`connectors.json`, checked by `tests/contract/test_catalog.py`) already fails
CI when a tool's name, schema, side effect or permission changes without a regenerated catalog.
The rest of the surface is not yet pinned.

- [ ] An API stability page lists the public surface:
  - every tool contract (the catalog);
  - the names each connector module exports (`__all__`);
  - the catalog schema (`catalog_version`), plugin names, setting names (`ALGEN_TOOLS_*`) and
    extra names;
  - the CLI: `python -m algen_agent_tools catalog` and `python -m algen_agent_tools.mcp
    snapshot|check`;
  - the testing helpers (`assert_toolkit_contract`, `describe_plugin`, `plugin_tools`) and every
    connector's fake.
- [ ] Extension points are marked stable or experimental in code and docs: `SqlToolkit` and
      `Dialect`, `MessagingPlatform`, `MessagingProvider` and `messaging_toolkit`,
      `add_mailbox_read_tools`, `add_get_attachment_tool`, and the MCP overlay format.
- [ ] Modules under `_core` and anything documented as internal stay out of `__all__`.
- [ ] A CI check fails on an unannounced change to module exports, as the catalog check does for
      tool contracts.

### Connectors

Each connector declares its own `maturity` (`alpha`, `beta`, `stable`, `deprecated`) in the
catalog, so a beta package can still ship alpha connectors. A connector becomes `beta` when:

- [ ] it has a named owner in its docs page and `.github/CODEOWNERS`;
- [ ] its security review (the checklist in [connector policy](connector-policy.md)) is recorded
      in a pull request;
- [ ] its fake covers every tool, and its unit tests run offline;
- [ ] a live or integration test against the real service passed before the release that
      promotes it.

| Connector | Live or integration evidence today | Gap for beta |
| --- | --- | --- |
| PostgreSQL | `PostgreSQL integration` CI job | none |
| MySQL / MariaDB | `MySQL integration` CI job (MySQL 8.4) | MariaDB is not in CI |
| SQLite | unit tests use real database files | none |
| SMTP | `test_mail_live.py` sends one message | none |
| Gmail | `test_mail_live.py` sends and lists | `get_message` and `get_attachment` have no live test |
| IMAP | `test_imap_live.py` | none |
| GitHub | `test_github_live.py` checks the overlay for drift | no live call of a tool |
| Documents | unit tests parse real files through the Runtime | none |
| Outlook | none | a live test (delegated and application access) |
| Telegram | none | a live test with a test bot |
| Discord | none | a live test with a test bot and server |
| Webhook | none | an integration test against a local receiver, signature included |
| Sample | not applicable | none |

### Testing

- [ ] Executor-level tests (agent runs through the Runtime's real executor, as
      `tests/end_to_end/` does for SQL caching) cover each write or send path, each attachment
      path, and a guardrail refusal.
- [ ] The SQL placeholder checks (PostgreSQL `$n`, MySQL `%s`, SQLite `?`) have property-based or
      fuzz tests, including quoted strings and comments.
- [ ] Known Runtime defects that affect connectors are fixed or documented with a workaround:
      [algen-agent-runtime#79](https://github.com/AlgenAI/algen-agent-runtime/issues/79)
      (`session` cache scope fails every cacheable tool call).

### Adoption

- [ ] Algen Agent Studio ships the latest `0.2` alpha (it pins `0.2.0a1` on Runtime `0.1.0a17`
      today) and runs it in production for about two weeks, with the domain packs that use these
      connectors.
- [ ] The last two or three alphas introduced no new deprecations and no breaking contract
      change.

## Supported release (0.3.0)

- [ ] The beta line had no breaking change for its stability window.
- [ ] Only the latest supported minor gets fixes, as `SECURITY.md` says; the table there names it.

## General availability (1.0.0)

### Runtime 1.0

- [ ] The Runtime has released `1.0.0`, and the supported range is `>=1.0,<2`.

### Policies

- [ ] A long-term support and deprecation policy is published: how long a deprecated tool,
      connector or public function keeps working in `1.x` (the `0.x` rule in
      [connector policy](connector-policy.md#deprecation) is one minor release), which Python
      versions are supported, and how security fixes are backported.
- [ ] `SECURITY.md` lists the supported versions, and `SUPPORT.md` states what support `1.x` gets.
- [ ] Every connector that ships `stable` has been `beta` for at least one minor release.
- [ ] A connector whose vendor API changes faster than the stability window (for example an MCP
      overlay) states what "stable" covers: the overlay's declared tools and governance, not the
      server's own behaviour.

### Governance

- [ ] At least two active maintainers, so no person solely authors and approves a
      security-sensitive release change (see [governance](../GOVERNANCE.md)).
- [ ] Every `stable` connector has an owner, and no single person owns all of them.

### Release engineering

- [ ] Provenance attestations for every release artifact (today: PyPI Trusted Publishing and
      `SHA256SUMS`).
- [ ] An SBOM and a license report are published with each release.
- [ ] The licensing of the `all` extra is decided: it installs PyMuPDF (AGPL-3.0) through
      `documents`. Either `all` leaves `documents` out, or the docs and release notes say so
      prominently.
- [ ] Reproducible lock files for CI.

### Continuous evidence

- [ ] Live tests for every `stable` connector run on a schedule with dedicated test accounts, not
      only before a release.
- [ ] `python -m algen_agent_tools.mcp check` runs on a schedule for every shipped overlay, and
      drift opens an issue.

### Documentation

- [ ] Every connector page, the README and `docs/compatibility.md` are reviewed for `1.0`; stale
      version references are removed.
- [ ] An upgrade guide covers each `0.x` minor release up to `1.0`.
- [ ] Studio's Tools documentation page is synced for `1.0`.
- [ ] The package classifier is `Development Status :: 5 - Production/Stable`.

## Stability evidence

- [ ] No breaking change for at least four to six weeks on the supported pre-1.0 line.
- [ ] Studio and the domain packs run the same release in production through that window.
- [ ] No open critical or high security issue, and no open defect that sends, writes or reads
      data outside a connector's declared boundary.
