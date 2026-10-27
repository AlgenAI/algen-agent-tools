# Discord

List one Discord server's text channels, read their messages, save attachments as artifacts,
and post plain text as a bot, through the REST API.

| | |
| --- | --- |
| Plugin | `algen_agent_tools.discord` |
| Extra | `pip install "algen-agent-tools[discord]"` (no extra packages) |
| Environment | `ALGEN_TOOLS_DISCORD_BOT_TOKEN`, `ALGEN_TOOLS_DISCORD_GUILD_ID`; optional `ALGEN_TOOLS_DISCORD_ALLOWED_CHANNELS` |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `discord.list_conversations` | read | `discord.read` | 60 s | Runtime default | agent policy |
| `discord.list_messages` | read | `discord.read` | 60 s | Runtime default | agent policy |
| `discord.get_attachment` (with `artifact_sink`) | read | `discord.read` | 120 s | Runtime default | agent policy |
| `discord.send_message` | external | `discord.write` | 60 s | 1 | agent policy (asks by default) |

These are the shared [messaging tools](telegram.md#the-messaging-family), with the same inputs
as on Telegram.

- `discord.list_conversations(max_results=25, page_token=None)` lists the server's text and
  announcement channels in the server's order.
- `discord.list_messages(conversation_id, max_results=25, page_token=None)` reads one channel
  or thread, newest first, up to 100 per page. `next_page_token` continues with older
  messages.
- `discord.get_attachment(conversation_id, message_id, attachment_id)` reads the message, then
  downloads the attachment from Discord's CDN (without the bot token) through the deployment's
  `artifact_sink`, within `attachment_policy` (default 20 MB; `max_download_bytes` 25 MB).
- `discord.send_message(conversation_id, text, reply_to_message_id=None)` posts up to 2,000
  characters. The message has:
  - **no pings**: `allowed_mentions` is empty, so `@everyone`, roles and users in the text don't
    notify anyone;
  - **no link embeds**, so a link in the text can't make Discord fetch a URL;
  - an **enforced nonce** made from the Runtime's idempotency key, so Discord drops a repeated
    send for a few minutes.

Discord ids are numeric snowflakes. Every id a tool takes must be one.

## The server boundary

The bot can be in many servers. The connector works in **one**: `ALGEN_TOOLS_DISCORD_GUILD_ID`.
Before it reads or writes a channel, it looks the channel up and refuses anything that isn't a
text channel, announcement channel or thread of that server. Direct messages, voice channels
and other servers are all refused. `ALGEN_TOOLS_DISCORD_ALLOWED_CHANNELS` (or
`allowed_channels=` in code) narrows this further to the channels it lists.

## Bot setup

1. Create an application and bot in the Discord developer portal, and copy its token.
2. Invite it to the server with the **View Channels**, **Read Message History**, **Send
   Messages** and (for replies in threads) **Send Messages in Threads** permissions. Grant no
   others.
3. To read message text, turn on the **Message Content** privileged intent. Without it, Discord
   returns empty text except for messages that mention the bot. The tool description says so.

## Security

- The token travels only in the `Authorization` header to `discord.com`, never in a URL or
  to the CDN, and errors carry only the operation and HTTP status.
- Requests go only to `discord.com` and `cdn.discordapp.com`. An attachment URL elsewhere is
  refused before it is fetched.
- Message text is written by people outside the deployment. The read tools say so, and their
  audit metadata records no content. Keep `send_message` behind approval when agents read and
  reply in one run.

## Agent configuration

```yaml
agents:
  - name: community-helper
    domain_plugins: [algen_agent_tools.discord]
    enabled_tools: [discord.list_conversations, discord.list_messages, discord.send_message]
    tool_permissions: [discord.read, discord.write]
security:
  trusted_plugin_prefixes: [algen_agent_tools]
```
