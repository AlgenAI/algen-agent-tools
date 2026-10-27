"""Provider-neutral inbound email: bounded summaries of messages a mailbox connector lists."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class InboundEmailAttachment(BaseModel):
    """Bounded Gmail attachment descriptor; bytes are fetched only on explicit demand."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_attachment_id: str = Field(min_length=1, max_length=2_000)
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="application/octet-stream", min_length=3, max_length=255)
    size: int = Field(default=0, ge=0)


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
