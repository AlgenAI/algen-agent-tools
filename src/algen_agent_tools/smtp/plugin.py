from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.smtp.toolkit import DEFAULT_ENV, smtp_toolkit

SmtpPlugin = toolkit_plugin("smtp", smtp_toolkit, env=DEFAULT_ENV)
