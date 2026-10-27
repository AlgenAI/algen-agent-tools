from algen_agent_tools._env import env_name
from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.catalog import ConnectorInfo, Egress, Setting
from algen_agent_tools.smtp.toolkit import (
    ALLOWED_DOMAINS,
    HOST,
    PASSWORD,
    PORT,
    SECURITY,
    SENDER,
    USERNAME,
    smtp_toolkit,
)

INFO = ConnectorInfo(
    connector="smtp",
    title="SMTP email",
    summary=(
        "Send email through any SMTP server from one fixed sender address. The model chooses "
        "recipients, subject and body; every send is an external action."
    ),
    category="email",
    extra="smtp",
    settings=(
        Setting(
            env=env_name(HOST),
            description="The SMTP server's host name.",
            example="smtp.example.com",
        ),
        Setting(
            env=env_name(SENDER),
            description="The address every email is sent from.",
            example="agent@example.com",
        ),
        Setting(env=env_name(PORT), description="The SMTP port.", required=False, default="587"),
        Setting(
            env=env_name(SECURITY),
            description="Connection security: starttls, tls or plain.",
            required=False,
            default="starttls",
        ),
        Setting(
            env=env_name(USERNAME),
            description="The SMTP login, when the server needs one.",
            required=False,
        ),
        Setting(
            env=env_name(PASSWORD),
            description="The SMTP password, when the server needs one.",
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
            example="example.com,partner.example",
        ),
    ),
    egress=(Egress(protocol="smtp", host_setting=env_name(HOST)),),
    code_only_options=("artifact_loader",),
)

SmtpPlugin = toolkit_plugin(INFO, smtp_toolkit)
