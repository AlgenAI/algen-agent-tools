from algen_agent_tools._env import env_name
from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.catalog import ConnectorInfo, Egress, Setting
from algen_agent_tools.discord.toolkit import (
    ALLOWED_CHANNELS,
    BOT_TOKEN,
    GUILD_ID,
    discord_toolkit,
)

INFO = ConnectorInfo(
    connector="discord",
    title="Discord",
    summary=(
        "List a Discord server's text channels, read their messages, save attachments as "
        "artifacts, and post plain-text messages as a bot."
    ),
    category="messaging",
    extra="discord",
    settings=(
        Setting(
            env=env_name(BOT_TOKEN),
            description="The bot's token from the Discord developer portal.",
            secret=True,
        ),
        Setting(
            env=env_name(GUILD_ID),
            description="The id of the one server (guild) the connector works in.",
            example="123456789012345678",
        ),
        Setting(
            env=env_name(ALLOWED_CHANNELS),
            description=(
                "Comma-separated channel ids the bot may read and write. Unset: every text "
                "channel of the server the bot can see."
            ),
            required=False,
            example="123456789012345678,234567890123456789",
        ),
    ),
    egress=(
        Egress(protocol="https", host="discord.com", port=443),
        Egress(protocol="https", host="cdn.discordapp.com", port=443),
    ),
    code_only_options=("artifact_sink", "attachment_policy"),
)

DiscordPlugin = toolkit_plugin(INFO, discord_toolkit)
