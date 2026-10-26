# Release process

Releases are built by GitHub Actions and published to PyPI with
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/). Maintainers never upload
artifacts from a workstation.

## One-time setup (before the first release)

1. **PyPI project and trusted publisher.** The name `algen-agent-tools` does not exist on PyPI
   until the first upload, so register a *pending* trusted publisher: on pypi.org, open
   **Your account → Publishing → Add a new pending publisher** and enter:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `algen-agent-tools` |
   | Owner | `AlgenAI` |
   | Repository name | `algen-agent-tools` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   The first successful publish creates the project; add the other maintainers as owners then.
   Optionally do the same on test.pypi.org for a dry run.
2. **GitHub environment.** In the repository, **Settings → Environments → New environment**
   named `pypi`, with required reviewers (the release maintainers) and deployment limited to
   protected branches and `v*` tags.
3. **Release token.** `auto-release.yml` creates the tag and GitHub Release with a fine-grained
   personal access token stored as the `RELEASE_TOKEN` secret (contents: read and write on this
   repository). A `GITHUB_TOKEN` cannot be used: GitHub does not let it trigger
   `release.yml`.
4. **Branch protection on `main`.** Require pull requests, the Quality, Security and DCO checks,
   CODEOWNERS review and signed-off commits; disallow force pushes.
5. **Security features.** Enable private vulnerability reporting, Dependabot alerts and secret
   scanning.

## Before a release

- The version in `src/algen_agent_tools/__init__.py` is the one to publish; `CHANGELOG.md` has a
  section `## [<version>] - <YYYY-MM-DD>` with changes, upgrade notes and known limitations.
- The supported Runtime range in `pyproject.toml` and `docs/compatibility.md` agree, and the
  Quality workflow's `runtime-range` job passed against both bounds.
- `main` is green on every required check, and dependency, CodeQL, Bandit and Gitleaks findings
  are reviewed.

## Publish

1. Merge a release pull request that sets the version and changelog date.
2. On the push to `main`, **Auto Release** reads the version and, if `v<version>` does not
   exist, creates the tag and a GitHub Release with the changelog section as notes (a/b/rc
   versions are marked pre-release). Merging a version bump is therefore a release action.
3. **Release** verifies the tag matches the version, runs lint, typing and tests, builds the wheel
   and sdist, writes and checks `SHA256SUMS`, inspects the artifacts, installs the wheel in a
   fresh virtualenv and describes every plugin it ships, then uploads the artifacts.
4. A reviewer approves the `pypi` environment; Trusted Publishing uploads exactly those
   artifacts.
5. Verify the published package:

   ```bash
   python -m venv /tmp/tools-check
   /tmp/tools-check/bin/pip install --pre "algen-agent-tools[all]"
   /tmp/tools-check/bin/python scripts/smoke_describe.py
   ```

To publish an existing tag again (for example after fixing the PyPI configuration), run the
**Release** workflow manually with that tag.

## Rollback and yanking

A published version is immutable: never reuse a version number or move a tag. If a release is
defective:

1. Yank it on PyPI (**Manage project → Releases → Options → Yank**) with the reason. Pinned
   installs keep working; resolvers stop choosing it.
2. Mark the GitHub Release as yanked in its notes.
3. For a vulnerability, publish a GitHub Security Advisory.
4. Ship a fixed version through the normal process, noting the yanked version in the changelog.
