"""Read and send mail for one Gmail or Google Workspace mailbox with an OAuth refresh token.

Replaces the Runtime's ``communications.gmail_inbox_tool`` and ``GmailMailboxClient``.
"""

from algen_agent_tools.gmail.client import (
    GmailMailboxClient,
    GmailMailboxPage,
    GmailOAuthSettings,
)
from algen_agent_tools.gmail.toolkit import gmail_toolkit
from algen_agent_tools.mail.inbound import InboundEmailAttachment, InboundEmailSummary

__all__ = [
    "GmailMailboxClient",
    "GmailMailboxPage",
    "GmailOAuthSettings",
    "InboundEmailAttachment",
    "InboundEmailSummary",
    "gmail_toolkit",
]
