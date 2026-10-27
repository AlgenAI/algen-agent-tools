from algen_agent_tools._env import env_name
from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.catalog import ConnectorInfo, Egress, Setting
from algen_agent_tools.outlook.toolkit import (
    ALLOWED_DOMAINS,
    CLIENT_ID,
    CLIENT_SECRET,
    MAILBOX,
    REFRESH_TOKEN,
    TENANT_ID,
    outlook_toolkit,
)

INFO = ConnectorInfo(
    connector="outlook",
    title="Outlook (Microsoft 365)",
    summary=(
        "Read and send mail for one Outlook mailbox through Microsoft Graph: list a folder, read "
        "one message's text, save an attachment as an artifact, and send from the mailbox."
    ),
    category="email",
    extra="outlook",
    settings=(
        Setting(
            env=env_name(TENANT_ID),
            description="The Microsoft Entra tenant id or domain.",
            example="contoso.onmicrosoft.com",
        ),
        Setting(env=env_name(CLIENT_ID), description="The app registration's client id."),
        Setting(
            env=env_name(CLIENT_SECRET),
            description="The app registration's client secret.",
            secret=True,
        ),
        Setting(
            env=env_name(MAILBOX),
            description="The mailbox's address; mail is read from and sent as it.",
            example="agent@contoso.com",
        ),
        Setting(
            env=env_name(REFRESH_TOKEN),
            description=(
                "A refresh token to act as the signed-in user (delegated). Unset: the app's own "
                "credentials on the mailbox (application access)."
            ),
            secret=True,
            required=False,
        ),
        Setting(
            env=env_name(ALLOWED_DOMAINS),
            description=(
                "Comma-separated domains recipients and Reply-To must belong to (subdomains "
                "included). Unset: any domain."
            ),
            required=False,
            example="contoso.com,partner.example",
        ),
    ),
    egress=(
        Egress(protocol="https", host="login.microsoftonline.com", port=443),
        Egress(protocol="https", host="graph.microsoft.com", port=443),
    ),
    code_only_options=("artifact_loader", "artifact_sink", "attachment_policy"),
)

OutlookPlugin = toolkit_plugin(INFO, outlook_toolkit)
