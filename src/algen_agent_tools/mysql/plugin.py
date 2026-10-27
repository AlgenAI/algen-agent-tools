from algen_agent_tools._env import env_name
from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.catalog import ConnectorInfo, Egress, Setting
from algen_agent_tools.mysql.toolkit import DEFAULT_DSN, mysql_toolkit

INFO = ConnectorInfo(
    connector="mysql",
    title="MySQL / MariaDB",
    summary=(
        "Read a MySQL or MariaDB database through typed tools: list and describe its tables. "
        "Named, parameterised queries are declared in code; the model never writes SQL."
    ),
    category="database",
    extra="mysql",
    settings=(
        Setting(
            env=env_name(DEFAULT_DSN),
            description=(
                "The connection string (mysql://user:password@host:port/database; add "
                "?ssl=verify for verified TLS)."
            ),
            secret=True,
        ),
    ),
    egress=(Egress(protocol="mysql", host_setting=env_name(DEFAULT_DSN)),),
    code_only_options=("max_rows", "query"),
)

MysqlPlugin = toolkit_plugin(INFO, mysql_toolkit)
