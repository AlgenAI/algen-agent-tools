"""Import paths other projects rely on.

The Runtime's deprecation messages and ``docs/migrating-from-runtime.md`` send users to these
names, so moving or renaming one breaks code we have told people to write. Change this list only
with a deprecation (see docs/connector-policy.md).
"""

from __future__ import annotations

import importlib

import pytest

PUBLIC_NAMES = {
    "algen_agent_tools.mail": (
        "EmailAddress",
        "EmailArtifactLoader",
        "EmailAttachmentReference",
        "EmailDeliveryError",
        "EmailDeliveryReceipt",
        "EmailDeliveryStatus",
        "EmailMessage",
        "EmailSender",
        "FakeMailbox",
        "InboundEmailAttachment",
        "InboundEmailSummary",
    ),
    "algen_agent_tools.smtp": ("SmtpEmailSender", "SmtpSettings", "smtp_toolkit"),
    "algen_agent_tools.gmail": (
        "GmailMailboxClient",
        "GmailMailboxPage",
        "GmailOAuthSettings",
        "InboundEmailAttachment",
        "InboundEmailSummary",
        "gmail_toolkit",
    ),
    "algen_agent_tools.postgres": ("PostgresToolkit", "postgres_toolkit"),
    "algen_agent_tools.outlook": ("OutlookMailboxClient", "OutlookSettings", "outlook_toolkit"),
    "algen_agent_tools.mcp": ("McpOverlay", "overlay_plugin"),
    "algen_agent_tools.messaging": (
        "Capability",
        "FakeMessaging",
        "InboundMessage",
        "MessagingClient",
        "MessagingPlatform",
        "messaging_toolkit",
    ),
    "algen_agent_tools.telegram": ("TelegramBotClient", "TelegramSettings", "telegram_toolkit"),
    "algen_agent_tools.testing": (
        "assert_toolkit_contract",
        "describe_plugin",
        "plugin_tools",
        "toolkit_contract_problems",
    ),
    "algen_agent_tools.catalog": ("ConnectorInfo", "build_catalog", "load_catalog"),
    "algen_agent_tools.documents": ("DocumentText", "documents_toolkit"),
    "algen_agent_tools.attachments": (
        "ArtifactLoader",
        "ArtifactSink",
        "AttachmentPolicy",
        "AttachmentRef",
        "add_get_attachment_tool",
    ),
}


@pytest.mark.parametrize(("module", "names"), sorted(PUBLIC_NAMES.items()))
def test_public_names_stay_importable(module: str, names: tuple[str, ...]) -> None:
    loaded = importlib.import_module(module)
    missing = [name for name in names if not hasattr(loaded, name)]
    assert not missing, f"{module} no longer exports {missing}"


def test_postgres_toolkit_keeps_its_query_method() -> None:
    from algen_agent_tools.postgres import PostgresToolkit

    assert callable(PostgresToolkit.query)
