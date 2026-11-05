"""Inspect OpenDocument files (ODT, ODS, ODP) before producing downstream-safe plain text.

Only what a reader sees is returned. Hidden content is left out and reported, as for Office
files: text a style or ``text:display="none"`` hides, ``text:hidden-text`` and hidden
paragraphs, text under 2 pt (``hidden_text.hidden_run``, ``hidden_text.tiny_font``); hidden
sheets, rows and columns (``hidden_text.hidden_sheet``, ``hidden_text.hidden_cells``); hidden
slides (``hidden_text.hidden_slide``). Comments and speaker notes are not extracted. Basic
macros and objects or images loaded from a remote address are findings. A sheet is cut at
``max_rows_per_sheet`` (``document.truncated``). Standard library only.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree

from algen_agent_tools.documents.inspection.core import (
    DocumentSecurityFinding,
    DocumentSpan,
    ExtractedDocument,
    FindingSeverity,
    classify_findings,
    normalize_document_text,
    sanitize_untrusted_text,
)
from algen_agent_tools.documents.inspection.ooxml import PackageLimits, open_package, parse_part

MEDIA_TYPES = {
    "application/vnd.oasis.opendocument.text": "text",
    "application/vnd.oasis.opendocument.spreadsheet": "spreadsheet",
    "application/vnd.oasis.opendocument.presentation": "presentation",
}
_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
}
_SKIPPED = {
    "office:annotation",
    "presentation:notes",
    "text:tracked-changes",
    "draw:image",
    "draw:object",
    "draw:object-ole",
}
_REMOTE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//)", re.I)
_MACRO = re.compile(r"^(?:Basic/[^/]+/[^/]+\.xml|Scripts/.+)$")
_POINTS = re.compile(r"^\s*([0-9.]+)\s*pt\s*$")
MAX_REPEAT = 1_000


def _tag(element: ElementTree.Element) -> str:
    namespace, _, local = element.tag.rpartition("}")
    prefix = next((key for key, value in _NS.items() if f"{{{value}" == namespace), "")
    return f"{prefix}:{local}"


def _get(element: ElementTree.Element, name: str) -> str | None:
    prefix, _, local = name.partition(":")
    return element.get(f"{{{_NS[prefix]}}}{local}")


class _Styles:
    """What each named style hides, following parent styles."""

    def __init__(self, roots: list[ElementTree.Element]) -> None:
        self._styles: dict[str, ElementTree.Element] = {}
        for root in roots:
            for style in root.iter(f"{{{_NS['style']}}}style"):
                name = _get(style, "style:name")
                if name:
                    self._styles[name] = style

    def _chain(self, name: str | None) -> list[ElementTree.Element]:
        chain: list[ElementTree.Element] = []
        while name and name in self._styles and len(chain) < 10:
            style = self._styles[name]
            chain.append(style)
            name = _get(style, "style:parent-style-name")
        return chain

    def _property(self, name: str | None, properties: str, attribute: str) -> str | None:
        for style in self._chain(name):
            for element in style.iter(f"{{{_NS['style']}}}{properties}"):
                value = _get(element, attribute)
                if value is not None:
                    return value
        return None

    def hidden_text(self, name: str | None) -> bool:
        return self._property(name, "text-properties", "text:display") == "none"

    def tiny(self, name: str | None) -> bool:
        size = _POINTS.match(self._property(name, "text-properties", "fo:font-size") or "")
        return bool(size) and float(size.group(1)) < 2.0  # type: ignore[union-attr]

    def hidden_table(self, name: str | None) -> bool:
        return self._property(name, "table-properties", "table:display") == "false"

    def hidden_slide(self, name: str | None) -> bool:
        value = self._property(name, "drawing-page-properties", "presentation:visibility")
        return value == "hidden"


class _Reader:
    def __init__(self, styles: _Styles, max_rows: int) -> None:
        self.styles = styles
        self.max_rows = max_rows
        self.hidden: dict[str, list[str]] = {}
        self.images = 0
        self.remote: list[str] = []

    def _hide(self, kind: str, text: str) -> None:
        text = normalize_document_text(text)
        if text:
            self.hidden.setdefault(kind, []).append(text)

    def inline(self, element: ElementTree.Element, out: list[str]) -> None:
        """The visible text of a paragraph's content into ``out``; hidden runs are kept apart."""
        if element.text:
            out.append(element.text)
        for child in element:
            tag = _tag(child)
            if tag == "text:s":
                out.append(" " * min(int(_get(child, "text:c") or 1), 100))
            elif tag == "text:tab":
                out.append("\t")
            elif tag == "text:line-break":
                out.append("\n")
            elif tag == "text:hidden-text":
                self._hide("hidden_text.hidden_run", _get(child, "text:string-value") or "")
            elif tag in _SKIPPED or tag == "text:note-citation":
                self._media(child)
            else:
                style = _get(child, "text:style-name")
                if self.styles.hidden_text(style) or self.styles.tiny(style):
                    parts: list[str] = []
                    self.inline(child, parts)
                    kind = "hidden_text.hidden_run"
                    if not self.styles.hidden_text(style):
                        kind = "hidden_text.tiny_font"
                    self._hide(kind, "".join(parts))
                else:
                    self.inline(child, out)
            if child.tail:
                out.append(child.tail)

    def _media(self, element: ElementTree.Element) -> None:
        for item in element.iter():
            if _tag(item) in {"draw:image", "draw:object", "draw:object-ole"}:
                href = _get(item, "xlink:href") or ""
                if _REMOTE.match(href):
                    self.remote.append(href[:300])
                elif _tag(item) == "draw:image":
                    self.images += 1

    def paragraph(self, element: ElementTree.Element) -> str | None:
        parts: list[str] = []
        self.inline(element, parts)
        text = normalize_document_text("".join(parts))
        style = _get(element, "text:style-name")
        hidden = any(
            _get(field, "text:is-hidden") == "true"
            for field in element.iter(f"{{{_NS['text']}}}hidden-paragraph")
        )
        if hidden or self.styles.hidden_text(style) or self.styles.tiny(style):
            kind = "hidden_text.tiny_font" if self.styles.tiny(style) else "hidden_text.hidden_run"
            self._hide(kind, text)
            return None
        return text or None

    def blocks(self, element: ElementTree.Element, lines: list[str]) -> None:
        """Visible lines of a body, section, cell or text box."""
        for child in element:
            tag = _tag(child)
            if tag in {"text:p", "text:h"}:
                text = self.paragraph(child)
                if text:
                    lines.append(text)
            elif tag in _SKIPPED:
                self._media(child)
            elif tag == "text:section" and (
                _get(child, "text:display") == "none"
                or self.styles.hidden_text(_get(child, "text:style-name"))
            ):
                hidden: list[str] = []
                self.blocks(child, hidden)
                self._hide("hidden_text.hidden_run", "\n".join(hidden))
            elif tag == "table:table":
                lines.extend(self.table(child)[0])
            else:
                self.blocks(child, lines)

    def _cell(self, cell: ElementTree.Element) -> str:
        lines: list[str] = []
        self.blocks(cell, lines)
        return " ".join(lines)

    def table(self, table: ElementTree.Element) -> tuple[list[str], bool]:
        """A table's visible rows, cells joined with `` | ``, and whether it was cut."""
        hidden_columns: set[int] = set()
        column = 0
        for item in table.iter(f"{{{_NS['table']}}}table-column"):
            repeat = min(int(_get(item, "table:number-columns-repeated") or 1), MAX_REPEAT)
            if _get(item, "table:visibility") in {"collapse", "filter"}:
                hidden_columns.update(range(column, column + repeat))
            column += repeat
        rows: list[str] = []
        cut = False
        for row in table.iter(f"{{{_NS['table']}}}table-row"):
            cells: list[str] = []
            hidden_cells: list[str] = []
            index = 0
            for cell in row:
                if _tag(cell) not in {"table:table-cell", "table:covered-table-cell"}:
                    continue
                text = self._cell(cell)
                repeat = int(_get(cell, "table:number-columns-repeated") or 1)
                repeat = 1 if not text else min(repeat, MAX_REPEAT)
                for _ in range(repeat):
                    (hidden_cells if index in hidden_columns else cells).append(text)
                    index += 1
            while cells and not cells[-1]:
                cells.pop()
            hidden_row = _get(row, "table:visibility") in {"collapse", "filter"}
            if hidden_row:
                hidden_cells.extend(cells)
                cells = []
            if any(hidden_cells):
                self._hide("hidden_text.hidden_cells", " ".join(c for c in hidden_cells if c))
            if not cells:
                continue
            if len(rows) >= self.max_rows:
                cut = True
                break
            repeat = 1 if hidden_row else min(int(_get(row, "table:number-rows-repeated") or 1), 10)
            rows.extend([" | ".join(cells)] * repeat)
        return rows[: self.max_rows], cut


class OdfDocumentExtractor:
    """Bounded, dependency-free OpenDocument inspection with hidden-content findings."""

    def __init__(
        self,
        *,
        max_bytes: int = 10_000_000,
        max_characters: int = 250_000,
        max_rows_per_sheet: int = 5_000,
        limits: PackageLimits | None = None,
    ) -> None:
        self._limits = limits or PackageLimits(max_bytes=max_bytes)
        self._max_characters = max_characters
        self._max_rows = max_rows_per_sheet

    def extract(self, data: bytes, *, filename: str) -> ExtractedDocument:
        del filename
        with open_package(data, "OpenDocument", self._limits) as archive:
            names = set(archive.namelist())
            media_type = (
                archive.read("mimetype").decode("ascii", "replace").strip()
                if "mimetype" in names
                else ""
            )
            kind = MEDIA_TYPES.get(media_type)
            if kind is None or "content.xml" not in names:
                raise ValueError("file content is not an OpenDocument text, sheet or slides file")
            if "META-INF/manifest.xml" in names:
                manifest = parse_part(archive.read("META-INF/manifest.xml"), "OpenDocument")
                if manifest.find(f".//{{{_NS['manifest']}}}encryption-data") is not None:
                    raise ValueError("the OpenDocument file is encrypted")
            content = parse_part(archive.read("content.xml"), "OpenDocument")
            roots = [content]
            if "styles.xml" in names:
                roots.append(parse_part(archive.read("styles.xml"), "OpenDocument"))
            macros = any(_MACRO.match(name) for name in names)
        reader = _Reader(_Styles(roots), self._max_rows)
        findings: list[DocumentSecurityFinding] = []
        pages: list[str] = []
        body = content.find(f"{{{_NS['office']}}}body")
        container = body[0] if body is not None and len(body) else None
        if container is None:
            raise ValueError("the OpenDocument file has no body")
        if kind == "spreadsheet":
            for number, sheet in enumerate(container.iter(f"{{{_NS['table']}}}table"), 1):
                rows, cut = reader.table(sheet)
                name = _get(sheet, "table:name") or f"Sheet{number}"
                text = "\n".join([f"--- Sheet: {name} ---", *rows]) if rows else ""
                if reader.styles.hidden_table(_get(sheet, "table:style-name")):
                    if rows:
                        findings.append(_hidden("hidden_text.hidden_sheet", number, rows))
                    pages.append("")
                    continue
                pages.append(text)
                if cut:
                    findings.append(
                        DocumentSecurityFinding(
                            kind="document.truncated",
                            severity=FindingSeverity.LOW,
                            message=f"Only the first {self._max_rows} rows of the sheet were read.",
                            page=number,
                        )
                    )
        elif kind == "presentation":
            for number, slide in enumerate(container.iter(f"{{{_NS['draw']}}}page"), 1):
                lines: list[str] = []
                reader.blocks(slide, lines)
                if reader.styles.hidden_slide(_get(slide, "draw:style-name")):
                    if lines:
                        findings.append(_hidden("hidden_text.hidden_slide", number, lines))
                    pages.append("")
                    continue
                pages.append("\n".join(lines))
        else:
            lines = []
            reader.blocks(container, lines)
            pages.append("\n".join(lines))
        for hidden_kind, texts in reader.hidden.items():
            message = (
                "Text uses an unusually small font."
                if hidden_kind == "hidden_text.tiny_font"
                else "Spreadsheet cells are in hidden rows or columns."
                if hidden_kind == "hidden_text.hidden_cells"
                else "Text is formatted as hidden."
            )
            findings.append(
                DocumentSecurityFinding(
                    kind=hidden_kind,
                    severity=FindingSeverity.HIGH,
                    message=message,
                    evidence=" ".join(texts)[:300],
                )
            )
        if macros:
            findings.append(
                DocumentSecurityFinding(
                    kind="document.macros",
                    severity=FindingSeverity.CRITICAL,
                    message="The document carries a macro project.",
                )
            )
        for href in reader.remote[:5]:
            findings.append(
                DocumentSecurityFinding(
                    kind="document.external_reference",
                    severity=FindingSeverity.HIGH,
                    message="The document loads remote content when opened.",
                    evidence=href,
                )
            )
        visible = "\n\n".join(page for page in pages if page)
        hidden_text = "\n".join(text for texts in reader.hidden.values() for text in texts)
        if len(visible) + len(hidden_text) > self._max_characters:
            raise ValueError(
                f"OpenDocument exceeds {self._max_characters} extracted character limit"
            )
        if reader.images and not visible.strip():
            findings.append(
                DocumentSecurityFinding(
                    kind="document.ocr_required",
                    severity=FindingSeverity.MEDIUM,
                    message="The document contains images but no text; OCR review is required.",
                )
            )
        elif reader.images:
            findings.append(
                DocumentSecurityFinding(
                    kind="document.images_excluded",
                    severity=FindingSeverity.LOW,
                    message="Embedded images were excluded from downstream screening.",
                )
            )
        sanitized, visible_findings = sanitize_untrusted_text(visible)
        findings.extend(visible_findings)
        spans = [
            DocumentSpan(text=page, page=number) for number, page in enumerate(pages, 1) if page
        ]
        if hidden_text:
            spans.append(DocumentSpan(text=hidden_text, page=1, suspicious=True))
        unique, disposition = classify_findings(findings)
        return ExtractedDocument(
            media_type=media_type,
            text="\n\n".join(span.text for span in spans),
            sanitized_text=sanitized,
            spans=tuple(spans),
            findings=unique,
            disposition=disposition,
            page_count=max(1, len(pages)),
            image_count=reader.images,
        )


def _hidden(kind: str, page: int, lines: list[str]) -> DocumentSecurityFinding:
    noun = "sheet" if kind.endswith("sheet") else "slide"
    return DocumentSecurityFinding(
        kind=kind,
        severity=FindingSeverity.HIGH,
        message=f"A hidden {noun} holds text.",
        page=page,
        evidence=" ".join(lines)[:300],
    )
