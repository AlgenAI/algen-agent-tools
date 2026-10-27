from algen_agent_tools._env import env_name
from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.catalog import ConnectorInfo, Setting
from algen_agent_tools.sqlite.toolkit import DEFAULT_PATH, sqlite_toolkit

INFO = ConnectorInfo(
    connector="sqlite",
    title="SQLite",
    summary=(
        "Read a SQLite database file through typed tools: list and describe its tables. Named, "
        "parameterised queries are declared in code; the model never writes SQL."
    ),
    category="database",
    extra="sqlite",
    settings=(
        Setting(
            env=env_name(DEFAULT_PATH),
            description="The path of an existing SQLite database file; it opens read-only.",
            example="/var/lib/app/data.db",
        ),
    ),
    egress=(),
    code_only_options=("max_rows", "query", "read_only"),
)

SqlitePlugin = toolkit_plugin(INFO, sqlite_toolkit)
