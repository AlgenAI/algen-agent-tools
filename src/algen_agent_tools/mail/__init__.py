"""Shared email contracts for the mail connectors: outbound messages and the send tool, and
bounded summaries of inbound mail."""

from algen_agent_tools.mail.fake import FakeMailbox
from algen_agent_tools.mail.inbound import InboundEmailAttachment, InboundEmailSummary
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
from algen_agent_tools.mail.tool import (
    ContextualArtifactLoader,
    MailClient,
    OutgoingEmail,
    OutgoingEmailWithAttachments,
    add_send_tool,
    bind_artifact_loader,
)

__all__ = [
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
    "InboundEmailSummary",
    "MailClient",
    "OutgoingEmail",
    "OutgoingEmailWithAttachments",
    "add_send_tool",
    "bind_artifact_loader",
    "build_mime_message",
]
