"""Provider-neutral outbound email: bounded, header-safe messages and their MIME form.

Moved from ``algen_agent_runtime.communications.email`` (Runtime 0.1.0a11).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.message import EmailMessage as MimeEmailMessage
from email.utils import formataddr, parseaddr
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

_HEADER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{1,126}$")
DOMAIN = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")


def reject_header_injection(value: str, field: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError(f"{field} cannot contain line breaks")
    return value


class EmailAddress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    # format: email tells the Runtime's pii guardrail (0.1.0a12+) that this field is meant to hold
    # an address, so it leaves a single address here unredacted and still screens the rest.
    address: str = Field(min_length=3, max_length=320, json_schema_extra={"format": "email"})
    display_name: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def validate_address(self) -> EmailAddress:
        reject_header_injection(self.address, "email address")
        _, parsed = parseaddr(self.address)
        if parsed != self.address or "@" not in parsed or parsed.startswith("@"):
            raise ValueError("email address is invalid")
        local, _, domain = parsed.rpartition("@")
        if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
            raise ValueError("email address is invalid")
        if self.display_name is not None:
            reject_header_injection(self.display_name, "display name")
        return self

    def formatted(self) -> str:
        return formataddr((self.display_name or "", self.address))


class EmailAttachmentReference(BaseModel):
    """Tenant-scoped Runtime artifact reference; message contracts never embed file bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: str = Field(min_length=1, max_length=256)
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=3, max_length=255)

    @model_validator(mode="after")
    def validate_filename(self) -> EmailAttachmentReference:
        reject_header_injection(self.filename, "attachment filename")
        if "/" in self.filename or "\\" in self.filename or self.filename in {".", ".."}:
            raise ValueError("attachment filename must not contain a path")
        media_type, separator, subtype = self.media_type.partition("/")
        if not separator or not media_type or not subtype:
            raise ValueError("attachment media_type must be a type/subtype value")
        if any(character.isspace() for character in self.media_type) or any(
            character in self.media_type for character in ";\r\n"
        ):
            raise ValueError("attachment media_type must not contain parameters or whitespace")
        return self


class EmailMessage(BaseModel):
    """Provider-neutral outbound message with bounded, header-safe content."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    sender: EmailAddress
    to: tuple[EmailAddress, ...] = Field(min_length=1, max_length=100)
    cc: tuple[EmailAddress, ...] = Field(default=(), max_length=100)
    bcc: tuple[EmailAddress, ...] = Field(default=(), max_length=100)
    reply_to: EmailAddress | None = None
    subject: str = Field(min_length=1, max_length=998)
    text_body: str | None = Field(default=None, max_length=1_000_000)
    html_body: str | None = Field(default=None, max_length=2_000_000)
    attachments: tuple[EmailAttachmentReference, ...] = Field(default=(), max_length=20)
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_message(self) -> EmailMessage:
        reject_header_injection(self.subject, "email subject")
        if self.text_body is None and self.html_body is None:
            raise ValueError("email requires text_body or html_body")
        recipients = (*self.to, *self.cc, *self.bcc)
        if len(recipients) > 100:
            raise ValueError("email supports at most 100 total recipients")
        normalized = [item.address.casefold() for item in recipients]
        if len(normalized) != len(set(normalized)):
            raise ValueError("email recipients must be unique across to, cc, and bcc")
        if len(self.headers) > 32:
            raise ValueError("email supports at most 32 custom headers")
        for name, value in self.headers.items():
            if not name.lower().startswith("x-"):
                raise ValueError("custom email headers must use the X- prefix")
            reject_header_injection(name, "email header name")
            reject_header_injection(value, "email header value")
            if not _HEADER_NAME.fullmatch(name):
                raise ValueError("custom email header name is invalid")
        return self


class EmailDeliveryStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class EmailDeliveryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    provider_message_id: str = Field(min_length=1, max_length=998)
    status: EmailDeliveryStatus
    accepted_recipients: tuple[str, ...] = ()
    rejected_recipients: dict[str, str] = Field(default_factory=dict)
    accepted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EmailDeliveryError(RuntimeError):
    """Content-minimal transport failure safe to surface through Runtime tooling."""


class EmailSender(Protocol):
    async def send(
        self, message: EmailMessage, *, idempotency_key: str
    ) -> EmailDeliveryReceipt: ...


EmailArtifactLoader = Callable[[EmailAttachmentReference], Awaitable[bytes]]


def build_mime_message(
    message: EmailMessage,
    *,
    idempotency_key: str,
    attachments: list[tuple[EmailAttachmentReference, bytes]],
    message_id_domain: str,
    include_bcc: bool = False,
) -> tuple[MimeEmailMessage, str]:
    """The MIME message and its deterministic Message-ID.

    SMTP passes every recipient in the envelope, so Bcc stays out of the headers. An API that
    takes recipients from the raw message (Gmail) needs ``include_bcc=True``; it removes the
    header before delivery.
    """
    mime = MimeEmailMessage()
    message_id = f"<{hashlib.sha256(idempotency_key.encode()).hexdigest()}@{message_id_domain}>"
    mime["Message-ID"] = message_id
    mime["From"] = message.sender.formatted()
    mime["To"] = ", ".join(item.formatted() for item in message.to)
    if message.cc:
        mime["Cc"] = ", ".join(item.formatted() for item in message.cc)
    if include_bcc and message.bcc:
        mime["Bcc"] = ", ".join(item.formatted() for item in message.bcc)
    if message.reply_to is not None:
        mime["Reply-To"] = message.reply_to.formatted()
    mime["Subject"] = message.subject
    for name, value in message.headers.items():
        mime[name] = value
    if message.text_body is not None:
        mime.set_content(message.text_body)
    else:
        mime.set_content("This message requires an HTML-capable email client.")
    if message.html_body is not None:
        mime.add_alternative(message.html_body, subtype="html")
    for reference, payload in attachments:
        media_type, _, subtype = reference.media_type.partition("/")
        mime.add_attachment(
            payload,
            maintype=media_type,
            subtype=subtype or "octet-stream",
            filename=reference.filename,
        )
    return mime, message_id
