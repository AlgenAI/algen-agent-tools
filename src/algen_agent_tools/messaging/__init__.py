"""Chat and messaging platforms: one model and one tool surface (``list_conversations``,
``list_messages``, ``get_attachment``, ``send_message``) that every platform connector shares."""

from algen_agent_tools.messaging.fake import FAKE_PLATFORM, FakeMessaging
from algen_agent_tools.messaging.models import (
    Conversation,
    ConversationKind,
    ConversationPage,
    DeliveryReceipt,
    InboundMessage,
    MessageAttachment,
    MessagePage,
    MessagingError,
    OutgoingMessage,
    Participant,
)
from algen_agent_tools.messaging.provider import (
    Capability,
    MessagingClient,
    MessagingPlatform,
    MessagingProvider,
    parse_conversations,
)
from algen_agent_tools.messaging.toolkit import messaging_toolkit

__all__ = [
    "FAKE_PLATFORM",
    "Capability",
    "Conversation",
    "ConversationKind",
    "ConversationPage",
    "DeliveryReceipt",
    "FakeMessaging",
    "InboundMessage",
    "MessageAttachment",
    "MessagePage",
    "MessagingClient",
    "MessagingError",
    "MessagingPlatform",
    "MessagingProvider",
    "OutgoingMessage",
    "Participant",
    "messaging_toolkit",
    "parse_conversations",
]
