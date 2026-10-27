from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.catalog import ConnectorInfo
from algen_agent_tools.sample.toolkit import sample_toolkit

INFO = ConnectorInfo(
    connector="sample",
    title="Sample",
    summary="Echoes text back. Use it to check that a deployment loads connectors end to end.",
    category="utility",
)

SamplePlugin = toolkit_plugin(INFO, sample_toolkit)
