# Contributing to Algen Agent Tools

Thank you for helping improve Algen Agent Tools. Contributions must keep connectors generic,
narrow and governed, keep tests offline by default, and keep every connector's dependencies
optional.

## Before opening a change

- Search existing issues and pull requests to avoid duplicate work.
- A new connector needs an accepted "Connector proposal" issue first; see the
  [connector policy](docs/connector-policy.md).
- Use an issue before changing a tool's name, input schema, side effect or permission, or any
  public function.
- Report vulnerabilities privately through [SECURITY.md](SECURITY.md), never in a public issue.
- Keep credentials, customer data, proprietary prompts and internal endpoints out of the
  repository.

Small bug fixes, tests and documentation improvements can be submitted directly.

## Development setup

Python 3.12 and 3.13 are supported and tested.

```bash
git clone https://github.com/AlgenAI/algen-agent-tools.git
cd algen-agent-tools
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Run the checks CI runs:

```bash
make check     # ruff format --check, ruff check, mypy (strict), pytest
```

To work on a connector, install its extra too, for example `pip install -e '.[dev,postgres]'`.

## Tests

- `tests/unit`: offline behaviour against each connector's fake.
- `tests/contract`: every toolkit and plugin in the package against the connector contract and
  `describe`.
- `tests/integration`: tests against a local service container (`integration` marker) or a real
  vendor account (`live` marker, run only with `ALGEN_TOOLS_LIVE=1`).

Live tests must never run in untrusted pull-request contexts.

## Runtime compatibility

The package supports a range of `algen-agent-runtime` releases (see
[compatibility](docs/compatibility.md)). Use only the Runtime's public API, and do not widen the
range without running the suite against the new bound.

## Documentation and changelog

Update the connector's page under `docs/connectors/`, the README's connector table and
[CHANGELOG.md](CHANGELOG.md) for every user-visible change. Breaking changes need an upgrade
note.

## Pull requests

Keep pull requests focused and explain:

- the problem and intended outcome;
- security and privacy impact (complete the review checklist for a new connector);
- tool contract, dependency and Runtime-range compatibility;
- tests and manual verification performed.

All required checks and maintainer reviews must pass before merge.

## Developer Certificate of Origin

The project uses the [Developer Certificate of Origin 1.1](https://developercertificate.org/).
Sign off every commit:

```bash
git commit -s
```

CI rejects unsigned pull-request commits. To sign earlier commits:

```bash
git rebase --signoff <base-branch-or-sha>
git push --force-with-lease origin <branch-name>
```

## Conduct and support

Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Support expectations are
in [SUPPORT.md](SUPPORT.md).
