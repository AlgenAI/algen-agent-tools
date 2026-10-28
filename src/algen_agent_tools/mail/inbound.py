"""Provider-neutral inbound email: bounded summaries and bodies of messages a mailbox connector
reads."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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


class InboundEmailSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_message_id: str
    thread_id: str | None = None
    sender: str = ""
    recipients: str = ""
    subject: str = ""
    received_at_ms: int = Field(default=0, ge=0)
    snippet: str = Field(default="", max_length=2_000)
    labels: tuple[str, ...] = ()
    attachments: tuple[InboundEmailAttachment, ...] = ()
    sender_auth: Literal["pass", "fail", "unknown"] = "unknown"
    """The receiving server's SPF/DKIM/DMARC verdict on the sender, when the connector can read
    it; see :mod:`algen_agent_tools.mail.sender_auth`. ``unknown`` is never a failure."""


class InboundEmailDetail(InboundEmailSummary):
    """One message with its body as bounded plain text."""

    cc: str = ""
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
