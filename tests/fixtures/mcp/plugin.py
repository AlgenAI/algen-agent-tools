from pathlib import Path

from algen_agent_tools.mcp import overlay_plugin

DeskPlugin = overlay_plugin(
    Path(__file__).with_name("ticket_desk.overlay.yaml"),
    name="tests.desk",
    version="1.0.0",
    publisher="algen-agent-tools tests",
)
