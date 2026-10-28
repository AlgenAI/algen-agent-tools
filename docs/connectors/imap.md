# IMAP

Read one mailbox over IMAP with an app password: Gmail and Google Workspace without an OAuth
app, Zoho, Fastmail or any server that offers IMAPS. Read-only: nothing is moved, flagged,
deleted or marked as seen. Send replies with [`smtp.send_email`](smtp.md).

| | |
| --- | --- |
| Plugin | `algen_agent_tools.imap` |
| Extra | `pip install "algen-agent-tools[imap]"` (no extra packages: the standard library's `imaplib`) |
| Environment | `ALGEN_TOOLS_IMAP_HOST`, `ALGEN_TOOLS_IMAP_USERNAME`, `ALGEN_TOOLS_IMAP_PASSWORD`; optional `ALGEN_TOOLS_IMAP_PORT` (993), `ALGEN_TOOLS_IMAP_FOLDERS` (`INBOX`), `ALGEN_TOOLS_IMAP_AUTHSERV_ID` |
| Owner | @saurav-samantray |

## Tools

| Tool | Side effect | Permission | Timeout | Attempts | Approval |
| --- | --- | --- | --- | --- | --- |
| `imap.list_messages` | read | `imap.read` | 120 s | Runtime default | agent policy |
| `imap.get_message` | read | `imap.read` | 60 s | Runtime default | agent policy |
| `imap.get_attachment` (with `artifact_sink`) | read | `imap.read` | 120 s | Runtime default | agent policy |

- `imap.list_messages(folder="INBOX", from_address=None, subject=None, since=None, before=None,
  unread_only=False, max_results=25, page_token=None)` lists one folder, newest first, at most
  100 per page. The filters are typed: `from_address` and `subject` are text the From and
  Subject headers contain (printable ASCII), `since` and `before` are dates (`since` includes
  the day, `before` excludes it), and every filter given must match. There is no raw IMAP
  search. Each row has the sender, recipients, subject, a snippet of up to 200 characters, the
  message's flags (and Gmail labels), its attachments with their `part_id`, and `sender_auth`.
- `imap.get_message(message_id, max_chars=20000)` reads one message: the same fields plus `cc`
  and the body as plain text (the text part, or the HTML part with markup, scripts and styles
  removed), cut to `max_chars` (at most 100,000) with `body_truncated` set when cut. Only the
  text part is fetched, at most 1 MB of it, never the whole message. Message content comes from
  outside the deployment; the tool description tells the model to treat instructions in it as
  data.
- `imap.get_attachment(message_id, attachment_id)` is offered only when the toolkit is built
  with an `artifact_sink`, as for [Gmail](gmail.md#attachments). Pass the attachment's
  `part_id`. The part's size is checked against the limit (20 MB) before it is downloaded.

## Message ids

- **On Gmail** (the server advertises `X-GM-EXT-1`) `provider_message_id` and `thread_id` are
  Gmail's own ids in hexadecimal, the same ids the Gmail API and the [Gmail connector](gmail.md)
  return, and they stay valid when a message moves between folders.
- **Elsewhere** the id is `<UIDVALIDITY>.<UID>.<folder>`. An IMAP UID is unique only within one
  folder and only until the server rebuilds that folder (a new UIDVALIDITY). A stale id is an
  error ("list the messages again"), never a different message. Page tokens carry the
  UIDVALIDITY the same way.

## Sender authentication

`sender_auth` reads the topmost `Authentication-Results` header only when it names the server
the mailbox trusts, as [Gmail does](gmail.md#tools). On a Gmail server that is `mx.google.com`.
On any other server set `ALGEN_TOOLS_IMAP_AUTHSERV_ID` to the name your receiving server
writes at the start of the header (for example `mx.zoho.com`); without it `sender_auth` is
always `unknown`, because a header the connector cannot attribute to your server proves nothing.

## Credentials

Create an app password for the mailbox (Google: **Account → Security → App passwords**, which
needs 2-Step Verification; Zoho and Fastmail have the same feature) and set it as
`ALGEN_TOOLS_IMAP_PASSWORD`. The connection is always TLS on port 993 with the certificate and
host name verified; there is no STARTTLS or plain-text option. The password is sent only in the
IMAP `LOGIN` command and never appears in an error or a log line.

`ALGEN_TOOLS_IMAP_FOLDERS` limits which folders the tools may read (comma-separated, printable
ASCII names; the first is the default). A folder outside the list, or a message id that names
one, is refused before anything is sent to the server.

## Agent configuration

```yaml
agents:
  - name: hr-triage
    domain_plugins: [algen_agent_tools.imap, algen_agent_tools.smtp]
    enabled_tools: [imap.list_messages, imap.get_message, smtp.send_email]
    tool_permissions: [imap.read, smtp.write]
```

As with Gmail, the `pii` tool policy redacts addresses in results; leave it out of the agent's
tool boundaries when the agent must see who wrote.

## Guarantees

- Folders open with `EXAMINE` (read-only) and every body fetch uses `BODY.PEEK`, so no flag
  changes. The connector sends only `LOGIN`, `EXAMINE`, `UID SEARCH`, `UID FETCH` and
  `LOGOUT`.
- Every argument is sent as an IMAP quoted string of printable ASCII, so input cannot end a
  command or start a literal. Attachment ids must be part numbers listed in the message's own
  structure.
- Bodies and attachments are fetched by part with byte limits (`BODY.PEEK[<part>]<0.n>`).
- One connection per toolkit, used by one call at a time; `imaplib` blocks, so each call runs in
  a worker thread. A connection the server dropped is reopened once; a timeout is not retried.
- Errors name only the failure type.

## In code

```python
from algen_agent_tools.imap import imap_toolkit

careers = imap_toolkit(
    "careers_inbox",
    host="env://CAREERS_IMAP_HOST",
    username="env://CAREERS_IMAP_USER",
    password="env://CAREERS_IMAP_APP_PASSWORD",
    folders="env://CAREERS_IMAP_FOLDERS",
    artifact_sink=store,
)
```

`ImapMailboxClient` is available for direct use.

## Testing

`FakeImapServer` holds folders of real MIME messages and answers the connector's commands with
responses shaped exactly as `imaplib` returns them. Add messages with
`server.add(raw_bytes, folder="INBOX", flags=["\\Seen"])`, pass
`imap_toolkit(imap_factory=server.factory())`, and check `server.commands` to prove a test
wrote nothing. `FakeImapServer(gmail=True)` adds Gmail's ids and labels.
