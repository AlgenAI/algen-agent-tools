# Governance

## Stewardship

Algen Agent Tools is stewarded by AlgenAI, alongside Algen Agent Runtime and Algen Agent Studio.
Vendor names used to identify connectors remain their owners' trademarks; their use here does
not imply endorsement.

The authoritative maintainer list is [MAINTAINERS.md](MAINTAINERS.md), mirrored by the protected
CODEOWNERS configuration. Each connector also has a named owner (see its docs page).

## Decisions

Routine changes are decided through reviewed pull requests. Adding or removing a connector,
changing a tool's public contract, the supported Runtime range, security boundaries, licensing,
governance or release policy requires a documented proposal (an issue) and approval from all
available maintainers. When only one maintainer is active, these changes must pass protected
checks and carry a clearly recorded rationale before merge.

## Roles

- Contributors submit issues, reviews, documentation and code under the DCO.
- Connector owners review changes to their connector and triage its security reports.
- Maintainers triage work, review changes, enforce the [connector policy](docs/connector-policy.md)
  and nominate new maintainers based on sustained, trusted contributions.
- Release maintainers approve versions, artifacts, changelogs, publication, yanks and security
  patches.

When two or more maintainers are active, no person may solely author and approve a
security-sensitive release change. During single-maintainer bootstrap, protected CI and the
manually approved release environment remain mandatory.

## Releases and succession

Releases use semantic versioning, immutable artifacts, protected environments and PyPI Trusted
Publishing. A release requires a passing protected branch, an approved changelog and artifact
verification. If a release maintainer becomes inactive, the remaining maintainers nominate a
successor and update CODEOWNERS, protected environments, PyPI ownership and security access
before the next release.
