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

## Credentials

Create an OAuth client in Google Cloud, authorise the mailbox once to obtain a refresh token, and
grant only the scopes the agent needs: `gmail.readonly` for `list_messages`, `gmail.send` for
`send_email`. The access token is refreshed in memory and never leaves the client.

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
