from algen_agent_tools._env import env_name
from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.catalog import ConnectorInfo, Egress, Setting
from algen_agent_tools.gmail.toolkit import (
    ALLOWED_DOMAINS,
    CLIENT_ID,
    CLIENT_SECRET,
    REFRESH_TOKEN,
    SENDER,
    gmail_toolkit,
)

INFO = ConnectorInfo(
    connector="gmail",
    title="Gmail",
    summary=(
        "Read and send mail for one Gmail or Google Workspace mailbox with an OAuth refresh "
        "token: list messages with a Gmail search query, and send from the mailbox's address."
    ),
    category="email",
    extra="gmail",
    settings=(
        Setting(env=env_name(CLIENT_ID), description="The Google OAuth client ID."),
        Setting(
            env=env_name(CLIENT_SECRET),
            description="The Google OAuth client secret.",
            secret=True,
        ),
        Setting(
            env=env_name(REFRESH_TOKEN),
            description="An OAuth refresh token for the mailbox, with Gmail read and send scopes.",
            secret=True,
        ),
        Setting(
            env=env_name(SENDER),
            description="The mailbox's address, or a verified alias, that email is sent from.",
            example="agent@example.com",
        ),
        Setting(
            env=env_name(ALLOWED_DOMAINS),
            description=(
                "Comma-separated domains recipients and Reply-To must belong to (subdomains "
                "included). Unset: any domain."
            ),
            required=False,
            example="example.com,partner.example",
        ),
    ),
    egress=(
        Egress(protocol="https", host="oauth2.googleapis.com", port=443),
        Egress(protocol="https", host="gmail.googleapis.com", port=443),
    ),
    code_only_options=("artifact_loader",),
)

GmailPlugin = toolkit_plugin(INFO, gmail_toolkit)
