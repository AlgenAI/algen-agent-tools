# Outlook (Microsoft 365)

Read and send mail for one Outlook mailbox through Microsoft Graph, with an Entra app
registration.

| | |
| --- | --- |
| Plugin | `algen_agent_tools.outlook` |
| Extra | `pip install "algen-agent-tools[outlook]"` (`cryptography`, for certificate authentication, from 0.2.0a14; nothing before) |
| Environment | `ALGEN_TOOLS_OUTLOOK_TENANT_ID`, `ALGEN_TOOLS_OUTLOOK_CLIENT_ID`, `ALGEN_TOOLS_OUTLOOK_MAILBOX`; a client secret (`ALGEN_TOOLS_OUTLOOK_CLIENT_SECRET`) or a certificate (`ALGEN_TOOLS_OUTLOOK_PRIVATE_KEY`, optional `ALGEN_TOOLS_OUTLOOK_PRIVATE_KEY_PASSPHRASE`, and `ALGEN_TOOLS_OUTLOOK_CERTIFICATE` or `ALGEN_TOOLS_OUTLOOK_CERTIFICATE_THUMBPRINT`, from 0.2.0a14); optional `ALGEN_TOOLS_OUTLOOK_REFRESH_TOKEN`, `ALGEN_TOOLS_OUTLOOK_ALLOWED_DOMAINS` |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `outlook.list_messages` | read | `outlook.read` | 120 s | Runtime default | agent policy |
| `outlook.get_message` | read | `outlook.read` | 60 s | Runtime default | agent policy |
| `outlook.get_thread` | read | `outlook.read` | 120 s | Runtime default | agent policy |
| `outlook.get_attachment` (with `artifact_sink`) | read | `outlook.read` | 120 s | Runtime default | agent policy |
| `outlook.read_attachment` (with `documents`) | read | `outlook.read` | 120 s | Runtime default | agent policy |
| `outlook.send_email` | external | `outlook.write` | 60 s | 1 | agent policy (asks by default) |

- `outlook.list_messages(folder="inbox", search=None, unread_only=False, max_results=25,
  page_token=None, received_after=None, received_before=None, order="newest_first")` lists one
  folder (`inbox`, `sentitems`, `drafts`, `archive`, `junkemail`), at most 50 per page: sender,
  recipients, subject, a preview of up to 2,000 characters, categories and attachments.
  `search` is Outlook search text; `unread_only` cannot be combined with it. `next_page_token`
  is opaque: it carries the folder and Graph's paging parameters (filter and order included),
  never a URL, and the connector rebuilds the request for its own mailbox.
- **Date window** (0.2.0a14): `received_after` (inclusive) and `received_before` (exclusive) are
  ISO 8601 date-times with a time zone (a naive date-time is refused), sent as
  `$filter=receivedDateTime ge … and receivedDateTime lt …` in UTC, with `isRead eq false`
  appended for `unread_only`. `order` is `newest_first` (`$orderby=receivedDateTime desc`) or
  `oldest_first` (`asc`), so a backfill can page a range oldest to newest. Graph cannot combine
  `$search` with `$filter` or `$orderby` on messages, so `search` with a date or
  `oldest_first` is refused before anything is sent.
- `outlook.list_messages` is `volatile` (0.2.0a13): its result is never served from the Runtime's
  tool-result cache, and the repeat guard never holds back a second identical call, since new
  messages arrive on their own.
- `outlook.get_thread` is `volatile` too (a thread gains replies). `outlook.get_attachment` and
  `outlook.read_attachment` with an artifact sink key cached results on the run (`cache_context`).
- `outlook.get_message(message_id, max_chars=20000)` and `outlook.get_attachment(message_id,
  attachment_id)` behave as their [Gmail](gmail.md#tools) counterparts; they are the same shared
  tools. Graph returns the body as text on request; an HTML body is converted. Only file
  attachments can be saved (not attached Outlook items or cloud links).
- Attachments are listed as for [Gmail](gmail.md#tools): up to 20, with `attachment_count`,
  `attachments_truncated`, `is_inline` (Graph's `isInline`) and a generated name for a nameless
  file. `content_id` is set only if Graph includes `contentId` in the attachment listing, which
  it does not promise (it is a file attachment's property, outside the listing's `$select`). Graph reports `hasAttachments` false for a
  message whose only attachments are inline, so `list_messages` shows none for it while
  `get_message` and `get_thread` always read the attachments. The connector reads at most five
  pages of attachment descriptors; past that `attachments_truncated` is set and
  `attachment_count` is a lower bound.
- `outlook.get_thread(thread_id, max_messages=20, max_chars_per_message=4000)` reads a
  conversation (`conversationId`) across the mailbox's folders, as for
  [Gmail](gmail.md#tools). Graph returns at most 50 of its messages in one call; `truncated`
  says when there were more. In-Reply-To and References come from the message's internet
  headers, which Graph keeps only for received mail.
- `outlook.read_attachment(message_id, attachment_id, max_chars=50000)`, with `documents`, is
  the shared [Gmail](gmail.md#reading-attachments) tool: an attachment's inspected text in one
  call.
- `outlook.send_email` takes the same input as [`smtp.send_email`](smtp.md) and sends from the
  mailbox, saving a copy in Sent Items. Attachments travel inline, so they total at most 3 MB
  (`max_attachment_bytes`).
- With `in_reply_to`, `outlook.send_email` is a reply: it finds the mailbox's message with that
  Message-ID (`internetMessageId`) and sends through Graph's `messages/{id}/reply`, which
  sets In-Reply-To and References from the original and keeps the reply in its conversation.
  Graph takes only `X-` headers on a new message, so `references` is not sent, and
  `references` without `in_reply_to` is refused. A Message-ID not in the mailbox is refused
  before anything is sent.

## Access modes

- **Delegated** (`ALGEN_TOOLS_OUTLOOK_REFRESH_TOKEN` set): the connector acts as the signed-in
  user on `/me`. Grant the delegated permissions `Mail.Read` (or `Mail.ReadWrite`) and
  `Mail.Send`, plus `offline_access`. Microsoft rotates refresh tokens: the client uses each new
  one for the next refresh; pass `on_refresh_token=` to `OutlookMailboxClient` to store it.
- **Application** (no refresh token): the app's own credentials on the mailbox
  `ALGEN_TOOLS_OUTLOOK_MAILBOX` names. Scope the app to that mailbox. The least-privileged way is
  Exchange Online **RBAC for Applications**, with **no** Graph `Mail.Read` application
  permission in Entra (an unscoped one reaches every mailbox in the tenant):

  ```powershell
  New-ServicePrincipal -AppId <client-id> -ObjectId <enterprise-app-object-id> -DisplayName "Careers-Mailbox-Reader"
  New-ManagementScope -Name "Careers mailbox" -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'careers@contoso.com'"
  New-ManagementRoleAssignment -App <client-id> -Role "Application Mail.Read" -CustomResourceScope "Careers mailbox"
  Test-ServicePrincipalAuthorization -Identity <client-id> -Resource careers@contoso.com
  ```

  Add `Application Mail.Send` only if the agent sends. With RBAC for Applications the access
  token's `roles` claim does not contain `Mail.Read`: Exchange authorizes each request, and the
  connector never inspects the token's roles. (The older alternative is the Graph `Mail.Read`
  application permission limited by an application access policy.) The connector only ever
  builds requests for its configured mailbox, but the grant is the boundary that matters.

## Certificate authentication

From 0.2.0a14 the app can authenticate with a certificate instead of a client secret, in either
access mode (delegated refreshes send the assertion too). Set `ALGEN_TOOLS_OUTLOOK_PRIVATE_KEY`
(the PEM private key, RSA, PKCS#8 or PKCS#1; encrypted keys take
`ALGEN_TOOLS_OUTLOOK_PRIVATE_KEY_PASSPHRASE`) and either `ALGEN_TOOLS_OUTLOOK_CERTIFICATE` (the PEM
certificate uploaded to the app registration, preferred) or
`ALGEN_TOOLS_OUTLOOK_CERTIFICATE_THUMBPRINT` (its SHA-1 thumbprint, as the Entra portal shows it),
and leave `ALGEN_TOOLS_OUTLOOK_CLIENT_SECRET` unset: exactly one of the two credentials is
accepted. PEM values may be stored on one line with `\n` escapes (as a secret store or `.env`
file often holds them).

Each token request carries a fresh client assertion as Microsoft documents: a JWT signed RS256,
header `x5t#S256` (the certificate's SHA-256 thumbprint) or `x5t` (from the SHA-1 thumbprint),
claims `aud` (the token endpoint), `iss` and `sub` (the client id), `jti`, `nbf`, `iat` and
`exp` (ten minutes), sent as `client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`
and `client_assertion`. The key is loaded when the client opens (a wrong key, passphrase, a
certificate of another key or a thumbprint that does not match fail then, as a
`ConfigurationError` naming no secret) and never leaves the process. The `outlook` extra installs
`cryptography`; without it a certificate fails with a `ConfigurationError` naming the extra.

In code, pass `certificate=GraphCertificate(private_key=SecretStr(pem), certificate=cert_pem)`
to `OutlookSettings` (`algen_agent_tools.msgraph`). `certificate_info(cert_pem)` returns the
thumbprints, subject and expiry for a status page; it never reads the key.

## Throttling

Graph throttles with `429` (and sometimes `503`) and a `Retry-After`. From 0.2.0a14 a throttled
read raises an `EmailDeliveryError` that is also an `algen_agent_tools.msgraph.GraphThrottled`
with `retry_after_seconds` (bounded to an hour; 10 s when Graph gives none) and is marked not
attempted. The connector does not retry itself: the Runtime's retry policy applies to tool calls,
and a host's batch job (a mailbox backfill) waits `retry_after_seconds` and continues from its
page token.

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
