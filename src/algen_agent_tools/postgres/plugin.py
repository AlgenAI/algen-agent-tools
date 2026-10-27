from algen_agent_tools._env import env_names
from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.postgres.toolkit import DEFAULT_DSN, postgres_toolkit

PostgresPlugin = toolkit_plugin("postgres", postgres_toolkit, env=env_names(DEFAULT_DSN))
