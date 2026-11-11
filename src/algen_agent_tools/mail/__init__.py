"""Shared email contracts for the mail connectors: outbound messages and the send tool, and
bounded summaries of inbound mail. ``find_links`` and ``unwrap_redirect`` (from
:mod:`algen_agent_tools.documents.inspection`) find links in a body behind mail-client redirects."""

from algen_agent_tools.documents.inspection.links import find_links, unwrap_redirect
from algen_agent_tools.mail.fake import FakeMailbox
from algen_agent_tools.mail.inbound import (
    EmailThread,
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
    MessageId,
    build_mime_message,
)
from algen_agent_tools.mail.sender_auth import (
    GMAIL_AUTHSERV_ID,
    SenderAuth,
    authentication_verdict,
    topmost_verdict,
)
from algen_agent_tools.mail.tool import (
    AttachmentText,
    ContextualArtifactLoader,
    MailClient,
    OutgoingEmail,
    OutgoingEmailWithAttachments,
    ThreadedOutgoingEmail,
    ThreadedOutgoingEmailWithAttachments,
    add_mailbox_read_tools,
    add_read_attachment_tool,
    add_send_tool,
    bind_artifact_loader,
)

__all__ = [
    "GMAIL_AUTHSERV_ID",
    "AttachmentText",
    "ContextualArtifactLoader",
    "EmailAddress",
    "EmailArtifactLoader",
    "EmailAttachmentReference",
    "EmailDeliveryError",
    "EmailDeliveryReceipt",
    "EmailDeliveryStatus",
    "EmailMessage",
    "EmailSender",
    "EmailThread",
    "FakeMailbox",
    "InboundEmailAttachment",
    "InboundEmailDetail",
    "InboundEmailSummary",
    "MailClient",
    "MessageId",
    "OutgoingEmail",
    "OutgoingEmailWithAttachments",
    "SenderAuth",
    "ThreadedOutgoingEmail",
    "ThreadedOutgoingEmailWithAttachments",
    "add_mailbox_read_tools",
    "add_read_attachment_tool",
    "add_send_tool",
    "authentication_verdict",
    "bind_artifact_loader",
    "build_mime_message",
    "find_links",
    "html_to_text",
    "topmost_verdict",
    "unwrap_redirect",
]
