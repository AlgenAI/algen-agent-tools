from algen_agent_tools._env import env_name
from algen_agent_tools._plugin import toolkit_plugin
from algen_agent_tools.catalog import ConnectorInfo, Egress, Setting
from algen_agent_tools.telegram.toolkit import ALLOWED_CHATS, BOT_TOKEN, telegram_toolkit

INFO = ConnectorInfo(
    connector="telegram",
    title="Telegram",
    summary=(
        "Read the messages a Telegram bot receives, save their files as artifacts, and post "
        "plain-text messages as the bot."
    ),
    category="messaging",
    extra="telegram",
    settings=(
        Setting(
            env=env_name(BOT_TOKEN),
            description="The bot's token from @BotFather.",
            secret=True,
        ),
        Setting(
            env=env_name(ALLOWED_CHATS),
            description=(
                "Comma-separated chat ids the bot may read and write. Unset: any chat the bot "
                "is in."
            ),
            required=False,
            example="123456789,-1001234567890",
        ),
    ),
    egress=(Egress(protocol="https", host="api.telegram.org", port=443),),
    code_only_options=("artifact_sink", "attachment_policy"),
)

TelegramPlugin = toolkit_plugin(INFO, telegram_toolkit)
