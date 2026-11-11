"""Provider-neutral inbound email: bounded summaries and bodies of messages a mailbox connector
reads."""

from __future__ import annotations

import mimetypes
import re
from collections.abc import Sequence
from email.utils import getaddresses
from functools import cache
from html.parser import HTMLParser
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from algen_agent_tools._core.schema import Identifier

MAX_BODY_CHARS = 100_000
MAX_THREAD_MESSAGES = 50
MAX_ATTACHMENTS = 20
"""The most attachments a message summary lists; ``attachment_count`` still counts them all."""
_MESSAGE_ID = re.compile(r"<[^<>\s]{1,995}>")


class InboundEmailAttachment(BaseModel):
    """Bounded Gmail attachment descriptor; bytes are fetched only on explicit demand."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_attachment_id: Identifier = Field(min_length=1, max_length=2_000)
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="application/octet-stream", min_length=3, max_length=255)
    size: int = Field(default=0, ge=0)
    part_id: Identifier | None = Field(default=None, max_length=64)
    """The attachment's stable place in the message; pass it to ``get_attachment``."""
    is_inline: bool = Field(
        default=False,
        description="The message shows this part in its body (an inline Content-Disposition, "
        "or a Content-ID without one), such as a signature logo or a pasted picture. Inline "
        "parts can still be real documents: some mail clients mark attached PDFs inline.",
    )
    content_id: Identifier | None = Field(
        default=None,
        max_length=998,
        description="The part's Content-ID without angle brackets, as the HTML body's cid: "
        "links name it.",
    )


_ADDRESS = re.compile(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+")

# One email address. ``format: email`` in the output schema tells the Runtime's ``pii``
# guardrail (0.1.0a19+) that the field is an address by design, so it reaches the model and an
# agent can reply to the mail it read; message bodies are still redacted.
Address = Annotated[str, Field(max_length=320, json_schema_extra={"format": "email"})]


def _addresses(header: str) -> list[tuple[str, str]]:
    """``(name, address)`` for each well-formed address in a header's display text."""
    return [
        (name.strip(), address.strip())
        for name, address in getaddresses([header])
        if _ADDRESS.fullmatch(address.strip())
    ]


class InboundEmailSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_message_id: Identifier
    thread_id: Identifier | None = None
    sender: str = ""
    """The sender as display text (``Name <address>``)."""
    recipients: str = ""
    """The recipients as display text."""
    sender_address: Address | None = None
    """The sender's address alone, read from ``sender``."""
    sender_name: str | None = Field(default=None, max_length=320)
    recipient_addresses: tuple[Address, ...] = Field(default=(), max_length=100)
    """The recipients' addresses alone, read from ``recipients``."""
    subject: str = ""
    received_at_ms: int = Field(default=0, ge=0)
    snippet: str = Field(default="", max_length=2_000)
    labels: tuple[str, ...] = ()
    attachments: tuple[InboundEmailAttachment, ...] = ()
    attachment_count: int = Field(
        default=0,
        ge=0,
        description="Every attachment part of the message, including any not listed in "
        "attachments.",
    )
    attachments_truncated: bool = Field(
        default=False,
        description="The message has more attachments than attachments lists.",
    )
    sender_auth: Literal["pass", "fail", "unknown"] = "unknown"
    """The receiving server's SPF/DKIM/DMARC verdict on the sender, when the connector can read
    it; see :mod:`algen_agent_tools.mail.sender_auth`. ``unknown`` is never a failure."""

    @model_validator(mode="before")
    @classmethod
    def _structured_addresses(cls, data: Any) -> Any:
        """Fill the address fields from the display text, unless the connector set them."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        listed = len(data.get("attachments") or ())
        data.setdefault("attachment_count", listed)
        if "attachments_truncated" not in data and isinstance(data["attachment_count"], int):
            data["attachments_truncated"] = data["attachment_count"] > listed
        if "sender_address" not in data:
            found = _addresses(str(data.get("sender") or ""))
            if found:
                data["sender_name"] = data.get("sender_name") or found[0][0] or None
                data["sender_address"] = found[0][1]
        if "recipient_addresses" not in data:
            data["recipient_addresses"] = tuple(
                address for _, address in _addresses(str(data.get("recipients") or ""))
            )[:100]
        if "cc" in cls.model_fields and "cc_addresses" not in data:
            data["cc_addresses"] = tuple(
                address for _, address in _addresses(str(data.get("cc") or ""))
            )[:100]
        return data

    @model_validator(mode="after")
    def _counted(self) -> InboundEmailSummary:
        if self.attachment_count < len(self.attachments):
            raise ValueError("attachment_count cannot be less than the attachments listed")
        return self


# Types the standard library's built-in table does not name.
_EXTENSIONS = {
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.ms-outlook": ".msg",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/rtf": ".rtf",
    "image/heic": ".heic",
    "image/webp": ".webp",
    "text/calendar": ".ics",
    "text/csv": ".csv",
    "text/markdown": ".md",
    "text/rtf": ".rtf",
}


@cache
def _builtin_types() -> mimetypes.MimeTypes:
    # The built-in table only, so a name does not depend on the host's mime.types files.
    return mimetypes.MimeTypes()


def generated_filename(position: int, media_type: str) -> str:
    """The name a connector gives the ``position``-th (from 1) attachment part of a message when
    the part declares none, typed from its media type: ``attachment-2.pdf``. The same part gets
    the same name in a listing and in ``get_attachment``."""
    media_type = media_type.partition(";")[0].strip().casefold()
    extension = (
        _EXTENSIONS.get(media_type)
        or _builtin_types().guess_extension(media_type, strict=False)
        or ".bin"
    )
    return f"attachment-{position}{extension}"


def normalized_content_id(value: str | None) -> str | None:
    """A Content-ID header value without its angle brackets, whitespace or control characters,
    or ``None`` when there is none."""
    cleaned = re.sub(r"[\x00-\x20\x7f]", "", value or "").removeprefix("<").removesuffix(">")
    return cleaned[:998] or None


def is_inline_part(disposition: str | None, content_id: str | None) -> bool:
    """Whether a part is shown in the message body: its Content-Disposition says ``inline``, or
    it has a Content-ID and no disposition (an image a ``multipart/related`` body refers to)."""
    disposition = (disposition or "").strip().casefold()
    return disposition == "inline" or (not disposition and content_id is not None)


def attachment_manifest(found: Sequence[InboundEmailAttachment]) -> dict[str, Any]:
    """The attachment fields of an :class:`InboundEmailSummary` from every attachment part of a
    message: the first :data:`MAX_ATTACHMENTS` of them, their total count and whether some were
    left out."""
    return {
        "attachments": tuple(found[:MAX_ATTACHMENTS]),
        "attachment_count": len(found),
        "attachments_truncated": len(found) > MAX_ATTACHMENTS,
    }


class InboundEmailDetail(InboundEmailSummary):
    """One message with its body as bounded plain text."""

    cc: str = ""
    cc_addresses: tuple[Address, ...] = Field(default=(), max_length=100)
    """The cc'd addresses alone, read from ``cc``."""
    text_body: str = Field(default="", max_length=MAX_BODY_CHARS)
    body_truncated: bool = False
    quoted_text_removed: bool = False
    """``text_body`` is the reply's own text: the history it quoted and its signature were
    removed (``include="latest"``)."""
    message_id_header: Identifier | None = Field(default=None, max_length=998)
    """The message's own Message-ID header (``<id@host>``), when it has one."""
    in_reply_to: Identifier | None = Field(default=None, max_length=998)
    """The Message-ID this message replies to."""
    references: tuple[Identifier, ...] = Field(default=(), max_length=100)
    """The Message-IDs of the thread so far, oldest first, from the References header."""


class EmailThread(BaseModel):
    """The messages of one thread, oldest first."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    thread_id: Identifier
    messages: tuple[InboundEmailDetail, ...] = Field(default=(), max_length=MAX_THREAD_MESSAGES)
    truncated: bool = False
    """The thread has more messages than were returned; the newest are kept."""


def message_ids(header: str) -> tuple[str, ...]:
    """The ``<id@host>`` Message-IDs in a header value, in order, at most 100."""
    return tuple(_MESSAGE_ID.findall(header or ""))[:100]


def thread_headers(message_id: str, in_reply_to: str, references: str) -> dict[str, Any]:
    """The threading fields of an :class:`InboundEmailDetail` from its raw header values."""
    own, parent = message_ids(message_id), message_ids(in_reply_to)
    return {
        "message_id_header": own[0] if own else None,
        "in_reply_to": parent[0] if parent else None,
        "references": message_ids(references),
    }


def thread_of(
    thread_id: str, details: list[InboundEmailDetail], max_messages: int, *, more: bool = False
) -> EmailThread:
    """``details`` oldest first, keeping the newest ``max_messages``; ``more`` says the
    connector already left some out."""
    ordered = sorted(details, key=lambda detail: detail.received_at_ms)
    kept = ordered[-max_messages:]
    return EmailThread(
        thread_id=thread_id, messages=tuple(kept), truncated=more or len(kept) < len(ordered)
    )


_BLOCK = frozenset(
    {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote"}
)
_HIDDEN = frozenset({"script", "style", "head", "title"})


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _HIDDEN:
            self._hidden += 1
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN:
            self._hidden = max(0, self._hidden - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Readable text from an HTML body: no markup, scripts or styles; blank runs collapsed."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.parts))
    return re.sub(r"\s*\n\s*(\n\s*)+", "\n\n", text).strip()


def bounded_body(text: str, max_chars: int) -> tuple[str, bool]:
    """``text`` cut to ``max_chars`` (and :data:`MAX_BODY_CHARS`), and whether it was cut."""
    limit = min(max_chars, MAX_BODY_CHARS)
    return text[:limit], len(text) > limit
