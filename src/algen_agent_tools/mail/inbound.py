"""Provider-neutral inbound email: bounded summaries and bodies of messages a mailbox connector
reads."""

from __future__ import annotations

import re
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_BODY_CHARS = 100_000


class InboundEmailAttachment(BaseModel):
    """Bounded Gmail attachment descriptor; bytes are fetched only on explicit demand."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_attachment_id: str = Field(min_length=1, max_length=2_000)
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="application/octet-stream", min_length=3, max_length=255)
    size: int = Field(default=0, ge=0)
    part_id: str | None = Field(default=None, max_length=64)
    """The attachment's stable place in the message; pass it to ``get_attachment``."""


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
    provider_message_id: str
    thread_id: str | None = None
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


class InboundEmailDetail(InboundEmailSummary):
    """One message with its body as bounded plain text."""

    cc: str = ""
    cc_addresses: tuple[Address, ...] = Field(default=(), max_length=100)
    """The cc'd addresses alone, read from ``cc``."""
    text_body: str = Field(default="", max_length=MAX_BODY_CHARS)
    body_truncated: bool = False


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
