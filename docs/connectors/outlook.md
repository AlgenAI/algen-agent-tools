# Outlook (Microsoft 365)

Read and send mail for one Outlook mailbox through Microsoft Graph, with an Entra app
registration.

| | |
| --- | --- |
| Plugin | `algen_agent_tools.outlook` |
| Extra | `pip install "algen-agent-tools[outlook]"` (no extra packages) |
| Environment | `ALGEN_TOOLS_OUTLOOK_TENANT_ID`, `ALGEN_TOOLS_OUTLOOK_CLIENT_ID`, `ALGEN_TOOLS_OUTLOOK_CLIENT_SECRET`, `ALGEN_TOOLS_OUTLOOK_MAILBOX`; optional `ALGEN_TOOLS_OUTLOOK_REFRESH_TOKEN`, `ALGEN_TOOLS_OUTLOOK_ALLOWED_DOMAINS` |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `outlook.list_messages` | read | `outlook.read` | 120 s | Runtime default | agent policy |
| `outlook.get_message` | read | `outlook.read` | 60 s | Runtime default | agent policy |
| `outlook.get_attachment` (with `artifact_sink`) | read | `outlook.read` | 120 s | Runtime default | agent policy |
| `outlook.send_email` | external | `outlook.write` | 60 s | 1 | agent policy (asks by default) |

- `outlook.list_messages(folder="inbox", search=None, unread_only=False, max_results=25,
  page_token=None)` lists one folder (`inbox`, `sentitems`, `drafts`, `archive`, `junkemail`),
  newest first, at most 50 per page: sender, recipients, subject, a preview of up to 2,000
  characters, categories and attachments. `search` is Outlook search text; `unread_only`
  cannot be combined with it. `next_page_token` is opaque: it carries the folder and Graph's
  paging parameters, never a URL, and the connector rebuilds the request for its own mailbox.
- `outlook.get_message(message_id, max_chars=20000)` and `outlook.get_attachment(message_id,
  attachment_id)` behave as their [Gmail](gmail.md#tools) counterparts; they are the same shared
  tools. Graph returns the body as text on request; an HTML body is converted. Only file
  attachments can be saved (not attached Outlook items or cloud links).
- `outlook.send_email` takes the same input as [`smtp.send_email`](smtp.md) and sends from the
  mailbox, saving a copy in Sent Items. Attachments travel inline, so they total at most 3 MB
  (`max_attachment_bytes`).

## Access modes

- **Delegated** (`ALGEN_TOOLS_OUTLOOK_REFRESH_TOKEN` set): the connector acts as the signed-in
  user on `/me`. Grant the delegated permissions `Mail.Read` (or `Mail.ReadWrite`) and
  `Mail.Send`, plus `offline_access`. Microsoft rotates refresh tokens: the client uses each new
  one for the next refresh; pass `on_refresh_token=` to `OutlookMailboxClient` to store it.
- **Application** (no refresh token): the app's own credentials on the mailbox
  `ALGEN_TOOLS_OUTLOOK_MAILBOX` names. Grant the application permissions `Mail.Read` and
  `Mail.Send`, and **limit the app to that mailbox** with an Exchange Online application access
  policy (or RBAC for Applications); otherwise the app can reach every mailbox in the tenant.
  The connector only ever builds requests for its configured mailbox, but the grant is the
  boundary that matters.

## Agent configuration

```yaml
agents:
  - name: inbox-assistant
    domain_plugins: [algen_agent_tools.outlook]
    enabled_tools: [outlook.list_messages, outlook.get_message, outlook.send_email]
    tool_permissions: [outlook.read, outlook.write]
security:
  trusted_plugin_prefixes: [algen_agent_tools]
```

The [Gmail notes](gmail.md#agent-configuration) on the `pii` guardrail apply unchanged.

## Guarantees

- The [SMTP guarantees](smtp.md#guarantees) for the model's input apply: a fixed sender, no
  custom headers from the model, the domain allowlist.
- Graph returns no id for a send; the receipt's `provider_message_id` is derived from the call's
  idempotency key, which also travels as the `X-Algen-Idempotency-Key` header. Graph does not
  deduplicate on it, so the tool is tried once.
- Requests go only to `login.microsoftonline.com` and `graph.microsoft.com`. Message and
  attachment ids are URL-encoded into one path segment; an id may contain `/` and `+` (Graph
  ids do) but never be `.` or `..`. The tenant id must be an id or domain.
- Errors name the failure type only, never Graph's response.

## In code

```python
from algen_agent_tools.outlook import outlook_toolkit

support = outlook_toolkit(
    "support_inbox",
    mailbox="env://SUPPORT_MAILBOX",
    allowed_domains=("contoso.com",),
    artifact_sink=store,  # enables outlook.get_attachment; see the Gmail page
)
```
