"""IMAP body search, Gmail raw queries, special-use folders and imap.list_folders."""

from __future__ import annotations

from email.message import EmailMessage
from email.policy import SMTP
from typing import Any

import pytest
from algen_agent_runtime.exceptions.errors import PolicyDeniedError
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import SecretStr, ValidationError

from algen_agent_tools.imap import FakeImapServer, ImapMailboxClient, ImapSettings, imap_toolkit


def mime(subject: str, body: str) -> bytes:
    message = EmailMessage()
    message["From"] = "ana@example.com"
    message["To"] = "hr@example.com"
    message["Subject"] = subject
    message["Message-ID"] = f"<{subject.replace(' ', '-')}@example.com>"
    message.set_content(body)
    return message.as_bytes(policy=SMTP)


def client(server: FakeImapServer, *folders: str) -> ImapMailboxClient:
    return ImapMailboxClient(
        ImapSettings(
            host="imap.example.com",
            username=server.username,
            password=SecretStr(server.password),
            folders=folders or ("INBOX",),
        ),
        imap_factory=server.factory(),
    )


def gmail_server() -> FakeImapServer:
    server = FakeImapServer(
        gmail=True,
        folders={"INBOX": [], "[Gmail]/Sent Mail": [], "[Gmail]/All Mail": []},
        special_use={"[Gmail]/Sent Mail": "\\Sent", "[Gmail]/All Mail": "\\All"},
    )
    server.add(mime("Invoice", "Total due 42"))
    server.add(mime("Lunch", "Pizza on Friday"))
    server.add(mime("Reply", "Thanks for the invoice"), folder="[Gmail]/Sent Mail")
    server.add(mime("Old", "Archived offer letter"), folder="[Gmail]/All Mail")
    return server


async def test_text_contains_searches_headers_and_body() -> None:
    server = gmail_server()
    page = await client(server).list_messages(text_contains="pizza")
    assert [m.subject for m in page.messages] == ["Lunch"]
    assert any('TEXT "pizza"' in command for command in server.commands)


async def test_gmail_query_is_sent_as_x_gm_raw_on_gmail() -> None:
    server = gmail_server()
    page = await client(server).list_messages(gmail_query="total due")
    assert [m.subject for m in page.messages] == ["Invoice"]
    assert any('X-GM-RAW "total due"' in command for command in server.commands)


async def test_gmail_query_is_refused_on_other_servers() -> None:
    server = FakeImapServer()
    server.add(mime("Invoice", "Total due 42"))
    with pytest.raises(PolicyDeniedError, match="Gmail server"):
        await client(server).list_messages(gmail_query="total")
    assert not any("SEARCH" in command for command in server.commands)


async def test_special_use_names_resolve_to_the_servers_folders() -> None:
    server = gmail_server()
    mailbox = client(server, "INBOX", "\\Sent")

    page = await mailbox.list_messages(folder="\\Sent")

    assert [m.subject for m in page.messages] == ["Reply"]
    assert any(command.startswith("LIST") for command in server.commands)
    # The real name works too, and the message id names the real folder.
    again = await mailbox.list_messages(folder="[Gmail]/Sent Mail")
    detail = await mailbox.get_message(again.messages[0].provider_message_id)
    assert detail.text_body == "Thanks for the invoice"


async def test_a_message_that_left_the_inbox_is_found_in_all_mail() -> None:
    server = gmail_server()
    archived = server.folders["[Gmail]/All Mail"][0]
    message_id = format(archived.gmail_id or 0, "x")

    with pytest.raises(Exception, match="not found"):
        await client(server, "INBOX").get_message(message_id)
    detail = await client(server, "INBOX", "\\All").get_message(message_id)

    assert detail.text_body == "Archived offer letter"


async def test_a_special_use_folder_the_server_lacks_is_left_out() -> None:
    server = FakeImapServer(folders={"INBOX": []})
    mailbox = client(server, "INBOX", "\\Archive")
    await mailbox.list_messages()
    with pytest.raises(PolicyDeniedError, match="not one this mailbox reads"):
        await mailbox.list_messages(folder="\\Archive")


async def test_folders_not_configured_stay_unreadable() -> None:
    with pytest.raises(PolicyDeniedError):
        await client(gmail_server(), "INBOX").list_messages(folder="\\Sent")


def test_unknown_special_use_names_are_refused_in_settings() -> None:
    with pytest.raises(ValidationError, match="special-use"):
        ImapSettings(
            host="imap.example.com",
            username="a@example.com",
            password=SecretStr("p"),
            folders=("INBOX", "\\Secret"),
        )


async def test_list_folders_returns_the_readable_folders_and_their_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = gmail_server()
    for name, value in {
        "HOST": "imap.example.com",
        "USERNAME": server.username,
        "PASSWORD": server.password,
        "FOLDERS": "INBOX,\\Sent,\\All",
    }.items():
        monkeypatch.setenv(f"ALGEN_TOOLS_IMAP_{name}", value)
    toolkit = imap_toolkit(imap_factory=server.factory())
    tool = next(t for t in toolkit.tools if t.definition.name == "imap.list_folders")

    result: Any = await tool.execute({}, fake_tool_context(tool))

    assert result["folders"] == [
        {"name": "INBOX", "special_use": None},
        {"name": "[Gmail]/Sent Mail", "special_use": "\\Sent"},
        {"name": "[Gmail]/All Mail", "special_use": "\\All"},
    ]


@pytest.mark.parametrize("field", ["text_contains", "gmail_query"])
async def test_search_text_must_be_printable_ascii(field: str) -> None:
    toolkit = imap_toolkit()
    tool = next(t for t in toolkit.tools if t.definition.name == "imap.list_messages")
    with pytest.raises(ValidationError):
        await tool.execute({field: "Rechnung für März"}, fake_tool_context(tool))
    with pytest.raises(ValidationError):
        await tool.execute({field: 'a"\r\nb'}, fake_tool_context(tool))


def test_list_lines_with_literal_names_are_read() -> None:
    class Connection:
        def list(self) -> tuple[str, list[Any]]:
            return "OK", [
                (b'(\\HasNoChildren \\Sent) "/" {12}', b'Sent "Items'),
                b'(\\Noselect) NIL "[Gmail]"',
                b'(\\All) "/" "Archive/All"',
            ]

    assert ImapMailboxClient._special_folders(Connection()) == {
        "\\Sent": 'Sent "Items',
        "\\All": "Archive/All",
    }
