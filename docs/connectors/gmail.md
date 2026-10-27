# Gmail

Read and send mail for one Gmail or Google Workspace mailbox, through the Gmail API with an
OAuth refresh token.

| | |
| --- | --- |
| Plugin | `algen_agent_tools.gmail` |
| Extra | `pip install "algen-agent-tools[gmail]"` (no extra packages) |
| Environment | `ALGEN_TOOLS_GMAIL_CLIENT_ID`, `ALGEN_TOOLS_GMAIL_CLIENT_SECRET`, `ALGEN_TOOLS_GMAIL_REFRESH_TOKEN`, `ALGEN_TOOLS_GMAIL_SENDER` |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `gmail.list_messages` | read | `gmail.read` | 120 s | Runtime default | agent policy |
| `gmail.send_email` | external | `gmail.write` | 60 s | 1 | agent policy (asks by default) |

- `gmail.list_messages(query="is:unread", max_results=25, page_token=None)` takes a Gmail search
  query and returns message metadata (sender, recipients, subject, a snippet of up to 2,000
  characters, labels, attachment names), at most 100 per page. Message bodies and attachment
  bytes are not returned.
- `gmail.send_email` takes the same input as [`smtp.send_email`](smtp.md) and sends from
  `ALGEN_TOOLS_GMAIL_SENDER`.

With `pii` at the tool boundary (the default), `list_messages` results reach the model with
addresses redacted; leaving it out (below) shows them.

## Credentials

Create an OAuth client in Google Cloud, authorise the mailbox once to obtain a refresh token, and
grant only the scopes the agent needs: `gmail.readonly` for `list_messages`, `gmail.send` for
`send_email`. The access token is refreshed in memory and never leaves the client.

## Agent configuration

Sending works under the default guardrails, as for [SMTP](smtp.md#agent-configuration):
address fields declare `"format": "email"`, so `pii` leaves the recipients alone and still
screens the rest of the message.

```yaml
agents:
  - name: notifier
    domain_plugins: [algen_agent_tools.gmail]
    enabled_tools: [gmail.list_messages, gmail.send_email]
    tool_permissions: [gmail.read, gmail.write]
```

`pii` also redacts tool results, so with the defaults `list_messages` shows the model
`[REDACTED_EMAIL]` in place of each sender and recipient. An agent that must read or reply to
those addresses leaves `pii` out of its tool boundaries:

```yaml
    guardrail_policy:
      tool_policies: [secrets, prompt_injection, output_validation]
```

Keep the agent's approval policy on (the default asks before every external call), and use
`allowed_domains` to bound who it can write to.

## Guarantees

The [SMTP guarantees](smtp.md#guarantees) for sending apply. Token and API errors name only the
failure type, never the response body.

## In code

```python
from algen_agent_tools.gmail import gmail_toolkit

support = gmail_toolkit(
    "support_inbox",
    client_id="env://SUPPORT_GMAIL_CLIENT_ID",
    client_secret="env://SUPPORT_GMAIL_CLIENT_SECRET",
    refresh_token="env://SUPPORT_GMAIL_REFRESH_TOKEN",
    sender="env://SUPPORT_GMAIL_ADDRESS",
)
```

`GmailMailboxClient` (including `download_attachment`) is available for direct use.

## Testing

`FakeMailbox(inbox=[InboundEmailSummary(...)])` serves pages to `list_messages` and records
sends; pass `client=mailbox.factory()`.

## Replaces

The Runtime's `communications.gmail_inbox_tool(mailbox)` and `GmailMailboxClient`. See
[migrating from the Runtime](../migrating-from-runtime.md).
