"""Shared outbound email contracts for the SMTP and Gmail connectors."""

from algen_agent_tools.mail.fake import FakeMailbox
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
    "MailClient",
    "OutgoingEmail",
    "OutgoingEmailWithAttachments",
    "add_send_tool",
    "bind_artifact_loader",
    "build_mime_message",
]
