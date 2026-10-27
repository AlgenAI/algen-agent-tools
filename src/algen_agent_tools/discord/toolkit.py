from __future__ import annotations

from typing import Any

from algen_agent_runtime.tools.authoring import Toolkit
from pydantic import SecretStr

from algen_agent_tools._env import SecretResolver, env_names, resolve_optional
from algen_agent_tools.attachments import ArtifactSink, AttachmentPolicy
from algen_agent_tools.discord.client import SNOWFLAKE, DiscordBotClient, DiscordSettings
from algen_agent_tools.messaging import (
    Capability,
    MessagingClient,
    MessagingPlatform,
    messaging_toolkit,
    parse_conversations,
)

BOT_TOKEN = "env://ALGEN_TOOLS_DISCORD_BOT_TOKEN"
GUILD_ID = "env://ALGEN_TOOLS_DISCORD_GUILD_ID"
ALLOWED_CHANNELS = "env://ALGEN_TOOLS_DISCORD_ALLOWED_CHANNELS"
DEFAULT_ENV = env_names(BOT_TOKEN, GUILD_ID, ALLOWED_CHANNELS)

DISCORD = MessagingPlatform(
    title="Discord",
    channel="discord",
    capabilities=(
        Capability.CONVERSATIONS | Capability.HISTORY | Capability.ATTACHMENTS | Capability.SEND
    ),
    conversation_pattern=SNOWFLAKE,
    message_pattern=SNOWFLAKE,
    attachment_pattern=SNOWFLAKE,
    max_text_chars=2_000,
    max_page_size=100,
    list_messages_note=(
        "Without the Message Content intent, text is empty except in messages that mention the bot."
    ),
    conversation_required=True,
)


def discord_toolkit(
    name: str = "discord",
    *,
    bot_token: str = BOT_TOKEN,
    guild_id: str = GUILD_ID,
    allowed_channels: tuple[str, ...] = (),
    allowed_channels_setting: str | None = ALLOWED_CHANNELS,
    artifact_sink: ArtifactSink | None = None,
    attachment_policy: AttachmentPolicy | None = None,
    client: Any = None,
) -> Toolkit:
    """A toolkit for one Discord bot in one server: ``<name>.list_conversations`` and
    ``<name>.list_messages`` (read), ``<name>.send_message`` (external) and, with an
    ``artifact_sink``, ``<name>.get_attachment`` (read).

    Every tool is limited to text channels and threads of the server ``guild_id`` names, and
    further to ``allowed_channels`` or, when that is empty, the comma-separated channel ids in
    ``allowed_channels_setting`` (unset: every text channel the bot can see there).
    """

    async def open_client(secrets: SecretResolver) -> MessagingClient:
        settings = DiscordSettings(
            bot_token=SecretStr(await secrets(bot_token)), guild_id=await secrets(guild_id)
        )
        return MessagingClient(
            DiscordBotClient(settings),
            parse_conversations(await resolve_optional(secrets, allowed_channels_setting), DISCORD),
        )

    return messaging_toolkit(
        name,
        platform=DISCORD,
        client=client or open_client,
        env=env_names(bot_token, guild_id, allowed_channels_setting),
        allowed_conversations=allowed_channels,
        artifact_sink=artifact_sink,
        attachment_policy=attachment_policy,
    )
