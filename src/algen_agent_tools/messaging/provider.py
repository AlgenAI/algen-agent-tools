"""What a messaging connector implements, and what it declares before any client opens."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from enum import Flag, auto
from typing import Any, Protocol

from algen_agent_runtime.exceptions.errors import ConfigurationError

from algen_agent_tools._env import split_list
from algen_agent_tools.attachments import ID_PATTERN, AttachmentUpload
from algen_agent_tools.messaging.models import (
    ConversationPage,
    DeliveryReceipt,
    MessagePage,
    OutgoingMessage,
)


class Capability(Flag):
    """What a platform's API can do. The toolkit adds only the tools a platform supports."""

    CONVERSATIONS = auto()
    """``list_conversations``: the platform can enumerate the chats the connector reaches."""
    HISTORY = auto()
    """``list_messages``: the platform returns past messages."""
    ATTACHMENTS = auto()
    """``get_attachment``: files on messages can be downloaded."""
    SEND = auto()
    """``send_message``: the connector can post plain text."""


@dataclass(frozen=True)
class MessagingPlatform:
    """A platform's fixed traits, known when the toolkit is built (before secrets resolve).

    The patterns bound every id a tool accepts, so an id cannot change which API path the
    connector calls; each must never match ``.`` or ``..``.
    """

    title: str
    """The platform's display name, used in tool descriptions."""
    channel: str
    """The ``channel`` audit metadata value, such as ``"telegram"``."""
    capabilities: Capability
    conversation_pattern: str
    message_pattern: str = ID_PATTERN
    attachment_pattern: str = ID_PATTERN
    max_text_chars: int = 4_000
    """The longest message ``send_message`` accepts."""
    max_page_size: int = 50
    list_messages_note: str = ""
    """Platform-specific limits of ``list_messages``, appended to its description."""
    conversation_required: bool = True
    """Whether ``list_messages`` must name a conversation."""


class MessagingProvider(Protocol):
    """One platform's client. Methods a platform's capabilities leave out may raise
    ``NotImplementedError``; the toolkit never calls them."""

    async def list_conversations(
        self, *, max_results: int, page_token: str | None
    ) -> ConversationPage: ...

    async def list_messages(
        self, *, conversation_id: str | None, max_results: int, page_token: str | None
    ) -> MessagePage: ...

    async def get_attachment(
        self, conversation_id: str, message_id: str, attachment_id: str
    ) -> AttachmentUpload: ...

    async def send(self, message: OutgoingMessage, *, idempotency_key: str) -> DeliveryReceipt: ...


@dataclass(frozen=True)
class MessagingClient:
    """A messaging toolkit's client: the platform provider and the conversations the
    deployment's settings allow (empty: any the platform lets the connector reach)."""

    provider: Any
    """A :class:`MessagingProvider`."""
    allowed_conversations: tuple[str, ...] = ()

    async def aclose(self) -> None:
        close = getattr(self.provider, "aclose", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result


def parse_conversations(value: str | None, platform: MessagingPlatform) -> tuple[str, ...]:
    """A comma-separated conversation allowlist setting; a malformed id is a configuration
    error."""
    items = split_list(value)
    invalid = [item for item in items if not re.fullmatch(platform.conversation_pattern, item)]
    if invalid:
        raise ConfigurationError(f"allowed {platform.title} conversations are invalid: {invalid}")
    return items
