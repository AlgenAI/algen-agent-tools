## Summary

Describe the problem and the outcome of this change.

## Verification

- [ ] `make check` passes (ruff format, ruff check, strict mypy, pytest).
- [ ] Tests pass offline, without credentials; any live test is marked `live`.
- [ ] Every changed toolkit passes `assert_toolkit_contract`.
- [ ] Connector docs, README table and CHANGELOG are updated when applicable.

## Risk and compatibility

- Security or privacy impact:
- Tool contract impact (names, input schemas, side effects, permissions):
- Dependency or supported Runtime range impact:
- Rollback plan:

## New connector only

- [ ] Linked to an accepted connector proposal issue.
- [ ] The security review checklist in `docs/connector-policy.md` is completed below.
- [ ] Owner added to `.github/CODEOWNERS` and the connector's docs page.

## Contribution certification

- [ ] Every commit includes a `Signed-off-by` line under the Developer Certificate of Origin.
