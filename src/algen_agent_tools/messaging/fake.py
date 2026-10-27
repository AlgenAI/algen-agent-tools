"""An in-memory messaging platform for offline tests of messaging toolkits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from algen_agent_tools._env import SecretResolver
from algen_agent_tools.attachments.models import AttachmentUpload
from algen_agent_tools.messaging.models import (
    Conversation,
    ConversationPage,
    DeliveryReceipt,
    InboundMessage,
    MessagePage,
    MessagingError,
    OutgoingMessage,
)
from algen_agent_tools.messaging.provider import Capability, MessagingClient, MessagingPlatform

FAKE_PLATFORM = MessagingPlatform(
    title="Fake",
    channel="fake",
    capabilities=Capability.CONVERSATIONS
    | Capability.HISTORY
    | Capability.ATTACHMENTS
    | Capability.SEND,
    conversation_pattern=r"^[A-Za-z0-9_-]{1,64}$",
    conversation_required=False,
)
"""A platform with every capability, for toolkits built over :class:`FakeMessaging`."""


def _page(items: tuple[Any, ...], max_results: int, page_token: str | None) -> tuple[Any, ...]:
    start = int(page_token or 0)
    return items[start : start + max_results]


def _next(total: int, max_results: int, page_token: str | None) -> str | None:
    end = int(page_token or 0) + max_results
    return str(end) if end < total else None


@dataclass
class FakeMessaging:
    """Serves conversations and messages and records what is sent.

    A repeated idempotency key returns the first receipt without sending again.
    """

    conversations: list[Conversation] = field(default_factory=list)
    messages: list[InboundMessage] = field(default_factory=list)
    """Newest first, as ``list_messages`` returns them."""
    files: dict[tuple[str, str], AttachmentUpload] = field(default_factory=dict)
    """Attachment content by ``(message id, attachment id)``."""
    sent: list[OutgoingMessage] = field(default_factory=list)
    receipts: dict[str, DeliveryReceipt] = field(default_factory=dict)
    closed: bool = False

    def factory(self, *, allowed_conversations: tuple[str, ...] = ()) -> Any:
        """A toolkit ``client`` over this platform, with ``allowed_conversations`` as if it
        came from the deployment's settings."""

        async def open_client(secrets: SecretResolver) -> MessagingClient:
            return MessagingClient(self, allowed_conversations)

        return open_client

    async def list_conversations(
        self, *, max_results: int, page_token: str | None
    ) -> ConversationPage:
        rows = tuple(self.conversations)
        return ConversationPage(
            conversations=_page(rows, max_results, page_token),
            next_page_token=_next(len(rows), max_results, page_token),
        )

    async def list_messages(
        self, *, conversation_id: str | None, max_results: int, page_token: str | None
    ) -> MessagePage:
        rows = tuple(
            m
            for m in self.messages
            if conversation_id is None or m.conversation.id == conversation_id
        )
        return MessagePage(
            messages=_page(rows, max_results, page_token),
            next_page_token=_next(len(rows), max_results, page_token),
        )

    async def get_attachment(
        self, conversation_id: str, message_id: str, attachment_id: str
    ) -> AttachmentUpload:
        try:
            return self.files[(message_id, attachment_id)]
        except KeyError:
            raise MessagingError("fake attachment not found") from None

    async def send(self, message: OutgoingMessage, *, idempotency_key: str) -> DeliveryReceipt:
        if idempotency_key in self.receipts:
            return self.receipts[idempotency_key]
        self.sent.append(message)
        receipt = DeliveryReceipt(
            provider="fake",
            conversation_id=message.conversation_id,
            message_id=f"fake-{len(self.sent)}",
            sent_at=datetime.now(UTC),
        )
        self.receipts[idempotency_key] = receipt
        return receipt

    async def aclose(self) -> None:
        self.closed = True
