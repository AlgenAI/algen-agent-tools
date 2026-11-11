"""Provider-neutral outbound email: bounded, header-safe messages and their MIME form.

Moved from ``algen_agent_runtime.communications.email`` (Runtime 0.1.0a11).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.message import EmailMessage as MimeEmailMessage
from email.policy import default as default_policy
from email.utils import formataddr, parseaddr
from enum import StrEnum
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from algen_agent_tools._core.schema import Identifier
from algen_agent_tools.attachments.models import AttachmentRef

_HEADER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{1,126}$")
DOMAIN = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")

_ATEXT = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_DOT_ATOM = rf"{_ATEXT}(?:\.{_ATEXT})*"
MESSAGE_ID_PATTERN = rf"^<{_DOT_ATOM}@(?:{_DOT_ATOM}|\[[!-Z^-~]*\])>$"
"""An RFC 5322 ``msg-id``: ``<id-left@id-right>``, a dot-atom on the left and a dot-atom or a
bracketed literal on the right; no spaces, comments or folding."""
MAX_MESSAGE_ID_CHARS = 980
"""A Message-ID's longest form here: with its header name it still fits one 998-octet line."""
MAX_REFERENCES = 100
"""The most Message-IDs a reply's References may name; the same bound a read returns."""
MAX_REFERENCES_CHARS = 20_000

MessageId = Annotated[
    Identifier,
    Field(min_length=5, max_length=MAX_MESSAGE_ID_CHARS, pattern=MESSAGE_ID_PATTERN),
]
"""One Message-ID with its angle brackets, such as ``<CAF=abc@mail.example.com>``: the form
``message_id_header``, ``in_reply_to`` and ``references`` take when a message is read."""

ThreadId = Annotated[Identifier, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")]
"""A provider's own thread id, such as Gmail's ``threadId``."""


def check_threading(
    in_reply_to: str | None, references: tuple[str, ...], thread_id: str | None = None
) -> None:
    """Refuse threading fields that cannot go out together: References longer than
    :data:`MAX_REFERENCES_CHARS`, or a provider thread without the message it replies to."""
    if sum(len(item) + 1 for item in references) > MAX_REFERENCES_CHARS:
        raise ValueError(f"email references exceed {MAX_REFERENCES_CHARS} characters")
    if thread_id is not None and in_reply_to is None:
        raise ValueError("thread_id needs in_reply_to: a provider threads a reply by both")


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


class EmailAttachmentReference(AttachmentRef):
    """Tenant-scoped Runtime artifact reference; message contracts never embed file bytes."""


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
    in_reply_to: MessageId | None = None
    """The Message-ID of the message this one replies to; sent as ``In-Reply-To``."""
    references: tuple[MessageId, ...] = Field(default=(), max_length=MAX_REFERENCES)
    """The thread's Message-IDs, oldest first: the parent's References. ``in_reply_to`` is
    added at the end when it is not there; sent as ``References``."""
    thread_id: ThreadId | None = None
    """The provider's own thread to send into (Gmail ``threadId``). A transport without
    provider threads refuses a message that sets it."""

    @model_validator(mode="after")
    def validate_message(self) -> EmailMessage:
        reject_header_injection(self.subject, "email subject")
        check_threading(self.in_reply_to, self.references, self.thread_id)
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

    def thread_references(self) -> tuple[str, ...]:
        """The References header's Message-IDs: ``references``, then ``in_reply_to`` when it
        is not already among them."""
        if self.in_reply_to is None or self.in_reply_to in self.references:
            return self.references
        return (*self.references, self.in_reply_to)


class EmailDeliveryStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class EmailDeliveryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    provider_message_id: Identifier = Field(min_length=1, max_length=998)
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


def _folded(name: str, ids: tuple[str, ...], width: int = 78) -> str:
    """``ids`` as one header value, folded between Message-IDs so lines stay within ``width``
    where an id allows it."""
    lines = [ids[0]]
    used = len(name) + 2 + len(ids[0])
    for item in ids[1:]:
        if used + 1 + len(item) <= width:
            lines[-1] += " " + item
            used += 1 + len(item)
        else:
            lines.append(" " + item)
            used = 1 + len(item)
    return "\n".join(lines)


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
    # Threading headers are stored folded between Message-IDs and never refolded: the email
    # package would turn a Message-ID longer than a line into RFC 2047 encoded words, which no
    # receiver reads as a Message-ID. Only those raw headers are affected by refold_source.
    mime = MimeEmailMessage(policy=default_policy.clone(refold_source="none"))
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
    if message.in_reply_to is not None:
        mime.set_raw("In-Reply-To", message.in_reply_to)
    references = message.thread_references()
    if references:
        mime.set_raw("References", _folded("References", references))
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
