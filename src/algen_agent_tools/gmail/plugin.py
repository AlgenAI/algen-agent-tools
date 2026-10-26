from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.gmail.toolkit import DEFAULT_ENV, gmail_toolkit

GmailPlugin = toolkit_plugin("gmail", gmail_toolkit, env=DEFAULT_ENV)
