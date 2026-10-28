from algen_agent_tools._env import env_name
from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.catalog import ConnectorInfo, Egress, Setting
from algen_agent_tools.imap.toolkit import (
    AUTHSERV_ID,
    FOLDERS,
    HOST,
    PASSWORD,
    PORT,
    USERNAME,
    imap_toolkit,
)

INFO = ConnectorInfo(
    connector="imap",
    title="IMAP mailbox",
    summary=(
        "Read one mailbox over IMAP with an app password (Gmail, Google Workspace, Zoho, "
        "Fastmail or any IMAPS server): list messages with typed filters, read one message's "
        "text, and save an attachment as an artifact. Read-only; nothing is marked as seen."
    ),
    category="email",
    extra="imap",
    settings=(
        Setting(
            env=env_name(HOST),
            description="The IMAP server's host name. The connection is always TLS.",
            example="imap.gmail.com",
        ),
        Setting(
            env=env_name(USERNAME),
            description="The mailbox login, usually its address.",
            example="hr@example.com",
        ),
        Setting(
            env=env_name(PASSWORD),
            description="An app password for the mailbox (not the account password).",
            secret=True,
        ),
        Setting(env=env_name(PORT), description="The IMAPS port.", required=False, default="993"),
        Setting(
            env=env_name(FOLDERS),
            description=(
                "Comma-separated folders the tools may read; the first is the default. Names "
                "are printable ASCII."
            ),
            required=False,
            default="INBOX",
            example="INBOX,Candidates",
        ),
        Setting(
            env=env_name(AUTHSERV_ID),
            description=(
                "The receiving server's name in its Authentication-Results headers, which "
                "sender_auth trusts. Unset: mx.google.com on Gmail, otherwise sender_auth is "
                "unknown."
            ),
            required=False,
            example="mx.zoho.com",
        ),
    ),
    egress=(Egress(protocol="imap", host_setting=env_name(HOST)),),
    code_only_options=("artifact_sink", "attachment_policy"),
)

ImapPlugin = toolkit_plugin(INFO, imap_toolkit)
