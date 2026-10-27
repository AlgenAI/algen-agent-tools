from __future__ import annotations

from typing import Any

from algen_agent_runtime.tools.authoring import Toolkit
from pydantic import SecretStr

from algen_agent_tools._env import SecretResolver, env_names, resolve_optional
from algen_agent_tools.attachments import ArtifactSink, AttachmentPolicy
from algen_agent_tools.messaging import (
    Capability,
    MessagingClient,
    MessagingPlatform,
    messaging_toolkit,
    parse_conversations,
)
from algen_agent_tools.telegram.client import TelegramBotClient, TelegramSettings

BOT_TOKEN = "env://ALGEN_TOOLS_TELEGRAM_BOT_TOKEN"
ALLOWED_CHATS = "env://ALGEN_TOOLS_TELEGRAM_ALLOWED_CHATS"
DEFAULT_ENV = env_names(BOT_TOKEN, ALLOWED_CHATS)

TELEGRAM = MessagingPlatform(
    title="Telegram",
    channel="telegram",
    capabilities=Capability.HISTORY | Capability.ATTACHMENTS | Capability.SEND,
    conversation_pattern=r"^-?[0-9]{1,20}$",
    message_pattern=r"^[0-9]{1,20}$",
    max_text_chars=4_096,
    max_page_size=100,
    list_messages_note=(
        "Telegram keeps only the messages sent to the bot that have not been confirmed, for up "
        "to 24 hours, and this reads at most the oldest 100 of them."
    ),
    conversation_required=False,
)


def telegram_toolkit(
    name: str = "telegram",
    *,
    bot_token: str = BOT_TOKEN,
    allowed_chats: tuple[str, ...] = (),
    allowed_chats_setting: str | None = ALLOWED_CHATS,
    artifact_sink: ArtifactSink | None = None,
    attachment_policy: AttachmentPolicy | None = None,
    client: Any = None,
) -> Toolkit:
    """A toolkit for one Telegram bot: ``<name>.list_messages`` (read), ``<name>.send_message``
    (external) and, with an ``artifact_sink``, ``<name>.get_attachment`` (read).

    ``bot_token`` is an ``env://`` reference resolved when the client opens. Chats are limited
    to ``allowed_chats`` or, when that is empty, to the comma-separated chat ids in
    ``allowed_chats_setting`` (unset: any chat the bot is in).
    """

    async def open_client(secrets: SecretResolver) -> MessagingClient:
        settings = TelegramSettings(bot_token=SecretStr(await secrets(bot_token)))
        return MessagingClient(
            TelegramBotClient(settings),
            parse_conversations(await resolve_optional(secrets, allowed_chats_setting), TELEGRAM),
        )

    return messaging_toolkit(
        name,
        platform=TELEGRAM,
        client=client or open_client,
        env=env_names(bot_token, allowed_chats_setting),
        allowed_conversations=allowed_chats,
        artifact_sink=artifact_sink,
        attachment_policy=attachment_policy,
    )
