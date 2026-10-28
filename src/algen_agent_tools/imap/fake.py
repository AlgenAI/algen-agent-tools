"""An in-memory IMAP server for offline tests of the IMAP toolkit.

It answers the commands the connector sends with responses shaped exactly as ``imaplib``
returns them (literals as ``(text, bytes)`` tuples), computed from real MIME messages, so tests
exercise the connector's parsing as well as its tools. Every command is recorded, so a test can
check that nothing was written and nothing was marked as seen.
"""

from __future__ import annotations

import email
import imaplib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.message import Message
from email.policy import compat32
from typing import Any

from algen_agent_tools.imap.client import ImapSettings
from algen_agent_tools.imap.protocol import (
    MONTHS,
    internaldate_ms,
    parse_value,
    quote,
    unquote,
)


@dataclass
class FakeImapMessage:
    raw: bytes
    uid: int
    flags: list[str] = field(default_factory=list)
    internaldate: str = "01-Oct-2026 09:00:00 +0000"
    gmail_id: int | None = None
    gmail_thread_id: int | None = None
    gmail_labels: tuple[str, ...] = ()

    @property
    def message(self) -> Message:
        return email.message_from_bytes(self.raw, policy=compat32)


@dataclass
class FakeImapServer:
    """Folders of messages, served to connections from :meth:`factory`."""

    username: str = "agent@example.com"
    password: str = "app-password"
    gmail: bool = False
    folders: dict[str, list[FakeImapMessage]] = field(default_factory=lambda: {"INBOX": []})
    uidvalidity: dict[str, int] = field(default_factory=dict)
    commands: list[str] = field(default_factory=list)
    """Every command received, as ``NAME args``; a test checks it holds no write."""
    connections: int = 0
    drop_next: bool = False
    """Raise ``imaplib.IMAP4.abort`` on the next command, as a server that hung up would."""
    logged_out: int = 0

    def add(
        self,
        raw: bytes,
        *,
        folder: str = "INBOX",
        flags: Sequence[str] = (),
        internaldate: str = "01-Oct-2026 09:00:00 +0000",
        gmail_thread_id: int | None = None,
        gmail_labels: Sequence[str] = (),
    ) -> FakeImapMessage:
        """Store ``raw`` (a whole RFC 5322 message) and return it with its new UID."""
        messages = self.folders.setdefault(folder, [])
        self.uidvalidity.setdefault(folder, 1_000 + len(self.uidvalidity))
        uid = max((item.uid for item in messages), default=0) + 1
        gmail_id = 0x18C0000000000000 + sum(len(items) for items in self.folders.values())
        message = FakeImapMessage(
            raw=raw,
            uid=uid,
            flags=list(flags),
            internaldate=internaldate,
            gmail_id=gmail_id if self.gmail else None,
            gmail_thread_id=(gmail_thread_id or gmail_id) if self.gmail else None,
            gmail_labels=tuple(gmail_labels),
        )
        messages.append(message)
        return message

    def factory(self) -> Callable[[ImapSettings], FakeImapConnection]:
        """An ``imap_factory`` for :func:`~algen_agent_tools.imap.imap_toolkit`."""

        def connect(settings: ImapSettings) -> FakeImapConnection:
            self.connections += 1
            return FakeImapConnection(self)

        return connect


class FakeImapConnection:
    """The slice of ``imaplib.IMAP4`` the connector uses."""

    def __init__(self, server: FakeImapServer) -> None:
        self._server = server
        self.capabilities: tuple[str, ...] = ("IMAP4REV1", "AUTH=PLAIN") + (
            ("X-GM-EXT-1",) if server.gmail else ()
        )
        self._authenticated = False
        self._folder: str | None = None
        self._responses: dict[str, list[bytes]] = {}

    def _record(self, command: str) -> None:
        self._server.commands.append(command)
        if self._server.drop_next:
            self._server.drop_next = False
            raise imaplib.IMAP4.abort("socket error: EOF")

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self._record("LOGIN")
        if unquote(user) != self._server.username or password != self._server.password:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials (Failure)")
        self._authenticated = True
        return "OK", [b"LOGIN completed"]

    def select(self, mailbox: str = "INBOX", readonly: bool = False) -> tuple[str, list[bytes]]:
        self._record(f"{'EXAMINE' if readonly else 'SELECT'} {mailbox}")
        name = unquote(mailbox)
        if not self._authenticated or name not in self._server.folders:
            self._folder = None
            return "NO", [b"Unknown mailbox"]
        self._folder = name
        validity = self._server.uidvalidity.setdefault(name, 1_000)
        self._responses = {"UIDVALIDITY": [str(validity).encode()]}
        return "OK", [str(len(self._server.folders[name])).encode()]

    def response(self, code: str) -> tuple[str, list[bytes | None]]:
        return code, list(self._responses.pop(code, [None]))

    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]:
        self._record(f"UID {command} {' '.join(args)}")
        if self._folder is None:
            raise imaplib.IMAP4.error("command illegal in this state")
        messages = self._server.folders[self._folder]
        if command == "SEARCH":
            found = [str(item.uid) for item in messages if _matches(item, list(args))]
            return "OK", [" ".join(found).encode()]
        if command == "FETCH":
            wanted = _uid_set(args[0])
            items = _items(args[1])
            data: list[Any] = []
            for sequence, item in enumerate(messages, 1):
                if item.uid in wanted:
                    data.extend(_fetch(sequence, item, items))
            return "OK", data
        raise imaplib.IMAP4.error(f"the fake does not support UID {command}")

    def shutdown(self) -> None:
        pass

    def logout(self) -> tuple[str, list[bytes]]:
        self._server.logged_out += 1
        return "BYE", [b"logging out"]


# SEARCH


def _uid_set(value: str) -> set[int]:
    uids: set[int] = set()
    for item in value.split(","):
        low, _, high = item.partition(":")
        uids.update(range(int(low), int(high or low) + 1))
    return uids


def _date(value: str) -> date:
    day, month, year = value.split("-")
    return date(int(year), MONTHS.index(month) + 1, int(day))


def _matches(item: FakeImapMessage, criteria: list[str]) -> bool:
    message = item.message
    received = internaldate_ms(item.internaldate.encode())
    day = datetime.fromtimestamp(received / 1000, UTC).date()
    index = 0
    while index < len(criteria):
        key = criteria[index].upper()
        if key == "ALL":
            index += 1
        elif key == "UNSEEN":
            if "\\Seen" in item.flags:
                return False
            index += 1
        elif key in ("FROM", "SUBJECT"):
            wanted = unquote(criteria[index + 1]).casefold()
            if wanted not in str(message.get(key.title(), "")).casefold():
                return False
            index += 2
        elif key == "SINCE":
            if day < _date(criteria[index + 1]):
                return False
            index += 2
        elif key == "BEFORE":
            if day >= _date(criteria[index + 1]):
                return False
            index += 2
        elif key == "UID":
            if item.uid not in _uid_set(criteria[index + 1]):
                return False
            index += 2
        elif key == "X-GM-MSGID":
            if item.gmail_id != int(criteria[index + 1]):
                return False
            index += 2
        else:
            raise imaplib.IMAP4.error(f"the fake does not support SEARCH {key}")
    return True


# FETCH


def _items(value: str) -> list[str]:
    parsed = parse_value(value.encode())
    if not isinstance(parsed, list):
        parsed = [parsed]
    items: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            raise imaplib.IMAP4.error("unexpected FETCH item")
        items.append(item)
    return items


def _children(message: Message) -> list[Message]:
    """The parts a multipart (or message/rfc822) part holds."""
    payload = message.get_payload()
    return (
        [item for item in payload if isinstance(item, Message)] if isinstance(payload, list) else []
    )


def _parts(message: Message, number: str = "") -> list[tuple[str, Message]]:
    if message.get_content_maintype() == "multipart":
        found: list[tuple[str, Message]] = []
        for index, child in enumerate(_children(message), 1):
            found.extend(_parts(child, f"{number}.{index}" if number else str(index)))
        return found
    return [(number or "1", message)]


def _payload(part: Message) -> bytes:
    payload = part.get_payload()
    if isinstance(payload, list):  # message/rfc822
        return b"".join(item.as_bytes() for item in _children(part))
    return str(payload).encode("ascii", "surrogateescape")


def _params(pairs: Sequence[tuple[str, str]] | None) -> str:
    if not pairs:
        return "NIL"
    return "(" + " ".join(f"{_q(key.upper())} {_q(value)}" for key, value in pairs) + ")"


def _raw_params(header: str) -> list[tuple[str, str]]:
    """A header's parameters as a server reports them: RFC 2231 values left encoded."""
    pieces, current, quoted = [], "", False
    for char in header.replace("\r\n", "").replace("\n", ""):
        if char == '"':
            quoted = not quoted
        if char == ";" and not quoted:
            pieces.append(current)
            current = ""
        else:
            current += char
    pieces.append(current)
    pairs = []
    for piece in pieces[1:]:
        key, _, value = piece.strip().partition("=")
        if key:
            pairs.append((key.strip(), unquote(value.strip())))
    return pairs


def _q(value: str) -> str:
    return quote(value) if value.isascii() else '"' + value + '"'


def _structure(message: Message) -> str:
    if message.get_content_maintype() == "multipart":
        children = "".join(_structure(child) for child in _children(message))
        return f"({children} {_q(message.get_content_subtype().upper())})"
    kind = message.get_content_maintype().upper()
    subtype = message.get_content_subtype().upper()
    params = _raw_params(message.get("Content-Type", ""))
    encoding = str(message.get("Content-Transfer-Encoding", "7BIT")).upper()
    body = _payload(message)
    fields = f"{_q(kind)} {_q(subtype)} {_params(params)} NIL NIL {_q(encoding)} {len(body)}"
    if kind == "TEXT":
        fields += f" {body.count(b'\n') + 1}"
    elif (kind, subtype) == ("MESSAGE", "RFC822"):
        [inner] = _children(message)
        fields += f" NIL {_structure(inner)} {body.count(b'\n') + 1}"
    disposition = message.get_content_disposition()
    if disposition:
        pairs = _raw_params(str(message.get("Content-Disposition", "")))
        fields += f" NIL ({_q(disposition.upper())} {_params(pairs)}) NIL NIL"
    return f"({fields})"


def _header_fields(raw: bytes, names: Sequence[str]) -> bytes:
    head = raw.replace(b"\r\n", b"\n").split(b"\n\n", 1)[0]
    fields: list[bytes] = []
    for line in head.split(b"\n"):
        if line[:1] in (b" ", b"\t") and fields:
            fields[-1] += b"\r\n" + line
        else:
            fields.append(line)
    wanted = {name.casefold() for name in names}
    kept = [item for item in fields if item.split(b":", 1)[0].decode().casefold() in wanted]
    return b"".join(item + b"\r\n" for item in kept) + b"\r\n"


def _fetch(sequence: int, item: FakeImapMessage, names: list[str]) -> list[Any]:
    """One FETCH response as imaplib returns it: literals become ``(text, bytes)`` tuples."""
    pieces: list[bytes | tuple[bytes]] = []
    for name in ["UID", *[n for n in names if n.upper() != "UID"]]:
        upper = name.upper()
        if upper == "UID":
            pieces.append(f"UID {item.uid}".encode())
        elif upper == "FLAGS":
            pieces.append(f"FLAGS ({' '.join(item.flags)})".encode())
        elif upper == "INTERNALDATE":
            pieces.append(f'INTERNALDATE "{item.internaldate}"'.encode())
        elif upper == "RFC822.SIZE":
            pieces.append(f"RFC822.SIZE {len(item.raw)}".encode())
        elif upper == "BODYSTRUCTURE":
            pieces.append(f"BODYSTRUCTURE {_structure(item.message)}".encode())
        elif upper == "X-GM-MSGID" and item.gmail_id is not None:
            pieces.append(f"X-GM-MSGID {item.gmail_id}".encode())
        elif upper == "X-GM-THRID" and item.gmail_thread_id is not None:
            pieces.append(f"X-GM-THRID {item.gmail_thread_id}".encode())
        elif upper == "X-GM-LABELS":
            labels = " ".join(_q(label) for label in item.gmail_labels)
            pieces.append(f"X-GM-LABELS ({labels})".encode())
        elif upper.startswith(("BODY[", "BODY.PEEK[")):
            if not upper.startswith("BODY.PEEK[") and "\\Seen" not in item.flags:
                item.flags.append("\\Seen")
            section, _, partial = name.split("[", 1)[1].partition("]")
            content = _section(item, section)
            label = f"BODY[{section}]"
            if partial:
                start, _, length = partial.strip("<>").partition(".")
                content = content[int(start) : int(start) + int(length)]
                label += f"<{start}>"
            pieces.append(label.encode())
            pieces.append((content,))
    return _imaplib_shape(sequence, pieces)


def _section(item: FakeImapMessage, section: str) -> bytes:
    if section.upper().startswith("HEADER.FIELDS"):
        names = section[section.index("(") + 1 : section.index(")")].split()
        return _header_fields(item.raw, names)
    for number, part in _parts(item.message):
        if number == section:
            return _payload(part)
    return b""


def _imaplib_shape(sequence: int, pieces: list[bytes | tuple[bytes]]) -> list[Any]:
    data: list[Any] = []
    pending = f"{sequence} (".encode()
    first = True
    for piece in pieces:
        if isinstance(piece, tuple):
            literal = piece[0]
            data.append((pending + f" {{{len(literal)}}}".encode(), literal))
            pending = b""
            continue
        pending += (b"" if first else b" ") + piece
        first = False
    data.append(pending + b")")
    return data
