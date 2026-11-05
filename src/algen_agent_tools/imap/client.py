"""IMAP mailbox reads over TLS with an app password (RFC 3501).

Folders open read-only (EXAMINE) and every body fetch uses ``BODY.PEEK``, so reading never marks
mail as seen. Message bodies and attachments are fetched by part, within byte limits, and never
as whole messages. ``imaplib`` blocks, so each operation runs in a worker thread, one at a time
on the mailbox's single connection.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import imaplib
import re
import ssl
from collections.abc import Callable, Mapping
from datetime import date
from email.message import Message
from email.parser import BytesHeaderParser
from email.policy import default as default_policy
from typing import Any, TypeVar

from algen_agent_runtime.exceptions.errors import PolicyDeniedError
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from algen_agent_tools._core.errors import failure
from algen_agent_tools.attachments.models import AttachmentUpload
from algen_agent_tools.imap.protocol import (
    BodyPart,
    Value,
    attachment_parts,
    body_parts,
    decode_text,
    decode_transfer,
    fetch_responses,
    internaldate_ms,
    parse_value,
    quote,
    search_date,
    section,
    strings,
    text,
    text_part,
)
from algen_agent_tools.mail.inbound import (
    EmailThread,
    InboundEmailAttachment,
    InboundEmailDetail,
    InboundEmailSummary,
    bounded_body,
    html_to_text,
    thread_headers,
    thread_of,
)
from algen_agent_tools.mail.models import EmailDeliveryError, reject_header_injection
from algen_agent_tools.mail.sender_auth import GMAIL_AUTHSERV_ID, topmost_verdict

T = TypeVar("T")

HEADER_FIELDS = "FROM TO CC SUBJECT DATE AUTHENTICATION-RESULTS MESSAGE-ID IN-REPLY-TO REFERENCES"
SNIPPET_BYTES = 4_096
SNIPPET_CHARS = 200
MAX_BODY_FETCH_BYTES = 1_000_000
MAX_HEADER_CHARS = 10_000
GMAIL_CAPABILITY = "X-GM-EXT-1"
_GMAIL_ID = re.compile(r"^[0-9a-f]{1,16}$")
_FOLDER_ID = re.compile(r"^([0-9]{1,10})\.([0-9]{1,10})\.([A-Za-z0-9_-]{1,400})$")
_PAGE_TOKEN = re.compile(r"^([0-9]{1,10})\.([0-9]{1,10})$")
_SECTION = re.compile(r"^[0-9]{1,3}(\.[0-9]{1,3}){0,9}$")
_THREAD_ROOT = re.compile(r"^r\.([A-Za-z0-9_-]{4,1400})$")
SPECIAL_USE = ("\\All", "\\Archive", "\\Drafts", "\\Flagged", "\\Junk", "\\Sent", "\\Trash")
"""RFC 6154 special-use folders a ``folders`` setting may name instead of a folder."""


def special_use(name: str) -> str | None:
    """The canonical special-use flag ``name`` stands for (``\\sent`` is ``\\Sent``)."""
    return next((flag for flag in SPECIAL_USE if flag.casefold() == name.casefold()), None)


class ImapSettings(BaseModel):
    """One IMAP mailbox, reached over TLS (IMAPS) with a login and app password."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=993, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=320)
    password: SecretStr
    folders: tuple[str, ...] = Field(default=("INBOX",), min_length=1, max_length=50)
    """The folders tools may read; the first is the default. A special-use name (``\\Sent``,
    ``\\All``, ``\\Archive``, ``\\Drafts``, ``\\Junk``, ``\\Trash``, ``\\Flagged``) stands
    for the server's folder with that flag, found when the connection opens."""
    authserv_id: str | None = Field(default=None, max_length=253)
    """The receiving server's name in its Authentication-Results headers. Unset: Gmail's on a
    Gmail server, otherwise ``sender_auth`` stays ``unknown``."""
    timeout_seconds: float = Field(default=30, gt=0, le=300)
    max_attachment_bytes: int = Field(default=20_000_000, ge=1, le=100_000_000)

    @model_validator(mode="after")
    def validate_settings(self) -> ImapSettings:
        reject_header_injection(self.host, "IMAP host")
        for value in (self.username, *self.folders):
            quote(value)  # printable ASCII only
        if any(not folder or len(folder) > 200 for folder in self.folders):
            raise ValueError("IMAP folder names must be 1 to 200 characters")
        unknown = [f for f in self.folders if f.startswith("\\") and special_use(f) is None]
        if unknown:
            raise ValueError(f"IMAP special-use folders must be one of {list(SPECIAL_USE)}")
        if len({folder.casefold() for folder in self.folders}) != len(self.folders):
            raise ValueError("IMAP folders must be unique")
        return self


class ImapFolder(BaseModel):
    """One folder the mailbox's tools may read."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    special_use: str | None = None
    """The folder's RFC 6154 flag, such as ``\\Sent``, when the server marks it."""


class ImapFolderList(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    folders: tuple[ImapFolder, ...] = ()


class ImapMailboxPage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    messages: tuple[InboundEmailSummary, ...] = ()
    next_page_token: str | None = None
    result_size_estimate: int = Field(default=0, ge=0)


ImapFactory = Callable[[ImapSettings], Any]
"""Opens an unauthenticated connection with ``imaplib.IMAP4``'s interface."""


def _open_tls(settings: ImapSettings) -> imaplib.IMAP4:
    return imaplib.IMAP4_SSL(
        settings.host,
        settings.port,
        ssl_context=ssl.create_default_context(),
        timeout=settings.timeout_seconds,
    )


def folder_token(folder: str) -> str:
    return base64.urlsafe_b64encode(folder.encode()).decode().rstrip("=")


def _folder_from_token(token: str) -> str:
    try:
        return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError):
        return ""


def _encoded_limit(decoded_bytes: int) -> int:
    """The most octets a part of ``decoded_bytes`` can take as base64 with line breaks."""
    return decoded_bytes * 4 // 3 * 78 // 76 + 1_024


def _header(headers: Message, name: str) -> str:
    try:
        value = headers.get(name)
        return "" if value is None else str(value)[:MAX_HEADER_CHARS]
    except (ValueError, TypeError, IndexError, AttributeError, LookupError):
        return ""


def thread_key(ids: dict[str, Any]) -> str | None:
    """A plain IMAP thread id: the thread's first Message-ID (References, else In-Reply-To,
    else the message's own), as ``r.<base64url>``."""
    references: tuple[str, ...] = ids["references"]
    root = references[0] if references else ids["in_reply_to"] or ids["message_id_header"]
    if root is None:
        return None
    return "r." + base64.urlsafe_b64encode(root.encode()).decode().rstrip("=")


def _thread_root(thread_id: str) -> str | None:
    found = _THREAD_ROOT.fullmatch(thread_id)
    if found is None:
        return None
    root = _folder_from_token(found.group(1))
    return root if root.startswith("<") and root.endswith(">") else None


def _auth_results(headers: Message) -> list[str]:
    try:
        return [str(value) for value in headers.get_all("Authentication-Results") or ()]
    except (ValueError, TypeError, IndexError, AttributeError, LookupError):
        return []


class ImapMailboxClient:
    """Bounded, read-only access to the configured folders of one IMAP mailbox.

    Message ids are Gmail's own (hexadecimal, as the Gmail API shows them) on a Gmail server,
    and ``<UIDVALIDITY>.<UID>.<folder>`` elsewhere, so an id stays valid until the server
    rebuilds the folder, and a stale id is an error rather than another message.
    """

    def __init__(self, settings: ImapSettings, *, imap_factory: ImapFactory | None = None) -> None:
        self._settings = settings
        self._factory = imap_factory or _open_tls
        self._connection: Any = None
        self._gmail = False
        self._folders: tuple[str, ...] | None = None
        self._flags: dict[str, str] = {}
        self._lock = asyncio.Lock()

    # Connection

    async def _run(self, operation: str, work: Callable[[Any], T]) -> T:
        async with self._lock:
            try:
                return await asyncio.to_thread(self._attempt, work)
            except (EmailDeliveryError, PolicyDeniedError):
                raise
            except (imaplib.IMAP4.error, OSError, ValueError, UnicodeError) as exc:
                raise EmailDeliveryError(failure("IMAP", operation, exc)) from exc

    def _attempt(self, work: Callable[[Any], T]) -> T:
        # Reads are safe to repeat, so a connection the server dropped is reopened once. A
        # timeout is not retried: the tool's deadline would pass first.
        for attempt in (1, 2):
            connection = self._connect()
            try:
                return work(connection)
            except (imaplib.IMAP4.abort, ConnectionError):
                self._drop()
                if attempt == 2:
                    raise
            except OSError:
                self._drop()
                raise
        raise AssertionError("unreachable")

    def _connect(self) -> Any:
        if self._connection is not None:
            return self._connection
        connection = self._factory(self._settings)
        try:
            connection.login(
                quote(self._settings.username), self._settings.password.get_secret_value()
            )
        except BaseException:
            with contextlib.suppress(Exception):
                connection.shutdown()
            raise
        self._gmail = GMAIL_CAPABILITY in tuple(getattr(connection, "capabilities", ()))
        if any(special_use(folder) for folder in self._settings.folders):
            try:
                self._resolve(connection)
            except BaseException:
                with contextlib.suppress(Exception):
                    connection.logout()
                raise
        self._connection = connection
        return connection

    @staticmethod
    def _special_folders(connection: Any) -> dict[str, str]:
        """Each special-use flag the server marks a folder with, and that folder's name."""
        status, data = connection.list()
        if status != "OK":
            raise EmailDeliveryError("IMAP LIST failed")
        found: dict[str, str] = {}
        for line in data or ():
            if isinstance(line, tuple) and len(line) == 2:
                # A name sent as a literal: imaplib gives (b'(flags) "/" {n}', b'name').
                head, literal = (bytes(item) for item in line)
                escaped = literal.replace(b"\\", b"\\\\").replace(b'"', b'\\"')
                raw = re.sub(rb"\{\d+\+?\}\s*$", b"", head) + b'"' + escaped + b'"'
            elif isinstance(line, bytes):
                raw = line
            else:
                continue
            parsed = parse_value(b"(" + raw + b")")
            if not isinstance(parsed, list) or len(parsed) < 3 or not isinstance(parsed[0], list):
                continue
            name = text(parsed[2])
            for flag in strings(parsed[0]):
                canonical = special_use(flag)
                if canonical and name:
                    found.setdefault(canonical, name)
        return found

    def _resolve(self, connection: Any) -> None:
        """Replace special-use names in the folders setting with the server's folders. One the
        server does not have is left out."""
        flags = self._special_folders(connection)
        folders: list[str] = []
        for folder in self._settings.folders:
            canonical = special_use(folder)
            name = flags.get(canonical) if canonical else folder
            if name and name.casefold() not in {item.casefold() for item in folders}:
                folders.append(name)
        self._folders = tuple(folders)
        self._flags = {name: flag for flag, name in flags.items()}

    def _drop(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            with contextlib.suppress(Exception):
                connection.logout()

    async def aclose(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._drop)

    # Folders and ids

    @property
    def _trusted_server(self) -> str | None:
        if self._settings.authserv_id:
            return self._settings.authserv_id
        return GMAIL_AUTHSERV_ID if self._gmail else None

    @property
    def _readable(self) -> tuple[str, ...]:
        """The folders tools may read, with special-use names resolved once connected."""
        return self._folders if self._folders is not None else self._settings.folders

    def _folder(self, folder: str) -> str:
        """The configured folder ``folder`` names (INBOX in any case, or a special-use name)."""
        canonical = special_use(folder)
        if canonical is not None:
            for name, flag in self._flags.items():
                if flag == canonical and name in self._readable:
                    return name
        for configured in self._readable:
            if folder == configured or (
                folder.upper() == "INBOX" and configured.upper() == "INBOX"
            ):
                return configured
        raise PolicyDeniedError(
            f"folder {folder!r} is not one this mailbox reads; use one of {list(self._readable)}"
        )

    @staticmethod
    def _examine(connection: Any, folder: str) -> int:
        """Open ``folder`` read-only and return its UIDVALIDITY."""
        status, _ = connection.select(quote(folder), readonly=True)
        if status != "OK":
            raise EmailDeliveryError(f"IMAP folder {folder!r} cannot be opened")
        _, values = connection.response("UIDVALIDITY")
        for value in reversed(values or ()):
            if value and text(value).strip().isdigit():
                return int(text(value).strip())
        raise EmailDeliveryError(f"IMAP folder {folder!r} has no UIDVALIDITY")

    @staticmethod
    def _uid(connection: Any, command: str, *args: str) -> list[Any]:
        status, data = connection.uid(command, *args)
        if status != "OK":
            raise EmailDeliveryError(f"IMAP {command} failed")
        return list(data or ())

    def _locate(self, connection: Any, message_id: str) -> tuple[str, int, int]:
        """The folder, its UIDVALIDITY and the message's UID, with the folder open."""
        found = _FOLDER_ID.fullmatch(message_id)
        if found is not None:
            folder = self._folder(_folder_from_token(found.group(3)))
            validity = self._examine(connection, folder)
            if validity != int(found.group(1)):
                raise EmailDeliveryError(
                    "IMAP message id is stale (the server rebuilt the folder); list the "
                    "messages again"
                )
            return folder, validity, int(found.group(2))
        if self._gmail and _GMAIL_ID.fullmatch(message_id):
            for folder in self._readable:
                validity = self._examine(connection, folder)
                data = self._uid(connection, "SEARCH", "X-GM-MSGID", str(int(message_id, 16)))
                uids = b" ".join(item for item in data if isinstance(item, bytes)).split()
                if uids:
                    return folder, validity, int(uids[0])
            raise EmailDeliveryError("IMAP message was not found in the configured folders")
        raise EmailDeliveryError("IMAP message id is not valid for this mailbox")

    # Fetching

    def _metadata(self, connection: Any, uids: list[int]) -> dict[int, dict[str, Value]]:
        items = f"UID FLAGS INTERNALDATE BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS ({HEADER_FIELDS})]"
        if self._gmail:
            items += " X-GM-MSGID X-GM-THRID X-GM-LABELS"
        data = self._uid(connection, "FETCH", ",".join(map(str, uids)), f"({items})")
        found: dict[int, dict[str, Value]] = {}
        for response in fetch_responses(data):
            uid = text(response.get("UID"))
            if uid.isdigit():
                found[int(uid)] = response
        return found

    def _part(self, connection: Any, uid: int, part: BodyPart, limit: int) -> bytes:
        """Up to ``limit`` octets of one part, still transfer-encoded."""
        data = self._uid(connection, "FETCH", str(uid), f"(BODY.PEEK[{part.section}]<0.{limit}>)")
        for response in fetch_responses(data):
            if text(response.get("UID")) in ("", str(uid)):
                return section(response, "BODY[")[:limit]
        return b""

    def _text(self, connection: Any, uid: int, part: BodyPart, limit: int) -> tuple[str, bool]:
        """A text part as plain text, from at most ``limit`` octets; and whether it was cut."""
        raw = self._part(connection, uid, part, limit)
        content = decode_text(decode_transfer(raw, part.encoding), part.charset)
        if part.media_type == "text/html":
            content = html_to_text(content)
        else:
            content = content.replace("\r\n", "\n").strip()
        return content, part.size > limit

    def _summary(
        self,
        response: Mapping[str, Value],
        folder: str,
        validity: int,
        uid: int,
        snippet: str = "",
    ) -> dict[str, Any]:
        headers = BytesHeaderParser(policy=default_policy).parsebytes(
            section(response, "BODY[HEADER")
        )
        gmail_id = text(response.get("X-GM-MSGID"))
        thread_id = text(response.get("X-GM-THRID"))
        trusted = self._trusted_server
        parts = body_parts(response.get("BODYSTRUCTURE"))
        ids = thread_headers(
            _header(headers, "Message-ID"),
            _header(headers, "In-Reply-To"),
            _header(headers, "References"),
        )
        return {
            "provider_message_id": format(int(gmail_id), "x")
            if gmail_id.isdigit()
            else f"{validity}.{uid}.{folder_token(folder)}",
            "thread_id": format(int(thread_id), "x") if thread_id.isdigit() else thread_key(ids),
            "sender": _header(headers, "From"),
            "recipients": _header(headers, "To"),
            "subject": _header(headers, "Subject"),
            "received_at_ms": internaldate_ms(response.get("INTERNALDATE")),
            "snippet": snippet,
            "labels": tuple(
                label[:200]
                for label in (
                    *strings(response.get("FLAGS")),
                    *strings(response.get("X-GM-LABELS")),
                )
            )[:50],
            "attachments": tuple(
                InboundEmailAttachment(
                    provider_attachment_id=part.section,
                    part_id=part.section,
                    filename=part.filename or "message.eml",
                    media_type=part.media_type,
                    size=part.size * 3 // 4 if part.encoding == "base64" else part.size,
                )
                for part in attachment_parts(parts)[:20]
            ),
            "sender_auth": topmost_verdict(_auth_results(headers), trusted)
            if trusted
            else "unknown",
            "_cc": _header(headers, "Cc"),
            "_parts": parts,
            "_ids": ids,
        }

    # Operations

    async def list_messages(
        self,
        *,
        folder: str = "INBOX",
        from_address: str | None = None,
        subject: str | None = None,
        since: date | None = None,
        before: date | None = None,
        unread_only: bool = False,
        text_contains: str | None = None,
        gmail_query: str | None = None,
        max_results: int = 25,
        page_token: str | None = None,
    ) -> ImapMailboxPage:
        """Messages in ``folder`` that match every filter given, newest first.

        ``text_contains`` is text the headers or body contain (IMAP ``TEXT``); ``gmail_query``
        is a Gmail search (``X-GM-RAW``), only on a Gmail server.
        """
        if not 1 <= max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")
        criteria: list[str] = []
        if from_address:
            criteria += ["FROM", quote(from_address)]
        if subject:
            criteria += ["SUBJECT", quote(subject)]
        if since is not None:
            criteria += ["SINCE", search_date(since)]
        if before is not None:
            criteria += ["BEFORE", search_date(before)]
        if unread_only:
            criteria.append("UNSEEN")
        if text_contains:
            criteria += ["TEXT", quote(text_contains)]
        if gmail_query:
            criteria += ["X-GM-RAW", quote(gmail_query)]
        cursor = _PAGE_TOKEN.fullmatch(page_token) if page_token else None
        if page_token and cursor is None:
            raise EmailDeliveryError("IMAP page token is not valid")

        def work(connection: Any) -> ImapMailboxPage:
            if gmail_query and not self._gmail:
                raise PolicyDeniedError("gmail_query needs a Gmail server; use text_contains")
            name = self._folder(folder)
            validity = self._examine(connection, name)
            search = list(criteria)
            if cursor is not None:
                if int(cursor.group(1)) != validity:
                    raise EmailDeliveryError(
                        "IMAP page token is stale (the server rebuilt the folder); list again "
                        "without it"
                    )
                last = int(cursor.group(2))
                if last <= 1:
                    return ImapMailboxPage()
                search += ["UID", f"1:{last - 1}"]
            data = self._uid(connection, "SEARCH", *(search or ["ALL"]))
            found = b" ".join(item for item in data if isinstance(item, bytes)).split()
            uids = sorted({int(item) for item in found if item.isdigit()}, reverse=True)
            page = uids[:max_results]
            rows: list[InboundEmailSummary] = []
            metadata = self._metadata(connection, page) if page else {}
            for uid in page:
                response = metadata.get(uid)
                if response is None:  # expunged since the search
                    continue
                fields = self._summary(response, name, validity, uid)
                body = text_part(fields.pop("_parts"))
                fields.pop("_cc")
                fields.pop("_ids")
                if body is not None:
                    content, _ = self._text(connection, uid, body, SNIPPET_BYTES)
                    fields["snippet"] = " ".join(content.split())[:SNIPPET_CHARS]
                rows.append(InboundEmailSummary(**fields))
            return ImapMailboxPage(
                messages=tuple(rows),
                next_page_token=f"{validity}.{page[-1]}" if len(uids) > max_results else None,
                result_size_estimate=len(uids),
            )

        return await self._run("inbox read", work)

    async def list_folders(self) -> ImapFolderList:
        """The folders tools may read, each with its special-use flag when the server marks
        one."""

        def work(connection: Any) -> ImapFolderList:
            flags = {name: flag for flag, name in self._special_folders(connection).items()}
            return ImapFolderList(
                folders=tuple(
                    ImapFolder(name=name, special_use=flags.get(name)) for name in self._readable
                )
            )

        return await self._run("folder list", work)

    async def get_message(
        self, provider_message_id: str, *, max_chars: int = 20_000
    ) -> InboundEmailDetail:
        """One message with its body as plain text, cut to ``max_chars``."""

        def work(connection: Any) -> InboundEmailDetail:
            folder, validity, uid = self._locate(connection, provider_message_id)
            response = self._metadata(connection, [uid]).get(uid)
            if response is None:
                raise EmailDeliveryError("IMAP message was not found")
            return self._detail(connection, response, folder, validity, uid, max_chars)

        return await self._run("message read", work)

    def _detail(
        self,
        connection: Any,
        response: Mapping[str, Value],
        folder: str,
        validity: int,
        uid: int,
        max_chars: int,
    ) -> InboundEmailDetail:
        """One message from its metadata, with the body fetched as plain text."""
        limit = min(max_chars * 6 + 4_096, MAX_BODY_FETCH_BYTES)
        fields = self._summary(response, folder, validity, uid)
        body = text_part(fields.pop("_parts"))
        cc = fields.pop("_cc")
        ids = fields.pop("_ids")
        content, cut = self._text(connection, uid, body, limit) if body else ("", False)
        bounded, truncated = bounded_body(content, max_chars)
        fields["snippet"] = " ".join(bounded.split())[:SNIPPET_CHARS]
        return InboundEmailDetail(
            **fields, **ids, cc=cc, text_body=bounded, body_truncated=truncated or cut
        )

    async def get_thread(
        self, thread_id: str, *, max_messages: int = 20, max_chars: int = 4_000
    ) -> EmailThread:
        """The thread's messages in the configured folders, oldest first, the newest
        ``max_messages`` of them.

        On Gmail the thread is Gmail's own (``X-GM-THRID``). Elsewhere it is every message whose
        Message-ID, In-Reply-To or References names the thread's first message.
        """
        root = _thread_root(thread_id)

        def work(connection: Any) -> EmailThread:
            if root is not None:
                quoted = quote(root)
                criteria = [
                    "OR", "OR", "HEADER", "Message-ID", quoted, "HEADER", "References", quoted,
                    "HEADER", "In-Reply-To", quoted,
                ]  # fmt: skip
            elif self._gmail and _GMAIL_ID.fullmatch(thread_id):
                criteria = ["X-GM-THRID", str(int(thread_id, 16))]
            else:
                raise EmailDeliveryError("IMAP thread id is not valid for this mailbox")
            details: dict[str, InboundEmailDetail] = {}
            more = False
            for folder in self._readable:
                validity = self._examine(connection, folder)
                data = self._uid(connection, "SEARCH", *criteria)
                found = b" ".join(item for item in data if isinstance(item, bytes)).split()
                uids = sorted({int(item) for item in found if item.isdigit()})
                # The newest messages of each folder; a folder's UIDs grow with arrival.
                more = more or len(uids) > max_messages
                page = uids[-max_messages:]
                metadata = self._metadata(connection, page) if page else {}
                for uid in page:
                    response = metadata.get(uid)
                    if response is None:
                        continue
                    detail = self._detail(connection, response, folder, validity, uid, max_chars)
                    # Gmail shows one message in several folders (labels); count it once.
                    key = detail.message_id_header or detail.provider_message_id
                    details.setdefault(key, detail)
            if not details:
                raise EmailDeliveryError("IMAP thread was not found in the configured folders")
            return thread_of(thread_id, list(details.values()), max_messages, more=more)

        return await self._run("thread read", work)

    async def get_attachment(self, provider_message_id: str, attachment: str) -> AttachmentUpload:
        """One attachment by its part id, with the file name and type the message declares."""
        if not _SECTION.fullmatch(attachment):
            raise EmailDeliveryError("IMAP message has no such attachment")
        maximum = self._settings.max_attachment_bytes
        limit = _encoded_limit(maximum)

        def work(connection: Any) -> AttachmentUpload:
            _, _, uid = self._locate(connection, provider_message_id)
            data = self._uid(connection, "FETCH", str(uid), "(UID BODYSTRUCTURE)")
            responses = [r for r in fetch_responses(data) if text(r.get("UID")) == str(uid)]
            if not responses:
                raise EmailDeliveryError("IMAP message was not found")
            parts = attachment_parts(body_parts(responses[0].get("BODYSTRUCTURE")))
            part = next((item for item in parts if item.section == attachment), None)
            if part is None:
                raise EmailDeliveryError("IMAP message has no such attachment")
            if part.size > limit:
                raise EmailDeliveryError("IMAP attachment exceeds the configured size limit")
            raw = self._part(connection, uid, part, limit + 1)
            if len(raw) > limit:
                raise EmailDeliveryError("IMAP attachment exceeds the configured size limit")
            content = decode_transfer(raw, part.encoding)
            if len(content) > maximum:
                raise EmailDeliveryError("IMAP attachment exceeds the configured size limit")
            return AttachmentUpload(
                filename=part.filename or "message.eml",
                media_type=part.media_type,
                data=content,
            )

        return await self._run("attachment download", work)
