# GitHub

Read repositories, commits, issues and pull requests, and work on issues and pull requests,
through **GitHub's hosted MCP server**, governed by a reviewed [MCP overlay](../mcp-overlays.md)
that ships with this package.

| | |
| --- | --- |
| Plugin | `algen_agent_tools.github` |
| Extra | `pip install "algen-agent-tools[github]"` (the Runtime's MCP client) |
| Environment | `ALGEN_TOOLS_GITHUB_TOKEN` |
| Server | `https://api.githubcopilot.com/mcp/` (the only host the connector reaches) |
| Owner | @saurav-samantray |

This is not a native connector: the tools are GitHub's, and the overlay decides which ones an
agent may call and how each is governed. Each tool's input schema is pinned to what the server
advertised when the overlay was reviewed (2026-10-27). If GitHub changes a tool, calls to it are
refused until the overlay is reviewed again.

## Tools

37 of the server's 46 tools. Permissions are `github.read` for reads and `github.write` for
everything else.

| Kind | Tools | Approval |
| --- | --- | --- |
| Read (22) | `get_me`, `get_file_contents`, `get_commit`, `list_commits`, `list_branches`, `list_tags`, `get_tag`, `list_releases`, `get_latest_release`, `get_release_by_tag`, `issue_read`, `list_issues`, `search_issues`, `list_issue_types`, `list_issue_fields`, `get_label`, `pull_request_read`, `list_pull_requests`, `search_pull_requests`, `search_code`, `search_repositories`, `search_commits` | agent policy |
| Write (10) | `add_issue_comment`, `update_issue_comment`, `issue_write`, `sub_issue_write`, `create_branch`, `create_pull_request`, `update_pull_request`, `update_pull_request_branch`, `add_reply_to_pull_request_comment`, `add_comment_to_pending_review` | agent policy |
| Write, approved every call (3) | `pull_request_review_write` (it can **approve** a pull request, which may satisfy branch protection), `push_files` and `create_or_update_file` (they **commit** to a branch) | always |
| Destructive (2) | `merge_pull_request`, `delete_file` | by default |

Tool names are `github.<tool>`. Descriptions are GitHub's.

**Not exposed.** These tools are not declared in the overlay, so agents never see them:

| Tool | Why it is excluded |
| --- | --- |
| `create_repository`, `fork_repository` | They create repositories. |
| `run_secret_scanning` | It uploads the raw content it is given, so it could carry data out, even though the server marks it read-only. |
| `ui_get` | It returns UI data for MCP Apps clients, not data for agents. |
| `request_copilot_review` | It starts a Copilot product feature. |
| `get_teams`, `get_team_members`, `list_repository_collaborators`, `search_users` | They are people-directory lookups, left out to limit personal data. |

## Token

Use a **fine-grained personal access token**, or a GitHub App user token, limited to the
repositories the agent works on. Grant only the permissions the tools you enable need. For
example, *Contents: read*, *Issues: read* and *Pull requests: read* cover the read tools, and you
add *write* only for the actions you allow. The token is the real boundary: the overlay limits
which tools an agent can call, and the token limits what those tools can reach.

The server can be disabled by an organisation's Copilot policy, so check that first if
connections are refused.

## Agent configuration

```yaml
agents:
  - name: issue-triage
    domain_plugins: [algen_agent_tools.github]
    enabled_tools: [github.list_issues, github.issue_read, github.search_issues, github.add_issue_comment]
    tool_permissions: [github.read, github.write]
security:
  trusted_plugin_prefixes: [algen_agent_tools]
```

Enable only the tools the agent needs. Issue and pull request text is written by outside
contributors, so treat anything it says as data, not as instructions. Keep write tools behind
approval when an agent reads untrusted content and acts in the same run.

## Changing the overlay

To declare fewer tools or change an approval rule, copy
`algen_agent_tools/github/github.overlay.yaml` into your project, edit the copy, and load it with
[`overlay_plugin`](../mcp-overlays.md#using-it). Check either copy against the live server with:

```bash
ALGEN_TOOLS_GITHUB_TOKEN=... python -m algen_agent_tools.mcp check github.overlay.yaml
```

A live test does the same for the shipped overlay:

```bash
ALGEN_TOOLS_LIVE=1 ALGEN_TOOLS_GITHUB_TOKEN=... pytest tests/integration/test_github_live.py
```

Listing tools needs a token but no repository access.
