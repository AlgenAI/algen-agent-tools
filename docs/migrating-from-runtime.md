# Migrating from the Runtime's built-in connectors

`algen-agent-runtime` 0.1.0a11 and earlier shipped a few connectors in its core. They move here,
and the Runtime deprecates them: each warns on use for one minor release, then is removed.

| Runtime (deprecated) | algen-agent-tools |
| --- | --- |
| `communications.EmailMessage`, `EmailAddress`, `EmailAttachmentReference`, `EmailDeliveryReceipt`, `EmailDeliveryStatus`, `EmailDeliveryError`, `EmailSender` | `algen_agent_tools.mail` (same names) |
| `communications.SmtpEmailSender`, `SmtpSettings` | `algen_agent_tools.smtp` (same names) |
| `communications.email_tool(sender)` | `smtp_toolkit(...)` or `gmail_toolkit(...)`: tool `<name>.send_email` |
| `communications.GmailMailboxClient`, `GmailOAuthSettings`, `GmailMailboxPage`, `InboundEmailSummary`, `InboundEmailAttachment` | `algen_agent_tools.gmail` (same names) |
| `communications.gmail_inbox_tool(mailbox)` | `gmail_toolkit(...)`: tool `<name>.list_messages` |
| `tools.adapters.database_tool(name, handler)` | `PostgresToolkit.query(...)`: one typed tool per query |

`remote_tool` and `mcp_tool` stay in the Runtime: they are its HTTP and MCP adapters.

## Email

The transports (`SmtpEmailSender`, `GmailMailboxClient`) and message models keep their names and
behaviour; change the import.

The tool changes on purpose:

| | Runtime `email_tool` | `<name>.send_email` |
| --- | --- | --- |
| Name | `communications.email.send` | `smtp.send_email` / `gmail.send_email` (or your toolkit name) |
| Permission | `communications.email.send` | `<name>.write` |
| Sender | chosen by the model | fixed by the deployment |
| Custom headers | from the model | not accepted |
| Attachments | always in the schema | only when an `artifact_loader` is configured |
| Timeout | 300 s | 60 s |

Update `enabled_tools`, `tool_permissions` and any approval or guardrail policy that names the
old tool. To keep the old name during a transition, build the toolkit with
`smtp_toolkit("communications_email")` and rename later.

Before:

```python
from algen_agent_runtime.communications import SmtpEmailSender, SmtpSettings, email_tool

container = build_container(settings, additional_tools=[email_tool(SmtpEmailSender(SmtpSettings(...)))])
```

After, with no code at all:

```yaml
agents:
  - name: notifier
    domain_plugins: [algen_agent_tools.smtp]
    enabled_tools: [smtp.send_email]
    tool_permissions: [smtp.write]
security:
  trusted_plugin_prefixes: [algen_agent_tools]
```

and `ALGEN_TOOLS_SMTP_HOST`, `ALGEN_TOOLS_SMTP_SENDER` (plus credentials) in the environment.

## Database

`database_tool` let the model send any statement. Replace each statement the agent really needs
with a declared query; see [PostgreSQL](connectors/postgres.md#declaring-typed-queries). If you
used `read_only=False`, give the write queries a `write()` preset and, where possible,
`idempotency_key=True`.
