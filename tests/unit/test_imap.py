"""The IMAP connector against an in-memory server that answers as imaplib returns responses."""

from __future__ import annotations

import base64
import imaplib
from datetime import date
from email.message import EmailMessage
from email.policy import SMTP
from typing import Any

import pytest
from algen_agent_runtime.exceptions.errors import ConfigurationError, PolicyDeniedError
from algen_agent_runtime.tools.contracts import SideEffect, Tool, ToolContext
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import SecretStr

from algen_agent_tools.attachments import (
    AttachmentPolicy,
    AttachmentRef,
    AttachmentRefused,
    AttachmentUpload,
)
from algen_agent_tools.imap import FakeImapServer, ImapMailboxClient, ImapSettings, imap_toolkit
from algen_agent_tools.imap.protocol import (
    BodyPart,
    body_parts,
    decode_transfer,
    fetch_responses,
    internaldate_ms,
    parse_value,
    quote,
    search_date,
    text_part,
)
from algen_agent_tools.mail import EmailDeliveryError
from algen_agent_tools.testing import assert_toolkit_contract, plugin_tools

PDF = b"%PDF-1.7 candidate CV"
PASSWORD = "app-password"


def mime(
    *,
    sender: str = "Ana <ana@example.com>",
    subject: str = "Application",
    plain: str | None = "Hello,\nplease find my CV attached.",
    html: str | None = None,
    attachment: tuple[str, bytes] | None = ("cv.pdf", PDF),
    auth: tuple[str, ...] = (),
) -> bytes:
    message = EmailMessage()
    for header in auth:
        message["Authentication-Results"] = header
    message["From"] = sender
    message["To"] = "hr@example.com"
    message["Cc"] = "team@example.com"
    message["Subject"] = subject
    if plain is not None:
        message.set_content(plain)
        if html is not None:
            message.add_alternative(html, subtype="html")
    elif html is not None:
        message.set_content(html, subtype="html")
    if attachment is not None:
        name, data = attachment
        message.add_attachment(data, maintype="application", subtype="pdf", filename=name)
    return message.as_bytes(policy=SMTP)


def settings(**changes: Any) -> ImapSettings:
    values: dict[str, Any] = {
        "host": "imap.example.com",
        "username": "agent@example.com",
        "password": SecretStr(PASSWORD),
        **changes,
    }
    return ImapSettings(**values)


def client(server: FakeImapServer, **changes: Any) -> ImapMailboxClient:
    return ImapMailboxClient(settings(**changes), imap_factory=server.factory())


def configure(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    defaults = {
        "HOST": "imap.example.com",
        "USERNAME": "agent@example.com",
        "PASSWORD": PASSWORD,
    }
    for name in ("PORT", "FOLDERS", "AUTHSERV_ID"):
        monkeypatch.delenv(f"ALGEN_TOOLS_IMAP_{name}", raising=False)
    for name, value in {**defaults, **values}.items():
        monkeypatch.setenv(f"ALGEN_TOOLS_IMAP_{name}", value)


def named(toolkit: Any, action: str) -> Tool:
    return next(t for t in toolkit.tools if t.definition.name == f"imap.{action}")


async def call(toolkit: Any, action: str, **arguments: Any) -> Any:
    tool = named(toolkit, action)
    return await tool.execute(arguments, fake_tool_context(tool))


# Protocol


def test_bodystructure_follows_the_rfc_3501_example() -> None:
    structure = parse_value(
        b'(("TEXT" "PLAIN" ("CHARSET" "US-ASCII") NIL NIL "7BIT" 1152 23)("TEXT" "PLAIN" '
        b'("CHARSET" "US-ASCII" "NAME" "cc.diff") "<960723163407.20117h@cac.washington.edu>" '
        b'"Compiled diff" "BASE64" 4554 73) "MIXED")'
    )
    first, second = body_parts(structure)
    assert (first.section, first.media_type, first.size, first.filename) == (
        "1",
        "text/plain",
        1152,
        None,
    )
    assert (second.section, second.encoding, second.filename) == ("2", "base64", "cc.diff")
    assert text_part([first, second]) is first
    [single] = body_parts(
        parse_value(b'("TEXT" "PLAIN" ("CHARSET" "US-ASCII") NIL NIL "7BIT" 3 1)')
    )
    assert single.section == "1"


def test_fetch_responses_rebuild_imaplib_literals() -> None:
    data = [
        (b"1 (UID 5 BODY[HEADER.FIELDS (FROM TO)] {11}", b"From: a\r\n\r\n"),
        b' FLAGS (\\Seen) INTERNALDATE " 7-Jul-1996 02:44:25 -0700")',
        b"2 (UID 6 FLAGS ())",
    ]
    first, second = fetch_responses(data)
    assert first["UID"] == "5"
    assert first["BODY[HEADER.FIELDS (FROM TO)]"] == b"From: a\r\n\r\n"
    assert first["FLAGS"] == ["\\Seen"]
    assert internaldate_ms(first["INTERNALDATE"]) == 836732665000
    assert second == {"UID": "6", "FLAGS": []}
    with pytest.raises(ValueError, match="size marker"):
        fetch_responses([(b"1 (UID 5 BODY[]", b"x")])


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"filename": "../../etc/passwd"}, "passwd"),
        ({"filename*": "utf-8''r%C3%A9sum%C3%A9.pdf"}, "résumé.pdf"),
        ({"filename*0*": "utf-8''r%C3%A9", "filename*1": "sume.pdf"}, "résume.pdf"),
        ({"filename": "=?UTF-8?B?w6l0w6kucGRm?="}, "été.pdf"),
        ({"filename": "a\r\nb.pdf"}, "ab.pdf"),
        ({"filename": ".."}, None),
    ],
)
def test_file_names_are_decoded_without_paths(params: dict[str, str], expected: str) -> None:
    part = BodyPart("2", "application/pdf", "base64", 10, disposition_params=params)
    assert part.filename == expected


def test_arguments_are_quoted_printable_ascii_only() -> None:
    assert quote('a "b" \\c') == '"a \\"b\\" \\\\c"'
    for value in ("a\r\nb", "x\x00", "café", "{5}\r\n"):
        with pytest.raises(ValueError, match="printable ASCII"):
            quote(value)
    assert search_date(date(2026, 3, 7)) == "7-Mar-2026"


def test_a_cut_base64_body_decodes_up_to_its_last_whole_group() -> None:
    encoded = base64.b64encode(b"hello world, hello")
    assert decode_transfer(encoded, "base64") == b"hello world, hello"
    assert decode_transfer(encoded[:10], "base64") == b"hello "
    assert decode_transfer(b"caf=C3=A9", "quoted-printable") == "café".encode()


# Tools


def test_the_plugin_describes_read_only_tools_and_its_settings() -> None:
    tools = plugin_tools("algen_agent_tools.imap")
    assert {name: tool.side_effect for name, tool in tools.items()} == {
        "imap.get_message": SideEffect.READ,
        "imap.list_messages": SideEffect.READ,
    }
    assert {
        "ALGEN_TOOLS_IMAP_HOST",
        "ALGEN_TOOLS_IMAP_USERNAME",
        "ALGEN_TOOLS_IMAP_PASSWORD",
        "ALGEN_TOOLS_IMAP_FOLDERS",
    } <= set(tools["imap.list_messages"].env)
    toolkit = imap_toolkit(artifact_sink=lambda *a: None)  # type: ignore[arg-type]
    assert_toolkit_contract(toolkit)
    assert named(toolkit, "get_attachment").definition.side_effect is SideEffect.READ
    schema = named(toolkit, "list_messages").definition.input_schema
    assert "query" not in schema["properties"]
    assert set(schema["properties"]) == {
        "folder",
        "from_address",
        "subject",
        "since",
        "before",
        "unread_only",
        "max_results",
        "page_token",
    }


async def test_list_messages_pages_newest_first_without_marking_anything_seen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeImapServer()
    first = server.add(mime(subject="First"), internaldate="01-Oct-2026 09:00:00 +0000")
    server.add(mime(subject="Second", attachment=None), flags=["\\Seen"])
    server.add(mime(subject="Third", plain=None, html="<p>Hi <b>there</b></p><script>x</script>"))
    configure(monkeypatch)
    toolkit = imap_toolkit(imap_factory=server.factory())

    page = await call(toolkit, "list_messages", max_results=2)
    assert [m["subject"] for m in page["messages"]] == ["Third", "Second"]
    assert page["messages"][0]["snippet"] == "Hi there"
    assert page["result_size_estimate"] == 3
    rest = await call(toolkit, "list_messages", max_results=2, page_token=page["next_page_token"])
    [row] = rest["messages"]
    assert rest["next_page_token"] is None
    assert row["subject"] == "First"
    assert row["sender"] == "Ana <ana@example.com>"
    assert row["received_at_ms"] == 1790845200000
    assert row["snippet"] == "Hello, please find my CV attached."
    assert row["sender_auth"] == "unknown"
    assert row["provider_message_id"] == "1000.1.SU5CT1g"
    [attachment] = row["attachments"]
    # The size is estimated from the encoded size, line breaks included.
    assert abs(attachment.pop("size") - len(PDF)) <= 4
    assert attachment == {
        "provider_attachment_id": "2",
        "filename": "cv.pdf",
        "media_type": "application/pdf",
        "part_id": "2",
    }

    assert first.flags == []
    # Only reads: folders open read-only (EXAMINE) and bodies are fetched with BODY.PEEK.
    for command in server.commands:
        assert command == "LOGIN" or command.startswith(
            ('EXAMINE "INBOX"', "UID SEARCH ", "UID FETCH ")
        ), command
        assert "BODY[" not in command, command
    assert server.connections == 1
    await toolkit.aclose()
    assert server.logged_out == 1


async def test_filters_are_typed_and_sent_as_quoted_search_keys() -> None:
    server = FakeImapServer()
    server.add(mime(sender="Ana <ana@example.com>", subject="CV"), flags=["\\Seen"])
    server.add(
        mime(sender="Bo <bo@example.com>", subject="CV"), internaldate="05-Oct-2026 10:00:00 +0000"
    )
    server.add(mime(sender="Bo <bo@example.com>", subject="Other"))
    mailbox = client(server)
    page = await mailbox.list_messages(
        from_address="bo@example.com",
        subject="CV",
        since=date(2026, 10, 2),
        before=date(2026, 10, 6),
        unread_only=True,
    )
    assert [m.subject for m in page.messages] == ["CV"]
    assert (
        'UID SEARCH FROM "bo@example.com" SUBJECT "CV" SINCE 2-Oct-2026 BEFORE 6-Oct-2026 UNSEEN'
        in server.commands
    )


async def test_search_text_cannot_break_out_of_its_argument(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeImapServer()
    configure(monkeypatch)
    toolkit = imap_toolkit(imap_factory=server.factory())
    with pytest.raises(Exception, match="pattern"):
        await call(toolkit, "list_messages", subject='x" OR ALL\r\nA1 DELETE "INBOX')
    with pytest.raises(EmailDeliveryError, match="page token"):
        await client(server).list_messages(page_token="1 OR ALL")


async def test_only_configured_folders_are_read() -> None:
    server = FakeImapServer()
    server.add(mime(subject="hr"), folder="Candidates")
    server.add(mime(subject="secret"), folder="Payroll")
    mailbox = client(server, folders=("INBOX", "Candidates"))
    [row] = (await mailbox.list_messages(folder="Candidates")).messages
    assert row.subject == "hr"
    assert (await mailbox.list_messages(folder="inbox")).messages == ()
    with pytest.raises(PolicyDeniedError, match="Payroll"):
        await mailbox.list_messages(folder="Payroll")
    payroll_id = f"{server.uidvalidity['Payroll']}.1.UGF5cm9sbA"
    with pytest.raises(PolicyDeniedError):
        await mailbox.get_message(payroll_id)
    assert not any("Payroll" in command for command in server.commands)


async def test_get_message_returns_the_text_body_bounded() -> None:
    server = FakeImapServer()
    server.add(mime(plain=None, html="<p>Dear team,</p><p>" + "word " * 2_000 + "</p>"))
    mailbox = client(server)
    [row] = (await mailbox.list_messages()).messages
    detail = await mailbox.get_message(row.provider_message_id, max_chars=200)
    assert detail.cc == "team@example.com"
    assert detail.text_body.startswith("Dear team,\n\nword word")
    assert len(detail.text_body) == 200
    assert detail.body_truncated
    whole = await mailbox.get_message(row.provider_message_id, max_chars=100_000)
    assert not whole.body_truncated
    assert whole.text_body.endswith("word")


async def test_ids_go_stale_when_the_server_rebuilds_the_folder() -> None:
    server = FakeImapServer()
    server.add(mime())
    mailbox = client(server)
    [row] = (await mailbox.list_messages()).messages
    page_token = "1000.5"
    server.uidvalidity["INBOX"] = 2_000
    with pytest.raises(EmailDeliveryError, match="stale"):
        await mailbox.get_message(row.provider_message_id)
    with pytest.raises(EmailDeliveryError, match="stale"):
        await mailbox.list_messages(page_token=page_token)
    for message_id in ("not-an-id", "18c0000000000000", "2000.9.SU5CT1g"):
        with pytest.raises(EmailDeliveryError):
            await mailbox.get_message(message_id)


async def test_gmail_ids_labels_and_sender_verdict() -> None:
    server = FakeImapServer(gmail=True)
    genuine = "mx.google.com; dkim=pass; spf=pass; dmarc=pass header.from=example.com"
    forged = "mx.google.com; dmarc=pass"
    server.add(mime(auth=(genuine,)), gmail_thread_id=0xABC, gmail_labels=("\\Inbox", "Hiring"))
    # Only the topmost header counts: one the sender added lower down is ignored.
    server.add(mime(auth=("mx.google.com; spf=fail; dkim=none; dmarc=fail", forged)))
    mailbox = client(server)
    second, first = (await mailbox.list_messages()).messages
    assert first.provider_message_id == "18c0000000000000"
    assert first.thread_id == "abc"
    assert first.labels == ("\\Inbox", "Hiring")
    assert first.sender_auth == "pass"
    assert second.sender_auth == "fail"
    detail = await mailbox.get_message("18c0000000000000")
    assert detail.subject == "Application"
    assert "UID SEARCH X-GM-MSGID 1783425452438716416" in server.commands


async def test_the_trusted_server_is_configurable_and_never_assumed() -> None:
    header = "mx.zoho.com; dkim=pass; spf=pass; dmarc=pass"
    server = FakeImapServer()
    server.add(mime(auth=(header,)))
    [row] = (await client(server).list_messages()).messages
    assert row.sender_auth == "unknown"
    [row] = (await client(server, authserv_id="mx.zoho.com").list_messages()).messages
    assert row.sender_auth == "pass"


async def test_get_attachment_saves_one_part_through_the_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeImapServer()
    server.add(mime(attachment=("résumé.pdf", PDF)))
    stored: list[AttachmentUpload] = []

    async def sink(upload: AttachmentUpload, context: ToolContext) -> AttachmentRef:
        stored.append(upload)
        return AttachmentRef(
            artifact_id="artifact-1", filename=upload.filename, media_type=upload.media_type
        )

    configure(monkeypatch)
    toolkit = imap_toolkit(imap_factory=server.factory(), artifact_sink=sink)
    page = await call(toolkit, "list_messages")
    [row] = page["messages"]
    reference = await call(
        toolkit, "get_attachment", message_id=row["provider_message_id"], attachment_id="2"
    )
    assert reference["artifact_id"] == "artifact-1"
    assert stored == [AttachmentUpload("résumé.pdf", "application/pdf", PDF)]
    for attachment_id in ("1", "9", "2]<0.1> BODY[1"):
        with pytest.raises(Exception, match=r"no such attachment|pattern"):
            await call(
                toolkit,
                "get_attachment",
                message_id=row["provider_message_id"],
                attachment_id=attachment_id,
            )
    assert not any("BODY[" in command for command in server.commands)

    policy = imap_toolkit(
        imap_factory=server.factory(),
        artifact_sink=sink,
        attachment_policy=AttachmentPolicy(media_types=("image/png",)),
    )
    with pytest.raises(AttachmentRefused):
        await call(
            policy, "get_attachment", message_id=row["provider_message_id"], attachment_id="2"
        )


async def test_attachments_over_the_limit_are_refused_before_download() -> None:
    server = FakeImapServer()
    server.add(mime(attachment=("big.pdf", b"%PDF" + b"x" * 5_000)))
    mailbox = client(server, max_attachment_bytes=1_000)
    [row] = (await mailbox.list_messages()).messages
    with pytest.raises(EmailDeliveryError, match="size limit"):
        await mailbox.get_attachment(row.provider_message_id, "2")
    assert not any("BODY.PEEK[2]" in command for command in server.commands)


async def test_a_dropped_connection_is_reopened_once() -> None:
    server = FakeImapServer()
    server.add(mime())
    mailbox = client(server)
    await mailbox.list_messages()
    server.drop_next = True
    [row] = (await mailbox.list_messages()).messages
    assert row.subject == "Application"
    assert server.connections == 2


async def test_failures_name_only_the_cause_type() -> None:
    server = FakeImapServer(password="other")
    with pytest.raises(EmailDeliveryError) as caught:
        await client(server).list_messages()
    assert str(caught.value) == "IMAP inbox read failed (error)"
    assert PASSWORD not in repr(caught.value.__cause__)


async def test_the_client_opens_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    server = FakeImapServer()
    server.add(mime(), folder="Candidates")
    configure(monkeypatch, FOLDERS="Candidates, INBOX", PORT="993", AUTHSERV_ID="mx.zoho.com")
    toolkit = imap_toolkit(imap_factory=server.factory())
    page = await call(toolkit, "list_messages", folder="Candidates")
    assert len(page["messages"]) == 1
    reader = await toolkit.client()
    assert reader.transport._settings.folders == ("Candidates", "INBOX")
    assert reader.transport._settings.authserv_id == "mx.zoho.com"
    await toolkit.aclose()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"PORT": "imaps"}, "port must be a number"),
        ({"HOST": "imap.example.com\r\nX"}, "line breaks"),
        ({"FOLDERS": "INBOX,Café"}, "printable ASCII"),
        ({"FOLDERS": "INBOX,inbox"}, "unique"),
    ],
)
async def test_invalid_settings_are_configuration_errors_that_hide_values(
    monkeypatch: pytest.MonkeyPatch, values: dict[str, str], message: str
) -> None:
    configure(monkeypatch, **values)
    toolkit = imap_toolkit(imap_factory=FakeImapServer().factory())
    with pytest.raises(ConfigurationError, match=message) as caught:
        await toolkit.client()
    assert PASSWORD not in str(caught.value)


def test_the_tls_connection_verifies_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: dict[str, Any] = {}

    class Recorder:
        def __init__(self, host: str, port: int, **options: Any) -> None:
            opened.update(host=host, port=port, **options)

    monkeypatch.setattr(imaplib, "IMAP4_SSL", Recorder)
    from algen_agent_tools.imap.client import _open_tls

    _open_tls(settings(port=1993, timeout_seconds=12))
    assert (opened["host"], opened["port"], opened["timeout"]) == ("imap.example.com", 1993, 12)
    context = opened["ssl_context"]
    assert context.check_hostname and context.verify_mode.name == "CERT_REQUIRED"


async def test_a_forwarded_email_is_one_attachment() -> None:
    inner = EmailMessage()
    inner["From"] = "cto@example.com"
    inner["Subject"] = "Referral"
    inner.set_content("Please consider this candidate.")
    outer = EmailMessage()
    outer["From"] = "ana@example.com"
    outer["Subject"] = "Fwd: Referral"
    outer.set_content("See below.")
    outer.add_attachment(inner)
    server = FakeImapServer()
    server.add(outer.as_bytes(policy=SMTP))
    mailbox = client(server)
    [row] = (await mailbox.list_messages()).messages
    assert [(a.part_id, a.filename, a.media_type) for a in row.attachments] == [
        ("2", "message.eml", "message/rfc822")
    ]
    upload = await mailbox.get_attachment(row.provider_message_id, "2")
    assert b"Please consider this candidate." in upload.data
    assert (await mailbox.get_message(row.provider_message_id)).text_body == "See below."
