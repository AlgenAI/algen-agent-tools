# SMTP

Send email over SMTP from the deployment's own address.

| | |
| --- | --- |
| Plugin | `algen_agent_tools.smtp` |
| Extra | `pip install "algen-agent-tools[smtp]"` (no extra packages) |
| Environment | `ALGEN_TOOLS_SMTP_HOST`, `ALGEN_TOOLS_SMTP_SENDER` (required); `ALGEN_TOOLS_SMTP_PORT` (587), `ALGEN_TOOLS_SMTP_SECURITY` (`starttls`), `ALGEN_TOOLS_SMTP_USERNAME`, `ALGEN_TOOLS_SMTP_PASSWORD` |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `smtp.send_email` | external | `smtp.write` | 60 s | 1 | agent policy (asks by default) |

`smtp.send_email` takes `to` (1 to 50), `cc`, `bcc`, `reply_to`, `subject`, and `text_body`
and/or `html_body`. It returns the delivery receipt: provider, Message-ID, accepted and rejected
recipients.

## Guarantees

- **The sender is the deployment's.** The model cannot choose the From address or add headers.
- **Header-safe.** Line breaks in addresses, names or the subject are refused, recipients must be
  unique, and there are at most 100 in all.
- **One message per idempotency key.** The key becomes a deterministic Message-ID and the tool is
  tried once, so a replay does not send twice through the Runtime and a receiving server can
  recognise a duplicate.
- **TLS by default.** `security` is `starttls` or `tls`; `plain` is allowed only to loopback.
- **Content is not captured** in audit metadata, and errors name only the failure type.

## In code

```python
from algen_agent_tools.smtp import smtp_toolkit

notices = smtp_toolkit(
    "notices",
    host="env://NOTICES_SMTP_HOST",
    sender="env://NOTICES_FROM",
    allowed_domains=("example.com",),     # refuse any other recipient domain
    artifact_loader=load_artifact,        # adds an `attachments` field (Runtime artifact refs)
)
```

`SmtpEmailSender` and `SmtpSettings` are available for direct use outside a tool.

## Testing

`algen_agent_tools.mail.FakeMailbox` records sent messages; pass `client=FakeMailbox().factory()`.
Live tests: `ALGEN_TOOLS_LIVE=1 ALGEN_TOOLS_LIVE_RECIPIENT=you@example.com pytest
tests/integration/test_mail_live.py`.

## Replaces

The Runtime's `communications.email_tool(sender)` and `SmtpEmailSender`. See
[migrating from the Runtime](../migrating-from-runtime.md).
