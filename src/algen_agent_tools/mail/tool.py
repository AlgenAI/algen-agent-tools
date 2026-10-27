"""The ``send_email`` action the SMTP and Gmail toolkits share.

The deployment fixes the sender; the model chooses only recipients, subject and body. An
optional domain allowlist bounds who can be written to and who replies go to.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from algen_agent_runtime.exceptions.errors import PolicyDeniedError
from algen_agent_runtime.tools.authoring import Toolkit, external
from algen_agent_runtime.tools.contracts import Tool, ToolContext
from pydantic import BaseModel, ConfigDict, Field

from algen_agent_tools._core.context import current_call, tool_call
from algen_agent_tools.mail.models import (
    EmailAddress,
    EmailArtifactLoader,
    EmailAttachmentReference,
    EmailDeliveryError,
    EmailDeliveryReceipt,
    EmailMessage,
    EmailSender,
)

AUDIT_METADATA = {"content_capture": "disabled", "channel": "email"}

ContextualArtifactLoader = Callable[[EmailAttachmentReference, ToolContext], Awaitable[bytes]]
"""Loads an attachment's bytes for one tool call. It receives the call's context so it can
check that the artifact belongs to the caller's tenant (and run) before returning anything.
It is :data:`algen_agent_tools.attachments.ArtifactLoader` narrowed to email references."""


def bind_artifact_loader(loader: ContextualArtifactLoader) -> EmailArtifactLoader:
    """A transport-level loader that passes the current ``send_email`` call's context on."""

    async def load(reference: EmailAttachmentReference) -> bytes:
        context = current_call()
        if context is None:
            raise EmailDeliveryError("attachments load only inside a send_email tool call")
        return await loader(reference, context)

    return load


class OutgoingEmail(BaseModel):
    """What the model supplies to send one email; the sender is the deployment's."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    to: tuple[EmailAddress, ...] = Field(min_length=1, max_length=50)
    cc: tuple[EmailAddress, ...] = Field(default=(), max_length=50)
    bcc: tuple[EmailAddress, ...] = Field(default=(), max_length=50)
    reply_to: EmailAddress | None = None
    subject: str = Field(min_length=1, max_length=998)
    text_body: str | None = Field(default=None, max_length=1_000_000)
    html_body: str | None = Field(default=None, max_length=2_000_000)


class OutgoingEmailWithAttachments(OutgoingEmail):
    """An :class:`OutgoingEmail` that may attach Runtime artifacts by reference."""

    attachments: tuple[EmailAttachmentReference, ...] = Field(default=(), max_length=20)


@dataclass(frozen=True)
class MailClient:
    """A toolkit's client: the fixed sender and the transport that delivers for it."""

    sender: EmailAddress
    transport: Any
    """An :class:`~algen_agent_tools.mail.models.EmailSender`; the Gmail transport also
    lists messages."""

    async def aclose(self) -> None:
        close = getattr(self.transport, "aclose", None) or getattr(self.transport, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result


def _addressed_domains(message: EmailMessage) -> set[str]:
    """Every domain the message reaches: recipients, and where replies go."""
    addresses = (*message.to, *message.cc, *message.bcc)
    if message.reply_to is not None:
        addresses = (*addresses, message.reply_to)
    return {address.address.rpartition("@")[2].casefold() for address in addresses}


def add_send_tool(
    toolkit: Toolkit,
    *,
    provider: str,
    attachments: bool = False,
    allowed_domains: tuple[str, ...] = (),
    timeout: float = 60,
) -> Tool:
    """Add ``<toolkit>.send_email``: an external action, keyed and tried once.

    The idempotency key becomes the message's deterministic Message-ID. ``allowed_domains``
    (when not empty) refuses any recipient or reply-to address outside those domains or their
    subdomains.
    """
    allowed = tuple(domain.casefold().strip(".") for domain in allowed_domains)
    model = OutgoingEmailWithAttachments if attachments else OutgoingEmail

    async def send_email(email: OutgoingEmail, context: ToolContext) -> EmailDeliveryReceipt:
        client: MailClient = await toolkit.client()
        message = EmailMessage(sender=client.sender, **dict(email))
        if allowed:
            outside = sorted(
                domain
                for domain in _addressed_domains(message)
                if not any(domain == item or domain.endswith(f".{item}") for item in allowed)
            )
            if outside:
                raise PolicyDeniedError(f"recipients outside the allowed domains: {outside}")
        transport: EmailSender = client.transport
        with tool_call(context):
            return await transport.send(message, idempotency_key=context.idempotency_key)

    send_email.__doc__ = (
        f"Send one email through {provider} from the deployment's own address. Give at least "
        "one recipient, a subject and a text or HTML body. Every call sends a real email."
    )
    send_email.__annotations__ = {
        "email": model,
        "context": ToolContext,
        "return": EmailDeliveryReceipt,
    }
    return toolkit.tool(
        external(timeout=timeout),
        max_result_bytes=64_000,
        audit_metadata=AUDIT_METADATA,
    )(send_email)
