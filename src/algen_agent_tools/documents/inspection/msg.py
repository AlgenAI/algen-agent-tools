"""Inspect Outlook message files (``.msg``) before producing downstream-safe plain text.

A ``.msg`` file is an OLE compound file of MAPI properties. This module reads the sender,
recipients, date, subject, the plain-text or HTML body and the attachments (attached messages
included) from it with ``olefile``, builds the equivalent RFC 5322 message, and inspects that with
:class:`~algen_agent_tools.documents.inspection.email.EmailDocumentExtractor`, so the limits,
the recursion and the findings are the same as for ``.eml``. A message whose only body is RTF
reports its body as unread. It needs the ``msg`` extra (``olefile``).
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from typing import TYPE_CHECKING, Any

from algen_agent_tools.documents.inspection.core import ExtractedDocument

if TYPE_CHECKING:
    from algen_agent_tools.documents.inspection.ocr import TextRecognizer

UNREAD_BODY_HEADER = "X-Algen-Unread-Body"
"""Set on a converted message whose only body was RTF, which is not read."""
MAX_EMBEDDED_DEPTH = 8
MAX_ATTACHMENTS = 100

_STRING8, _UNICODE, _BINARY, _OBJECT = "001E", "001F", "0102", "000D"
_SUBJECT, _SENDER_NAME, _SENDER_EMAIL, _SENDER_SMTP = "0037", "0C1A", "0C1F", "5D01"
_DISPLAY_TO, _DISPLAY_CC, _HEADERS = "0E04", "0E03", "007D"
_BODY, _HTML, _RTF = "1000", "1013", "1009"
_ATTACH_DATA, _ATTACH_LONG_NAME, _ATTACH_NAME, _ATTACH_MIME = "3701", "3707", "3704", "370E"
_RECIPIENT_NAME, _RECIPIENT_EMAIL, _RECIPIENT_SMTP = "3001", "3003", "39FE"
_SUBMIT_TIME, _DELIVERY_TIME, _RECIPIENT_TYPE, _CODEPAGE = 0x0039, 0x0E06, 0x0C15, 0x3FFD
_HEADER_BYTES = {"root": 32, "embedded": 24, "child": 8}
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=UTC)
_DATE_HEADER = re.compile(r"^Date:[ \t]*(.+(?:\r?\n[ \t].+)*)", re.I | re.M)
_PLAIN_NAME = re.compile(r"[\r\n\x00]")


class _Message:
    """The MAPI properties of one message (the file, or a message attached to it)."""

    def __init__(self, ole: Any, prefix: str, kind: str) -> None:
        self._ole = ole
        self._prefix = prefix
        self._properties = self._fixed(prefix, _HEADER_BYTES[kind])
        codepage = self._properties.get(_CODEPAGE)
        self._codepage = f"cp{int.from_bytes(codepage[:4], 'little')}" if codepage else "cp1252"

    def _fixed(self, prefix: str, header: int) -> dict[int, bytes]:
        """Fixed-size property values by property id, from ``__properties_version1.0``."""
        path = f"{prefix}__properties_version1.0"
        if not self._ole.exists(path):
            return {}
        data = self._ole.openstream(path).read()
        values: dict[int, bytes] = {}
        for offset in range(header, len(data) - 15, 16):
            tag = int.from_bytes(data[offset : offset + 4], "little")
            values.setdefault(tag >> 16, data[offset + 8 : offset + 16])
        return values

    def _stream(self, prefix: str, property_id: str, kind: str) -> bytes | None:
        path = f"{prefix}__substg1.0_{property_id}{kind}"
        return self._ole.openstream(path).read() if self._ole.exists(path) else None

    def text(self, property_id: str, prefix: str | None = None) -> str:
        where = self._prefix if prefix is None else prefix
        unicode = self._stream(where, property_id, _UNICODE)
        if unicode is not None:
            return unicode.decode("utf-16-le", errors="replace").rstrip("\x00")
        legacy = self._stream(where, property_id, _STRING8)
        if legacy is not None:
            try:
                return legacy.decode(self._codepage, errors="replace").rstrip("\x00")
            except LookupError:
                return legacy.decode("cp1252", errors="replace").rstrip("\x00")
        return ""

    def binary(self, property_id: str, prefix: str | None = None) -> bytes | None:
        return self._stream(self._prefix if prefix is None else prefix, property_id, _BINARY)

    def date(self) -> str | None:
        headers = self.text(_HEADERS)
        found = _DATE_HEADER.search(headers)
        if found:
            return " ".join(found.group(1).split())
        for property_id in (_DELIVERY_TIME, _SUBMIT_TIME):
            value = self._properties.get(property_id)
            if value:
                ticks = int.from_bytes(value, "little")
                try:
                    moment = _FILETIME_EPOCH + timedelta(microseconds=ticks // 10)
                except OverflowError:
                    continue
                return format_datetime(moment)
        return None

    def children(self, marker: str) -> list[str]:
        """The storages (``__attach…`` or ``__recip…``) directly under this message."""
        depth = self._prefix.count("/")
        names = {
            "/".join(entry[: depth + 1]) + "/"
            for entry in self._ole.listdir(streams=True, storages=True)
            if len(entry) > depth
            and "/".join(entry[:depth]) + ("/" if depth else "") == self._prefix
            and entry[depth].startswith(marker)
        }
        return sorted(names)

    def recipients(self) -> tuple[str, str]:
        """The To and Cc display text, from the recipient table when it has addresses."""
        to: list[str] = []
        cc: list[str] = []
        for storage in self.children("__recip_version1.0_#"):
            kind = self._fixed(storage, _HEADER_BYTES["child"]).get(_RECIPIENT_TYPE)
            name = self.text(_RECIPIENT_NAME, storage)
            address = self.text(_RECIPIENT_SMTP, storage) or self.text(_RECIPIENT_EMAIL, storage)
            entry = _address(name, address)
            if entry:
                (cc if kind and kind[0] == 2 else to).append(entry)
        return (
            ", ".join(to) or self.text(_DISPLAY_TO).replace(";", ","),
            ", ".join(cc) or self.text(_DISPLAY_CC).replace(";", ","),
        )

    def sender(self) -> str:
        address = self.text(_SENDER_SMTP) or self.text(_SENDER_EMAIL)
        return _address(self.text(_SENDER_NAME), address)


def _address(name: str, address: str) -> str:
    name, address = " ".join(name.split()), address.strip()
    if "@" not in address:
        return name
    return f"{name} <{address}>" if name and name != address else address


def _header(value: str) -> str:
    return " ".join(_PLAIN_NAME.sub(" ", value).split())[:998]


def _to_email(ole: Any, prefix: str, kind: str, depth: int, budget: list[int]) -> EmailMessage:
    source = _Message(ole, prefix, kind)
    message = EmailMessage()
    to, cc = source.recipients()
    for header, value in (
        ("From", source.sender()),
        ("To", to),
        ("Cc", cc),
        ("Date", source.date() or ""),
        ("Subject", source.text(_SUBJECT)),
    ):
        if _header(value):
            message[header] = _header(value)
    if "Subject" not in message:
        message["Subject"] = ""
    body = source.text(_BODY)
    html = source.binary(_HTML)
    if body.strip():
        message.set_content(body)
    elif html:
        message.set_content(html.decode("utf-8", errors="replace"), subtype="html")
    elif source.text(_HTML).strip():
        message.set_content(source.text(_HTML), subtype="html")
    elif source.binary(_RTF):
        message[UNREAD_BODY_HEADER] = "rtf"
    for storage in source.children("__attach_version1.0_#"):
        if budget[0] <= 0:
            break
        budget[0] -= 1
        name = _header(
            source.text(_ATTACH_LONG_NAME, storage) or source.text(_ATTACH_NAME, storage)
        )
        embedded = f"{storage}__substg1.0_{_ATTACH_DATA}{_OBJECT}/"
        if ole.exists(embedded.rstrip("/")):
            if depth >= MAX_EMBEDDED_DEPTH:
                continue
            inner = _to_email(ole, embedded, "embedded", depth + 1, budget)
            message.add_attachment(inner, filename=name or "message.eml")
            continue
        data = source.binary(_ATTACH_DATA, storage)
        if not data:
            continue
        media_type = source.text(_ATTACH_MIME, storage).strip().lower()
        maintype, _, subtype = media_type.partition("/")
        if not maintype or not subtype or maintype in {"multipart", "message"}:
            maintype, subtype = "application", "octet-stream"
        message.add_attachment(
            data, maintype=maintype, subtype=subtype, filename=name or "attachment"
        )
    return message


def msg_to_email(data: bytes) -> EmailMessage:
    """The RFC 5322 message equivalent to an Outlook ``.msg`` file: headers, body and
    attachments, with attached messages as ``message/rfc822`` parts."""
    try:
        import olefile
    except ImportError as exc:
        raise ImportError("install algen-agent-tools[msg] for .msg files", name="olefile") from exc
    if not olefile.isOleFile(io.BytesIO(data)):
        raise ValueError("file content is not an Outlook message")
    try:
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            if not ole.exists(f"__substg1.0_{_SUBJECT}{_UNICODE}") and not ole.exists(
                "__properties_version1.0"
            ):
                raise ValueError("file content is not an Outlook message")
            return _to_email(ole, "", "root", 0, [MAX_ATTACHMENTS])
    except (OSError, EOFError, IndexError, KeyError, TypeError) as exc:
        raise ValueError(f"the Outlook message cannot be read ({type(exc).__name__})") from None


class MsgDocumentExtractor:
    """Bounded ``.msg`` inspection: the message is converted, then read as an email."""

    def __init__(
        self,
        *,
        max_bytes: int = 10_000_000,
        max_depth: int = 2,
        max_attachments: int = 20,
        max_characters: int = 250_000,
        ocr: TextRecognizer | None = None,
        ocr_max_pages: int = 3,
    ) -> None:
        from algen_agent_tools.documents.inspection.email import EmailDocumentExtractor

        self._max_bytes = max_bytes
        self._email = EmailDocumentExtractor(
            max_bytes=max_bytes * 2,
            max_depth=max_depth,
            max_attachments=max_attachments,
            max_characters=max_characters,
            ocr=ocr,
            ocr_max_pages=ocr_max_pages,
        )

    def extract(self, data: bytes, *, filename: str) -> ExtractedDocument:
        if len(data) > self._max_bytes:
            raise ValueError(f"document exceeds {self._max_bytes} byte limit")
        converted = msg_to_email(data).as_bytes()
        return self._email.extract(converted, filename=filename).model_copy(
            update={"media_type": "application/vnd.ms-outlook"}
        )
