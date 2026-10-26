# Changelog

All notable changes to Algen Agent Tools are recorded here. The project follows
[Semantic Versioning](https://semver.org/); during the `0.x` series public contracts are
experimental, and every breaking change carries an upgrade note.

A tool's name, input schema, side effect and permission are part of the public contract.

## [Unreleased]

## [0.1.0a1] - Unreleased

### Added

- The package: `algen-agent-tools`, importable as `algen_agent_tools`, supporting
  `algen-agent-runtime>=0.1.0a11,<0.2`. Each connector is a Runtime `Toolkit` registered by a
  plugin in the `algen_agent_runtime.plugins` entry point group under the trusted prefix
  `algen_agent_tools`, with one optional extra per connector and an `all` extra.
- `algen_agent_tools.sample`: a dependency-free reference toolkit (`sample.echo`) used to check
  an install end to end and as the template for new connectors.
- `algen_agent_tools.testing`: `assert_toolkit_contract` and `toolkit_contract_problems` (the
  Runtime's tool contract plus naming, permission-prefix and no-passthrough rules),
  `describe_plugin` and `plugin_tools` (the Runtime inventory for one plugin, without calling a
  tool).
- CI: lint, strict typing and tests on Python 3.12 and 3.13; the suite against the lowest and
  highest supported Runtime; coverage; package build and a clean-install check that describes
  every installed plugin; DCO, CodeQL, pip-audit, Bandit, Gitleaks and dependency review.
- Project policies: connector entry criteria, security review checklist, versioning and
  deprecation policy (`docs/connector-policy.md`), contributor and agent guides, security policy
  and governance.
