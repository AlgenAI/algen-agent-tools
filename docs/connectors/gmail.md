# Gmail

Read and send mail for one Gmail or Google Workspace mailbox, through the Gmail API with an
OAuth refresh token.

| | |
| --- | --- |
| Plugin | `algen_agent_tools.gmail` |
| Extra | `pip install "algen-agent-tools[gmail]"` (no extra packages) |
| Environment | `ALGEN_TOOLS_GMAIL_CLIENT_ID`, `ALGEN_TOOLS_GMAIL_CLIENT_SECRET`, `ALGEN_TOOLS_GMAIL_REFRESH_TOKEN`, `ALGEN_TOOLS_GMAIL_SENDER`; optional `ALGEN_TOOLS_GMAIL_ALLOWED_DOMAINS` |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `gmail.list_messages` | read | `gmail.read` | 120 s | Runtime default | agent policy |
| `gmail.get_message` | read | `gmail.read` | 60 s | Runtime default | agent policy |
| `gmail.get_attachment` (with `artifact_sink`) | read | `gmail.read` | 120 s | Runtime default | agent policy |
| `gmail.send_email` | external | `gmail.write` | 60 s | 1 | agent policy (asks by default) |

- `gmail.list_messages(query="is:unread", max_results=25, page_token=None)` takes a Gmail search
  query and returns message metadata (sender, recipients, subject, a snippet of up to 2,000
  characters, labels, attachments with their `part_id`), at most 100 per page. Message bodies
  and attachment bytes are not returned.
- `gmail.get_message(message_id, max_chars=20000)` reads one message: the same fields plus `cc`
  and the body as plain text (the text part, or the HTML part with markup, scripts and styles
  removed), cut to `max_chars` (at most 100,000) with `body_truncated` set when cut. Message
  content comes from outside the deployment; the tool description tells the model to treat
  instructions in it as data.
- `gmail.get_attachment(message_id, attachment_id)` is offered only when the toolkit is built
  with an `artifact_sink` (see [Attachments](#attachments)). It saves one attachment as an
  artifact and returns only its reference (`artifact_id`, `filename`, `media_type`), never the
  bytes. Pass the attachment's `part_id` from the listing: Gmail issues a new attachment id on
  every read, while the part id is stable.
- `gmail.send_email` takes the same input as [`smtp.send_email`](smtp.md) and sends from
  `ALGEN_TOOLS_GMAIL_SENDER`.

With `pii` at the tool boundary (the default), `list_messages` results reach the model with
addresses redacted; leaving it out (below) shows them.

## Credentials

Create an OAuth client in Google Cloud, authorise the mailbox once to obtain a refresh token, and
grant only the scopes the agent needs: `gmail.readonly` for `list_messages`, `get_message` and
`get_attachment`, `gmail.send` for `send_email`. The access token is refreshed in memory and never leaves the client.

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

`pii` also redacts tool results, so with the defaults `list_messages` and `get_message` show the model
`[REDACTED_EMAIL]` in place of each sender and recipient. An agent that must read or reply to
those addresses leaves `pii` out of its tool boundaries:

```yaml
    guardrail_policy:
      tool_policies: [secrets, prompt_injection, output_validation]
```

Keep the agent's approval policy on (the default asks before every external call), and use
`allowed_domains` (or the `ALGEN_TOOLS_GMAIL_ALLOWED_DOMAINS` setting, comma-separated) to bound
who it can write to.

## Attachments

`get_attachment` stores files through an `ArtifactSink` the deployment provides: an async
callable that receives the file (`AttachmentUpload`: file name, type and bytes) and the tool
call's `ToolContext`, stores it as an artifact of that tenant (scanning it first if the
deployment scans uploads) and returns an `AttachmentRef`. Before the sink sees a file,
`attachment_policy` (default `AttachmentPolicy()`: at most 20 MB, any type) refuses empty or
oversized files, types outside `media_types` when that is set, and files whose leading bytes do
not match their declared type (a renamed executable is not stored as a PDF). The file name and
type come from the message, not from the model.

```python
from algen_agent_tools.attachments import AttachmentPolicy, AttachmentRef, AttachmentUpload

async def store(upload: AttachmentUpload, context: ToolContext) -> AttachmentRef:
    artifact_id = await artifacts.put(context.tenant_id, upload.filename, upload.data)
    return AttachmentRef(
        artifact_id=artifact_id, filename=upload.filename, media_type=upload.media_type
    )

inbox = gmail_toolkit(
    artifact_sink=store,
    attachment_policy=AttachmentPolicy(media_types=("application/pdf",), max_bytes=10_000_000),
)
```

## Guarantees

The [SMTP guarantees](smtp.md#guarantees) for sending apply. Token and API errors name only the
failure type, never the response body. Message and attachment ids must match
`^[A-Za-z0-9_=-][A-Za-z0-9_.=-]*$` and are URL-encoded into a single path segment, so an id
cannot change which Gmail API path is called. Every request goes only to the configured API and
token hosts.

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

`FakeMailbox(inbox=[InboundEmailSummary(...)])` serves pages to `list_messages`, messages to
`get_message` (with the body of any `InboundEmailDetail` in the inbox), attachments from
`files={(message_id, part_id): AttachmentUpload(...)}` to `get_attachment`, and records sends;
pass `client=mailbox.factory()`.

## Replaces

The Runtime's `communications.gmail_inbox_tool(mailbox)` and `GmailMailboxClient`. See
[migrating from the Runtime](../migrating-from-runtime.md).
