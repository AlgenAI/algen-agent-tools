"""The parts of IMAP (RFC 3501) the IMAP connector reads: FETCH responses, BODYSTRUCTURE,
transfer encodings and command arguments. Nothing here talks to a server.

``imaplib`` returns a FETCH response as a list whose items are either ``bytes`` (response text)
or ``(text, literal)`` tuples, where ``text`` ends with the literal's ``{size}`` marker. This
module turns that back into values: lists, ``bytes`` for quoted strings and literals, ``str``
for atoms and ``None`` for NIL.
"""

from __future__ import annotations

import base64
import binascii
import codecs
import quopri
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import collapse_rfc2231_value, decode_params
from email.utils import unquote as unquote_param
from itertools import takewhile

Value = list["Value"] | bytes | str | None

MAX_PARTS = 500
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

_LITERAL_MARK = re.compile(rb"\{(\d+)\+?\}\s*$")
_INTERNALDATE = re.compile(
    r"^\s*(\d{1,2})-([A-Za-z]{3})-(\d{4}) (\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})\s*$"
)
_MEDIA_TYPE = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_STOP = frozenset(b' ()"\r\n')


# Command arguments


def quote(value: str) -> str:
    """``value`` as an IMAP quoted string. Only printable ASCII is accepted, so an argument can
    never end a command line or start a literal."""
    if any(not 0x20 <= ord(char) <= 0x7E for char in value):
        raise ValueError("IMAP arguments must be printable ASCII")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def unquote(value: str) -> str:
    """The inverse of :func:`quote`."""
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return re.sub(r"\\(.)", r"\1", value[1:-1])
    return value


def search_date(day: date) -> str:
    """A date as IMAP SEARCH takes it: ``7-Oct-2026``, independent of the locale."""
    return f"{day.day}-{MONTHS[day.month - 1]}-{day.year}"


def internaldate_ms(value: Value) -> int:
    """Milliseconds since the epoch from an INTERNALDATE such as ``17-Jul-1996 02:44:25 -0700``;
    0 when it is missing or malformed."""
    text = value.decode("ascii", "replace") if isinstance(value, bytes) else str(value or "")
    found = _INTERNALDATE.match(text)
    if found is None or found.group(2).title() not in MONTHS:
        return 0
    day, month, year, hour, minute, second, sign, off_h, off_m = found.groups()
    offset = timedelta(hours=int(off_h), minutes=int(off_m)) * (1 if sign == "+" else -1)
    try:
        moment = datetime(
            int(year),
            MONTHS.index(month.title()) + 1,
            int(day),
            int(hour),
            int(minute),
            int(second),
            tzinfo=timezone(offset),
        )
    except ValueError:
        return 0
    return max(0, int(moment.astimezone(UTC).timestamp() * 1000))


# Responses


def _tokens(text: bytes) -> Iterator[tuple[str, bytes]]:
    index, end = 0, len(text)
    while index < end:
        char = text[index]
        if char in b" \t\r\n":
            index += 1
        elif char in b"()":
            yield chr(char), b""
            index += 1
        elif char == ord('"'):
            index += 1
            value = bytearray()
            while index < end and text[index] != ord('"'):
                if text[index] == ord("\\") and index + 1 < end:
                    index += 1
                value.append(text[index])
                index += 1
            if index >= end:
                raise ValueError("unterminated quoted string in IMAP response")
            index += 1
            yield "string", bytes(value)
        else:
            # An atom, including a section such as BODY[HEADER.FIELDS (FROM TO)]<0>.
            start, depth = index, 0
            while index < end:
                char = text[index]
                if char == ord("["):
                    depth += 1
                elif char == ord("]"):
                    depth -= 1
                elif depth <= 0 and char in _STOP:
                    break
                index += 1
            yield "atom", text[start:index]


def _stream(data: Sequence[object]) -> list[tuple[str, bytes]]:
    tokens: list[tuple[str, bytes]] = []
    for item in data:
        if isinstance(item, tuple) and len(item) == 2:
            text, literal = item
            if not isinstance(text, bytes) or not isinstance(literal, bytes):
                raise ValueError("malformed IMAP literal")
            marker = _LITERAL_MARK.search(text)
            if marker is None:
                raise ValueError("IMAP literal without a size marker")
            tokens.extend(_tokens(text[: marker.start()]))
            tokens.append(("string", literal))
        elif isinstance(item, bytes):
            tokens.extend(_tokens(item))
        elif item is not None:
            raise ValueError("malformed IMAP response")
    return tokens


def _value(tokens: list[tuple[str, bytes]], position: int) -> tuple[Value, int]:
    kind, raw = tokens[position]
    if kind == "(":
        items: list[Value] = []
        position += 1
        while True:
            if position >= len(tokens):
                raise ValueError("unbalanced parentheses in IMAP response")
            if tokens[position][0] == ")":
                return items, position + 1
            item, position = _value(tokens, position)
            items.append(item)
    if kind == ")":
        raise ValueError("unbalanced parentheses in IMAP response")
    if kind == "string":
        return raw, position + 1
    atom = raw.decode("ascii", "replace")
    return (None if atom.upper() == "NIL" else atom), position + 1


def parse_value(text: bytes) -> Value:
    """One value from response text, for example a BODYSTRUCTURE."""
    tokens = list(_tokens(text))
    value, position = _value(tokens, 0)
    if position != len(tokens):
        raise ValueError("trailing data in IMAP value")
    return value


def fetch_responses(data: Sequence[object]) -> list[dict[str, Value]]:
    """Each ``<seq> (<name> <value> ...)`` FETCH response as a dict keyed by upper-case name."""
    tokens = _stream(data)
    responses: list[dict[str, Value]] = []
    position = 0
    while position < len(tokens):
        if tokens[position][0] != "atom":
            raise ValueError("FETCH response without a sequence number")
        value, position = _value(tokens, position + 1)
        if not isinstance(value, list) or len(value) % 2:
            raise ValueError("malformed FETCH response")
        responses.append({str(value[i]).upper(): value[i + 1] for i in range(0, len(value), 2)})
    return responses


def section(response: Mapping[str, Value], prefix: str) -> bytes:
    """The body section whose name starts with ``prefix`` (``BODY[HEADER``, ``BODY[``)."""
    for name, value in response.items():
        if name.startswith(prefix):
            return value if isinstance(value, bytes) else b""
    return b""


def text(value: Value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value if isinstance(value, str) else ""


def strings(value: Value) -> tuple[str, ...]:
    """A parenthesised list of atoms or strings, such as FLAGS or X-GM-LABELS."""
    return tuple(text(item) for item in value) if isinstance(value, list) else ()


# BODYSTRUCTURE


@dataclass(frozen=True)
class BodyPart:
    """One leaf of a message's MIME tree, as BODYSTRUCTURE describes it."""

    section: str
    """The part's IMAP section number, as ``BODY[<section>]`` fetches it."""
    media_type: str
    encoding: str
    size: int
    """Encoded size in octets."""
    params: dict[str, str] = field(default_factory=dict)
    disposition: str | None = None
    disposition_params: dict[str, str] = field(default_factory=dict)

    @property
    def charset(self) -> str:
        return self.params.get("charset", "utf-8")

    @property
    def filename(self) -> str | None:
        """The declared file name, decoded (RFC 2231 and RFC 2047), without any path."""
        name = _param(self.disposition_params, "filename") or _param(self.params, "name")
        if not name:
            return None
        try:
            name = str(make_header(decode_header(name)))
        except (ValueError, LookupError, UnicodeError):
            pass
        name = _CONTROL.sub("", re.split(r"[/\\]", name)[-1]).strip()[:255]
        return name if name not in ("", ".", "..") else None

    @property
    def is_attachment(self) -> bool:
        return self.filename is not None or self.media_type == "message/rfc822"


def _param(params: Mapping[str, str], name: str) -> str | None:
    """A parameter, joining RFC 2231 continuations and decoding ``name*`` values."""
    if name in params:
        return params[name]
    related = [(key, value) for key, value in params.items() if key.startswith(f"{name}*")]
    if not related:
        return None
    try:
        for key, value in decode_params([("", ""), *related])[1:]:
            if key == name:
                return unquote_param(str(collapse_rfc2231_value(value)))
    except (ValueError, LookupError, UnicodeError):
        return None
    return None


def _params(value: Value) -> dict[str, str]:
    if not isinstance(value, list):
        return {}
    return {text(value[i]).casefold(): text(value[i + 1]) for i in range(0, len(value) - 1, 2)}


def _size(value: Value) -> int:
    try:
        return max(0, int(text(value)))
    except ValueError:
        return 0


def body_parts(structure: Value) -> list[BodyPart]:
    """The leaves of a BODYSTRUCTURE in reading order (at most :data:`MAX_PARTS`). A forwarded
    ``message/rfc822`` is one leaf; its own parts are not listed."""
    parts: list[BodyPart] = []
    _walk(structure, "", parts)
    return parts


def _walk(node: Value, number: str, parts: list[BodyPart]) -> None:
    if len(parts) >= MAX_PARTS:
        return
    if not isinstance(node, list) or not node:
        raise ValueError("malformed BODYSTRUCTURE")
    if isinstance(node[0], list):
        children = list(takewhile(lambda child: isinstance(child, list), node))
        for index, child in enumerate(children, 1):
            _walk(child, f"{number}.{index}" if number else str(index), parts)
        return
    if len(node) < 7:
        raise ValueError("malformed BODYSTRUCTURE")
    kind, subtype = text(node[0]).casefold(), text(node[1]).casefold()
    media_type = f"{kind}/{subtype}"
    if not _MEDIA_TYPE.fullmatch(media_type):
        media_type = "application/octet-stream"
    if kind == "text":
        extension = 8
    elif media_type == "message/rfc822":
        extension = 10
    else:
        extension = 7
    disposition, disposition_params = None, {}
    found = node[extension + 1] if len(node) > extension + 1 else None
    if isinstance(found, list) and found:
        disposition = text(found[0]).casefold()
        disposition_params = _params(found[1] if len(found) > 1 else None)
    parts.append(
        BodyPart(
            section=number or "1",
            media_type=media_type,
            encoding=text(node[5]).casefold() or "7bit",
            size=_size(node[6]),
            params=_params(node[2]),
            disposition=disposition,
            disposition_params=disposition_params,
        )
    )


def text_part(parts: Sequence[BodyPart]) -> BodyPart | None:
    """The part that holds the message's text: the first inline text/plain part, else the
    first inline text/html part."""
    inline = [part for part in parts if part.filename is None and part.disposition != "attachment"]
    for media_type in ("text/plain", "text/html"):
        for part in inline:
            if part.media_type == media_type:
                return part
    return None


def attachment_parts(parts: Sequence[BodyPart]) -> list[BodyPart]:
    return [part for part in parts if part.is_attachment]


# Content


def decode_transfer(data: bytes, encoding: str) -> bytes:
    """A part's bytes with its Content-Transfer-Encoding removed. A base64 body cut short by a
    partial fetch decodes up to the last whole group."""
    if encoding == "base64":
        cleaned = re.sub(rb"[^A-Za-z0-9+/=]", b"", data)
        cleaned = cleaned[: len(cleaned) - len(cleaned) % 4]
        try:
            return base64.b64decode(cleaned)
        except binascii.Error as exc:
            raise ValueError("malformed base64 in IMAP part") from exc
    if encoding == "quoted-printable":
        return quopri.decodestring(data)
    return data


def decode_text(data: bytes, charset: str) -> str:
    try:
        codecs.lookup(charset)
    except LookupError:
        charset = "utf-8"
    return data.decode(charset, "replace")
