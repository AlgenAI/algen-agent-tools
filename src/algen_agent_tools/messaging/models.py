"""Chat messages as every messaging connector returns and sends them."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ConversationKind = Literal["direct", "group", "channel"]


class MessagingError(RuntimeError):
    """A messaging platform refused or failed a request; the message is content-minimal."""


class Participant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(max_length=256)
    display_name: str | None = Field(default=None, max_length=512)
    username: str | None = Field(default=None, max_length=256)
    is_bot: bool = False


class Conversation(BaseModel):
    """A chat, group or channel the connector can read or write."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(max_length=256)
    kind: ConversationKind
    title: str | None = Field(default=None, max_length=512)


class ConversationPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    conversations: tuple[Conversation, ...] = ()
    next_page_token: str | None = None


class MessageAttachment(BaseModel):
    """A file on a message; ``get_attachment`` saves it as an artifact by ``attachment_id``."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    attachment_id: str = Field(max_length=2_000)
    filename: str = Field(max_length=255)
    media_type: str = Field(max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)


class InboundMessage(BaseModel):
    """One received message. Its text comes from outside the deployment and is untrusted."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    message_id: str = Field(max_length=256)
    conversation: Conversation
    sender: Participant | None = None
    sent_at: datetime
    text: str = Field(default="", max_length=20_000)
    edited: bool = False
    reply_to_message_id: str | None = Field(default=None, max_length=256)
    attachments: tuple[MessageAttachment, ...] = ()


class MessagePage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    messages: tuple[InboundMessage, ...] = ()
    next_page_token: str | None = None


class OutgoingMessage(BaseModel):
    """A plain-text message to one conversation, as the model supplied it."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    conversation_id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=20_000)
    reply_to_message_id: str | None = Field(default=None, max_length=256)


class DeliveryReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    conversation_id: str
    message_id: str
    sent_at: datetime | None = None
