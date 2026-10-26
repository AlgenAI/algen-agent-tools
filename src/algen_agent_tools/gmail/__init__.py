"""Read and send mail for one Gmail or Google Workspace mailbox with an OAuth refresh token.

Replaces the Runtime's ``communications.gmail_inbox_tool`` and ``GmailMailboxClient``.
"""

from algen_agent_tools.gmail.client import (
    GmailMailboxClient,
    GmailMailboxPage,
    GmailOAuthSettings,
    InboundEmailAttachment,
    InboundEmailSummary,
)
from algen_agent_tools.gmail.toolkit import gmail_toolkit

__all__ = [
    "GmailMailboxClient",
    "GmailMailboxPage",
    "GmailOAuthSettings",
    "InboundEmailAttachment",
    "InboundEmailSummary",
    "gmail_toolkit",
]
