from algen_agent_tools._env import env_name
from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.catalog import ConnectorInfo, Egress, Setting
from algen_agent_tools.postgres.toolkit import DEFAULT_DSN, postgres_toolkit

INFO = ConnectorInfo(
    connector="postgres",
    title="PostgreSQL",
    summary=(
        "Read a PostgreSQL database through typed tools: list and describe the tables in the "
        "allowed schemas. Named, parameterised queries are declared in code; the model never "
        "writes SQL."
    ),
    category="database",
    extra="postgres",
    settings=(
        Setting(
            env=env_name(DEFAULT_DSN),
            description="The connection string (postgresql://user:password@host:port/database).",
            secret=True,
        ),
    ),
    egress=(Egress(protocol="postgres", host_setting=env_name(DEFAULT_DSN)),),
    code_only_options=("max_rows", "query", "schemas"),
)

PostgresPlugin = toolkit_plugin(INFO, postgres_toolkit)
