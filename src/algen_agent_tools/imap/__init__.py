"""Read one mailbox over IMAP (RFC 3501) with an app password: typed listing filters, bounded
message text and attachments saved as artifacts. Read-only; nothing is marked as seen."""

from algen_agent_tools.imap.client import ImapMailboxClient, ImapMailboxPage, ImapSettings
from algen_agent_tools.imap.fake import FakeImapServer
from algen_agent_tools.imap.toolkit import imap_toolkit

__all__ = [
    "FakeImapServer",
    "ImapMailboxClient",
    "ImapMailboxPage",
    "ImapSettings",
    "imap_toolkit",
]
