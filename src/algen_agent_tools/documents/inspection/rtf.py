"""Inspect RTF documents before producing downstream-safe plain text.

A bounded tokenizer reads the text a reader sees: paragraphs, line breaks, tabs and table cells
(joined with `` | ``), with ``\\'hh`` and ``\\u`` characters decoded. Font tables, styles,
document information, headers and footers, pictures and embedded objects are not text. Hidden
text (``\\v``) and text under 2 pt are left out and reported (``hidden_text.hidden_run``,
``hidden_text.tiny_font``). An embedded OLE object (``\\object``), a classic exploit carrier,
is ``document.embedded_object``; a field that loads a remote file (``INCLUDEPICTURE``,
``INCLUDETEXT``) is ``document.external_reference``. Standard library only.
"""

from __future__ import annotations

import re

from algen_agent_tools.documents.inspection.core import (
    DocumentSecurityFinding,
    DocumentSpan,
    ExtractedDocument,
    FindingSeverity,
    classify_findings,
    normalize_document_text,
    sanitize_untrusted_text,
)

_TOKEN = re.compile(
    rb"\\'([0-9a-fA-F]{2})|\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\([^a-zA-Z])|([{}])|([^\\{}\r\n]+)|[\r\n]+"
)
# Groups whose text is not the document's body text.
_DESTINATIONS = {
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "headerl",
    "headerr", "headerf", "footer", "footerl", "footerr", "footerf", "listtable",
    "listoverridetable", "revtbl", "rsidtbl", "generator", "themedata", "colorschememapping",
    "datastore", "latentstyles", "xmlnstbl", "fldinst", "filetbl", "pgdsctbl", "annotation",
    "atnid", "atnauthor", "footnote", "bkmkstart", "bkmkend", "nonshppict", "shpinst",
}  # fmt: skip
_REMOTE_FIELD = re.compile(r"\b(?:INCLUDEPICTURE|INCLUDETEXT|LINK|IMPORT)\b\s+\"?([^\"\s]+)", re.I)
MAX_DEPTH = 200


class _State:
    __slots__ = ("hidden", "points", "skip", "skip_count", "uc")

    def __init__(self) -> None:
        self.skip = False
        self.hidden = False
        self.points = 12.0
        self.uc = 1
        self.skip_count = 0

    def copy(self) -> _State:
        state = _State()
        state.skip, state.hidden, state.points, state.uc = (
            self.skip,
            self.hidden,
            self.points,
            self.uc,
        )
        return state


class RtfDocumentExtractor:
    """Bounded, dependency-free RTF inspection with hidden-content findings."""

    def __init__(self, *, max_bytes: int = 5_000_000, max_characters: int = 250_000) -> None:
        self._max_bytes = max_bytes
        self._max_characters = max_characters

    def extract(self, data: bytes, *, filename: str) -> ExtractedDocument:
        del filename
        if len(data) > self._max_bytes:
            raise ValueError(f"document exceeds {self._max_bytes} byte limit")
        if data.lstrip()[:5] != b"{\\rtf":
            raise ValueError("file content is not an RTF document")
        codepage = "cp1252"
        found = re.search(rb"\\ansicpg(\d{3,5})", data[:4096])
        if found:
            codepage = f"cp{int(found.group(1))}"
            try:
                "".encode(codepage)
            except LookupError:
                codepage = "cp1252"
        visible: list[str] = []
        hidden: list[str] = []
        tiny: list[str] = []
        instructions: list[str] = []
        images = 0
        objects = 0
        stack: list[_State] = []
        state = _State()
        pending: list[int] = []  # \'hh bytes waiting to be decoded together
        destination_next = False

        def emit(text: str) -> None:
            if state.skip or not text:
                return
            if state.hidden:
                hidden.append(text)
            elif state.points < 2.0:
                tiny.append(text)
            else:
                visible.append(text)

        def flush() -> None:
            if pending:
                emit(bytes(pending).decode(codepage, errors="replace"))
                pending.clear()

        for match in _TOKEN.finditer(data):
            hex_byte, word, argument, symbol, brace, text = match.groups()
            if hex_byte is not None:
                if state.skip_count:
                    state.skip_count -= 1
                    continue
                pending.append(int(hex_byte, 16))
                continue
            flush()
            if brace == b"{":
                if len(stack) >= MAX_DEPTH:
                    raise ValueError("RTF groups are nested deeper than the limit")
                stack.append(state)
                state = state.copy()
                destination_next = False
            elif brace == b"}":
                state = stack.pop() if stack else state
            elif symbol is not None:
                if symbol == b"*":
                    destination_next = True
                elif symbol in (b"\\", b"{", b"}"):
                    emit(symbol.decode())
                elif symbol == b"~":
                    emit("\u00a0")
                elif symbol == b"-":
                    pass
            elif word is not None:
                name = word.decode()
                value = int(argument) if argument else None
                if destination_next or name in _DESTINATIONS:
                    if name == "pict":
                        images += 1
                    elif name == "object":
                        objects += 1
                    state.skip = True
                    destination_next = False
                    continue
                if name in {"par", "line", "sect", "page", "row"}:
                    emit("\n")
                elif name == "tab":
                    emit("\t")
                elif name == "cell":
                    emit(" | ")
                elif name == "v":
                    state.hidden = value != 0
                elif name == "fs" and value is not None:
                    state.points = value / 2
                elif name == "plain":
                    state.hidden, state.points = False, 12.0
                elif name == "uc" and value is not None:
                    state.uc = max(0, min(value, 4))
                elif name == "u" and value is not None:
                    emit(chr(value % 65536))  # RTF writes code points as signed 16-bit
                    state.skip_count = state.uc
                elif name in {"emdash", "endash"}:
                    emit("\u2014" if name == "emdash" else "\u2013")
                elif name in {"lquote", "rquote"}:
                    emit("\u2018" if name == "lquote" else "\u2019")
                elif name in {"ldblquote", "rdblquote"}:
                    emit("\u201c" if name == "ldblquote" else "\u201d")
                elif name == "bullet":
                    emit("\u2022")
            elif text is not None:
                chunk = text.decode(codepage, errors="replace")
                if state.skip_count:
                    drop = min(state.skip_count, len(chunk))
                    chunk, state.skip_count = chunk[drop:], state.skip_count - drop
                emit(chunk)
            if sum(map(len, visible)) + sum(map(len, hidden)) > self._max_characters:
                raise ValueError(f"RTF exceeds {self._max_characters} extracted character limit")
        flush()
        # Field instructions are skipped as text but read for remote includes.
        for field in re.finditer(rb"\\fldinst\s*(?:\{[^{}]*\})*\s*([^{}]*)", data):
            instructions.append(field.group(1).decode("latin-1", "replace"))
        findings: list[DocumentSecurityFinding] = []
        for kind, texts, message in (
            ("hidden_text.hidden_run", hidden, "Text is formatted as hidden."),
            ("hidden_text.tiny_font", tiny, "Text uses an unusually small font."),
        ):
            text = normalize_document_text("".join(texts))
            if text:
                findings.append(
                    DocumentSecurityFinding(
                        kind=kind,
                        severity=FindingSeverity.HIGH,
                        message=message,
                        evidence=text[:300],
                    )
                )
        if objects:
            findings.append(
                DocumentSecurityFinding(
                    kind="document.embedded_object",
                    severity=FindingSeverity.HIGH,
                    message="The document embeds an OLE object, which can run code when opened.",
                )
            )
        for instruction in instructions:
            remote = _REMOTE_FIELD.search(instruction)
            if remote and re.match(r"^(?:[a-z][a-z0-9+.-]*:|\\\\)", remote.group(1), re.I):
                findings.append(
                    DocumentSecurityFinding(
                        kind="document.external_reference",
                        severity=FindingSeverity.HIGH,
                        message="The document loads remote content when opened.",
                        evidence=remote.group(1)[:300],
                    )
                )
        lines = [
            normalize_document_text(line)
            for line in "".join(visible).replace(" | \n", "\n").split("\n")
        ]
        body = "\n".join(line for line in lines if line)
        if images and not body:
            findings.append(
                DocumentSecurityFinding(
                    kind="document.ocr_required",
                    severity=FindingSeverity.MEDIUM,
                    message="The document contains images but no text; OCR review is required.",
                )
            )
        elif images:
            findings.append(
                DocumentSecurityFinding(
                    kind="document.images_excluded",
                    severity=FindingSeverity.LOW,
                    message="Embedded images were excluded from downstream screening.",
                )
            )
        sanitized, visible_findings = sanitize_untrusted_text(body)
        findings.extend(visible_findings)
        spans = [DocumentSpan(text=body, page=1)] if body else []
        concealed = normalize_document_text("".join(hidden + tiny))
        if concealed:
            spans.append(DocumentSpan(text=concealed, page=1, suspicious=True))
        unique, disposition = classify_findings(findings)
        return ExtractedDocument(
            media_type="application/rtf",
            text="\n".join(span.text for span in spans),
            sanitized_text=sanitized,
            spans=tuple(spans),
            findings=unique,
            disposition=disposition,
            image_count=images,
        )
