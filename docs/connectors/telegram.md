# Telegram

Read the messages a Telegram bot receives, save their files as artifacts, and post plain text as
the bot, through the Bot API.

| | |
| --- | --- |
| Plugin | `algen_agent_tools.telegram` |
| Extra | `pip install "algen-agent-tools[telegram]"` (no extra packages) |
| Environment | `ALGEN_TOOLS_TELEGRAM_BOT_TOKEN`; optional `ALGEN_TOOLS_TELEGRAM_ALLOWED_CHATS` |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `telegram.list_messages` | read | `telegram.read` | 60 s | Runtime default | agent policy |
| `telegram.get_attachment` (with `artifact_sink`) | read | `telegram.read` | 120 s | Runtime default | agent policy |
| `telegram.send_message` | external | `telegram.write` | 60 s | 1 | agent policy (asks by default) |

These are the shared [messaging tools](#the-messaging-family); Telegram has no
`list_conversations`, because a bot cannot list its chats.

- `telegram.list_messages(conversation_id=None, max_results=25)` returns the messages, channel
  posts and edits sent to the bot, newest first, at most 100: conversation (id, `direct`,
  `group` or `channel`, title), sender, time, text (or a file's caption), the message it replies
  to, and its files (the largest photo size, documents, audio, video, voice and video notes).
  `conversation_id` keeps one chat. There is no next page.
- `telegram.get_attachment(conversation_id, message_id, attachment_id)` saves one file through
  the deployment's `artifact_sink`, within `attachment_policy` (default 20 MB, the Bot API's own
  limit, and content that matches its type), and returns only the artifact reference. The Bot API
  cannot read a message by id, so `attachment_id` (the file id from `list_messages`) is what
  selects the file; the saved name is the one Telegram stores it under.
- `telegram.send_message(conversation_id, text, reply_to_message_id=None)` posts up to 4,096
  characters of plain text: no Markdown or HTML parsing, and **link previews off**, so a link in a
  message cannot make Telegram fetch a URL. The Bot API has no idempotency key; like every
  external action, the Runtime tries it once.

Chat ids are numbers (negative for groups and channels); `@username` is not accepted, so an id
in a listing, the allowlist and a send always compare the same way.

## How reading works

The connector reads the bot's pending updates with `getUpdates` and **never confirms them**:
reading changes nothing, and two agents see the same messages. The trade-offs:

- Telegram keeps an update for 24 hours, and `getUpdates` returns the oldest 100. A bot that
  receives more than 100 messages in a day shows only the oldest of them until they expire.
- `getUpdates` does not work while the bot has a webhook, or while another program polls it;
  the tool fails with `error 409` and says so. Use a bot for this connector alone.

For busy bots, a webhook receiver that stores messages is the right design; it is planned as a
separate inbound component.

## Security

- The bot token is part of every Bot API URL. The connector never puts the token in an error
  (errors carry the operation and an error code), never chains the underlying HTTP exception,
  and redacts it from `httpx`'s request log lines.
- Requests go only to `api.telegram.org` (`api_base_url`); a file path the API returns is
  checked before it is fetched.
- Message text and captions come from people outside the deployment. The read tools say so in
  their descriptions and record no content in audit metadata; keep `send_message` behind
  approval if agents read and reply in one run.
- `ALGEN_TOOLS_TELEGRAM_ALLOWED_CHATS` (or `allowed_chats=` in code) limits every tool to those
  chats: listings drop other chats, and a send or download for another chat is refused.

## Agent configuration

```yaml
agents:
  - name: support-bot
    domain_plugins: [algen_agent_tools.telegram]
    enabled_tools: [telegram.list_messages, telegram.send_message]
    tool_permissions: [telegram.read, telegram.write]
security:
  trusted_plugin_prefixes: [algen_agent_tools]
```

To save files, build the toolkit in code with the deployment's artifact store:

```python
from algen_agent_tools.telegram import telegram_toolkit

toolkit = telegram_toolkit(artifact_sink=save_artifact)  # adds telegram.get_attachment
```

## The messaging family

Telegram is built on `algen_agent_tools.messaging`, which every chat platform shares: the same
models (`InboundMessage`, `Conversation`, `DeliveryReceipt`), the same tool names and inputs,
and the same allowlist. A platform declares its traits once in a `MessagingPlatform` (title,
`Capability` flags, id patterns, text limit) and implements a `MessagingProvider`;
`messaging_toolkit` adds only the tools its capabilities support. `FakeMessaging` serves
offline tests.
