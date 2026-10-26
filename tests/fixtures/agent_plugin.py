"""A project's plugin for the end-to-end test: the sample toolkit and SMTP over a fake mailbox."""

from __future__ import annotations

from typing import Any

from algen_agent_runtime.plugins.sdk import PluginManifest, plugin

from algen_agent_tools.mail import FakeMailbox
from algen_agent_tools.sample import sample_toolkit
from algen_agent_tools.smtp import smtp_toolkit

MAILBOX = FakeMailbox()


@plugin(PluginManifest(name="tests.agent", version="1.0.0", publisher="algen-agent-tools tests"))
class AgentPlugin:
    def register(self, context: Any) -> None:
        sample_toolkit().register(context)
        smtp_toolkit(client=MAILBOX.factory("desk@example.org")).register(context)
