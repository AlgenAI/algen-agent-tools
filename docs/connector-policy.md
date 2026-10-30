# Connector policy

This package is curated. A connector is accepted, maintained and retired by the rules below.

## Entry criteria

A new connector needs all of:

1. **Demand.** At least two projects need it. Name them in the proposal.
2. **No domain logic.** It speaks to a vendor or protocol, not to one product. Hiring, payroll,
   text-to-SQL and similar logic belong in a domain pack.
3. **An owner.** A named maintainer who reviews its changes and answers its security reports,
   listed in `.github/CODEOWNERS` and in the connector's docs page.
4. **A security review.** The checklist below, recorded in the pull request that adds it.
5. **MCP first.** If a well-maintained MCP server covers the need, propose a governance overlay
   ([`algen_agent_tools.mcp`](mcp-overlays.md)) instead. A native connector is justified only when the overlay
   cannot give a guarantee the agent needs, such as an idempotency key the server ignores or a
   typed action the server lacks.

Open a "Connector proposal" issue before writing code.

## Security review checklist

- [ ] Every action is narrow and typed; nothing passes free-form SQL, URLs, shell commands or
      vendor request bodies through.
- [ ] Each tool's preset matches what it does: reads are `read()`, anything that writes, sends or
      deletes is `write`, `external` or `destructive`, keyed and single-attempt.
- [ ] Destructive actions require approval by default.
- [ ] Credentials are read only through `env://` references, resolved when the client opens, and
      never logged, returned or put in error messages.
- [ ] Outbound hosts are fixed by configuration, not by tool arguments.
- [ ] Results are bounded (row counts, page sizes, byte limits) and carry no secrets.
- [ ] Errors are content-minimal: they name the failure type, not the payload.
- [ ] Optional dependencies are imported lazily, after secrets are resolved, with an actionable
      error when the extra is missing.
- [ ] The fake covers every tool, and live tests are opt-in.

## Versioning

The package follows [Semantic Versioning](https://semver.org/). During `0.x`, any release may
change a public contract, but only with a CHANGELOG entry and an upgrade note. A tool's name,
input schema, side effect or permission is part of its public contract: agents and Studio
reviews depend on them. The path to a frozen beta and to `1.0` is tracked in
[beta and GA criteria](ga-criteria.md).

## Deprecation

From the first release:

1. A connector, tool or public function is deprecated in a release that emits a
   `DeprecationWarning` on use and records the replacement in the CHANGELOG.
2. It keeps working for at least one further minor release (`0.x` → `0.(x+1)`).
3. It is removed in a later minor release, with an upgrade note.

A connector whose vendor API is shut down, or which loses its owner and finds no replacement
within one minor release, is deprecated the same way.
