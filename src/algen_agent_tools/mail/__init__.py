"""Shared email contracts for the mail connectors: outbound messages and the send tool, and
bounded summaries of inbound mail."""

from algen_agent_tools.mail.fake import FakeMailbox
from algen_agent_tools.mail.inbound import (
    InboundEmailAttachment,
    InboundEmailDetail,
    InboundEmailSummary,
    html_to_text,
)
from algen_agent_tools.mail.models import (
    EmailAddress,
    EmailArtifactLoader,
    EmailAttachmentReference,
    EmailDeliveryError,
    EmailDeliveryReceipt,
    EmailDeliveryStatus,
    EmailMessage,
    EmailSender,
    build_mime_message,
)
from algen_agent_tools.mail.sender_auth import (
    GMAIL_AUTHSERV_ID,
    SenderAuth,
    authentication_verdict,
    topmost_verdict,
)
from algen_agent_tools.mail.tool import (
    ContextualArtifactLoader,
    MailClient,
    OutgoingEmail,
    OutgoingEmailWithAttachments,
    add_mailbox_read_tools,
    add_send_tool,
    bind_artifact_loader,
)

__all__ = [
    "GMAIL_AUTHSERV_ID",
    "ContextualArtifactLoader",
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
    "InboundEmailDetail",
    "InboundEmailSummary",
    "MailClient",
    "OutgoingEmail",
    "OutgoingEmailWithAttachments",
    "SenderAuth",
    "add_mailbox_read_tools",
    "add_send_tool",
    "authentication_verdict",
    "bind_artifact_loader",
    "build_mime_message",
    "html_to_text",
    "topmost_verdict",
]
