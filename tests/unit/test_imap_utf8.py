"""Non-ASCII mail in the IMAP fake and non-ASCII IMAP search (SEARCH CHARSET UTF-8)."""

from __future__ import annotations

from email.message import EmailMessage
from email.policy import SMTP
from typing import Any

import pytest
from algen_agent_runtime.exceptions.errors import PolicyDeniedError
from algen_agent_runtime.tools.testing import fake_tool_context
from pydantic import SecretStr, ValidationError

from algen_agent_tools.imap import FakeImapServer, ImapMailboxClient, ImapSettings, imap_toolkit
from algen_agent_tools.imap.protocol import body_parts, fetch_responses

BODY = "Grüße aus Köln, 注文番号 42"


def mime(
    body: str = BODY, *, sender: str = "Jürgen Groß <juergen@example.com>", **content: Any
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "hr@example.com"
    message["Subject"] = "Bestellung"
    message.set_content(body, **content)
    return message.as_bytes(policy=SMTP)


def settings(server: FakeImapServer) -> ImapSettings:
    return ImapSettings(
        host="imap.example.com", username=server.username, password=SecretStr(server.password)
    )


def client(server: FakeImapServer) -> ImapMailboxClient:
    return ImapMailboxClient(settings(server), imap_factory=server.factory())


def served_structure(server: FakeImapServer, uid: int) -> tuple[int, bytes]:
    """The BODYSTRUCTURE size of a single-part message and the body the fake serves."""
    connection: Any = server.factory()(settings(server))
    connection.login(server.username, server.password)
    connection.select('"INBOX"', readonly=True)
    _, data = connection.uid("FETCH", str(uid), "(BODYSTRUCTURE BODY.PEEK[1])")
    [response] = fetch_responses(data)
    [part] = body_parts(response["BODYSTRUCTURE"])
    body = response["BODY[1]"]
    assert isinstance(body, bytes)
    return part.size, body


@pytest.mark.parametrize("cte", [None, "base64", "quoted-printable"])
async def test_the_fake_serves_utf8_bodies_in_any_transfer_encoding(cte: str | None) -> None:
    server = FakeImapServer()
    raw = mime(**({"cte": cte} if cte else {}))
    if cte is None:
        assert b"Content-Transfer-Encoding: 8bit" in raw
    stored = server.add(raw)
    mailbox = client(server)

    page = await mailbox.list_messages()
    detail = await mailbox.get_message(page.messages[0].provider_message_id)

    assert detail.text_body == BODY
    assert page.messages[0].snippet == BODY
    size, body = served_structure(server, stored.uid)
    assert size == len(body)
    assert body == raw.split(b"\r\n\r\n", 1)[1]


async def test_an_8bit_body_in_another_charset_keeps_its_bytes() -> None:
    server = FakeImapServer()
    raw = mime("Grüße", cte="8bit").replace(b'charset="utf-8"', b'charset="iso-8859-1"')
    raw = raw.replace("Grüße".encode(), "Grüße".encode("iso-8859-1"))
    stored = server.add(raw)
    mailbox = client(server)

    page = await mailbox.list_messages()
    detail = await mailbox.get_message(page.messages[0].provider_message_id)

    assert detail.text_body == "Grüße"
    size, body = served_structure(server, stored.uid)
    assert size == len(body) == len("Grüße\r\n".encode("iso-8859-1"))


async def test_non_ascii_text_is_searched_as_a_utf8_literal() -> None:
    server = FakeImapServer()
    server.add(mime())
    server.add(mime("Nothing special", sender="ana@example.com"))

    page = await client(server).list_messages(text_contains="注文番号")

    assert [m.sender_address for m in page.messages] == ["juergen@example.com"]
    literal = len("注文番号".encode())
    assert f"UID SEARCH CHARSET UTF-8 TEXT {{{literal}}}" in server.commands


async def test_non_ascii_sender_and_text_filters_must_both_match() -> None:
    server = FakeImapServer()
    server.add(mime())  # Jürgen, the body has Köln
    server.add(mime("Grüße aus Berlin"))  # Jürgen, no Köln
    server.add(mime(sender="Ana <ana@example.com>"))  # Köln, not Jürgen

    page = await client(server).list_messages(
        from_address="Jürgen", text_contains="Köln", unread_only=True
    )

    assert [m.snippet for m in page.messages] == [BODY]
    searches = [c for c in server.commands if c.startswith("UID SEARCH")]
    assert searches == [
        f"UID SEARCH CHARSET UTF-8 UNSEEN FROM {{{len('Jürgen'.encode())}}}",
        f"UID SEARCH CHARSET UTF-8 UNSEEN TEXT {{{len('Köln'.encode())}}}",
    ]


async def test_ascii_search_stays_a_quoted_string_without_a_charset() -> None:
    server = FakeImapServer()
    server.add(mime())

    await client(server).list_messages(text_contains="Koeln")

    assert 'UID SEARCH TEXT "Koeln"' in server.commands


async def test_a_server_without_utf8_search_refuses_cleanly() -> None:
    server = FakeImapServer(utf8_search=False)
    server.add(mime())

    with pytest.raises(PolicyDeniedError, match="does not search non-ASCII text"):
        await client(server).list_messages(subject="Größe")
    # ASCII search still works on the same connection.
    page = await client(server).list_messages(subject="Bestellung")
    assert len(page.messages) == 1


async def test_control_characters_are_refused_in_non_ascii_search() -> None:
    server = FakeImapServer()
    with pytest.raises(Exception, match="control characters"):
        await client(server).list_messages(text_contains="Köln\r\nA1 LOGOUT")
    assert not any("SEARCH" in command for command in server.commands)


async def test_the_tool_takes_non_ascii_text_but_not_a_non_ascii_gmail_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeImapServer()
    server.add(mime())
    for name, value in {
        "HOST": "imap.example.com",
        "USERNAME": server.username,
        "PASSWORD": server.password,
    }.items():
        monkeypatch.setenv(f"ALGEN_TOOLS_IMAP_{name}", value)
    toolkit = imap_toolkit(imap_factory=server.factory())
    tool = next(t for t in toolkit.tools if t.definition.name == "imap.list_messages")

    result: Any = await tool.execute({"text_contains": "Grüße"}, fake_tool_context(tool))

    assert len(result["messages"]) == 1
    with pytest.raises(ValidationError):
        await tool.execute({"gmail_query": "Grüße"}, fake_tool_context(tool))
    with pytest.raises(ValidationError):
        await tool.execute({"text_contains": "Köln\x00"}, fake_tool_context(tool))
