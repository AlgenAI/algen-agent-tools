from algen_agent_tools._env import env_name
from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.catalog import ConnectorInfo, Egress, Setting
from algen_agent_tools.github.toolkit import TOKEN, github_toolkit

INFO = ConnectorInfo(
    connector="github",
    title="GitHub",
    summary=(
        "Read repositories, commits, issues and pull requests, and work on issues and pull "
        "requests, through GitHub's MCP server under a reviewed overlay."
    ),
    category="development",
    extra="github",
    settings=(
        Setting(
            env=env_name(TOKEN),
            description=(
                "A GitHub personal access token (fine-grained, limited to the repositories and "
                "permissions the agent needs)."
            ),
            secret=True,
        ),
    ),
    egress=(Egress(protocol="https", host="api.githubcopilot.com", port=443),),
)

GithubPlugin = toolkit_plugin(INFO, github_toolkit, capabilities=frozenset({"mcp"}))
