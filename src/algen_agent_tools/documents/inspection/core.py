from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from algen_agent_tools.documents.inspection.ocr import render_page_png, validate_ocr_options

if TYPE_CHECKING:
    from algen_agent_tools.documents.inspection.ocr import TextRecognizer


class DocumentDisposition(StrEnum):
    PASS = "pass"
    REVIEW = "review"
    QUARANTINE = "quarantine"


class FindingSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DocumentSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    page: int = Field(ge=1)
    font_size: float | None = None
    color: int | None = None
    opacity: float | None = None
    bbox: tuple[float, float, float, float] | None = None
    suspicious: bool = False
    source: Literal["text", "ocr"] = "text"
    """``ocr`` when the text was recognized from an image rather than read from the file."""
    confidence: float | None = Field(default=None, ge=0, le=1)
    """The recognizer's confidence for an ``ocr`` span, when it reports one."""


COVERAGE_KINDS = frozenset(
    {
        "document.attachment_skipped",
        "document.attachment_unread",
        "document.body_unread",
        "document.images_excluded",
        "document.ocr_no_text",
        "document.ocr_required",
        "document.pages_skipped",
        "document.truncated",
    }
)
"""Finding kinds that report content the inspection did not read, not a security concern."""


class DocumentSecurityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    severity: FindingSeverity
    message: str
    page: int | None = None
    evidence: str | None = Field(default=None, max_length=300)
    part: str | None = Field(default=None, max_length=255)
    """The attachment the finding is in, for an email; ``None`` for the document itself."""
    category: Literal["security", "coverage"] = "security"
    """``coverage`` when the finding reports content that was not read (images, scanned pages,
    rows past a limit); it does not affect the disposition. Set from ``kind`` when omitted."""

    @model_validator(mode="before")
    @classmethod
    def _category_from_kind(cls, data: Any) -> Any:
        if isinstance(data, dict) and "category" not in data:
            kind = data.get("kind")
            return {**data, "category": "coverage" if kind in COVERAGE_KINDS else "security"}
        return data


class UnreadContent(BaseModel):
    """Content the inspection did not read: images, scanned pages, rows past a limit or email
    attachments."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["image", "page", "rows", "attachment", "body"]
    page: int | None = None
    """The page, slide or sheet, when known."""
    part: str | None = None
    """The email attachment it is in, or the attachment itself for ``attachment``."""


def _unread_content(findings: tuple[DocumentSecurityFinding, ...]) -> tuple[UnreadContent, ...]:
    unread: list[UnreadContent] = []
    for finding in findings:
        if finding.kind == "document.truncated":
            kind: Literal["image", "page", "rows", "attachment", "body"] = "rows"
        elif finding.kind in {"document.attachment_skipped", "document.attachment_unread"}:
            kind = "attachment"
        elif finding.kind == "document.body_unread":
            kind = "body"
        elif finding.kind == "document.pages_skipped" or (
            finding.kind == "document.ocr_required" and finding.page is not None
        ):
            kind = "page"
        elif finding.kind in COVERAGE_KINDS:
            kind = "image"
        else:
            continue
        unread.append(UnreadContent(kind=kind, page=finding.page, part=finding.part))
    return tuple(unread)


class DocumentPart(BaseModel):
    """One attachment of an email that was read, with what its own inspection found."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    filename: str = Field(max_length=255)
    media_type: str = Field(max_length=255)
    disposition: DocumentDisposition
    coverage: Literal["full", "partial", "none"]


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    media_type: str
    text: str
    sanitized_text: str
    spans: tuple[DocumentSpan, ...]
    findings: tuple[DocumentSecurityFinding, ...] = ()
    disposition: DocumentDisposition = DocumentDisposition.PASS
    page_count: int = Field(default=1, ge=1)
    image_count: int = Field(default=0, ge=0)
    coverage: Literal["full", "partial", "none"] = "full"
    """``full`` when everything was read; ``partial`` when some text was read and some content
    was not; ``none`` when content was found but no text could be read (a scan without OCR).
    Derived from the coverage findings when omitted."""
    unread: tuple[UnreadContent, ...] = ()
    """What was not read. Derived from the coverage findings when omitted."""
    needs_ocr: bool = False
    """Images or scanned pages hold text that only OCR can read. Derived when omitted."""
    parts: tuple[DocumentPart, ...] = ()
    """The attachments of an email that were read; their text follows the body's."""

    @model_validator(mode="before")
    @classmethod
    def _coverage_from_findings(cls, data: Any) -> Any:
        if not isinstance(data, dict) or {"coverage", "unread", "needs_ocr"} & data.keys():
            return data
        findings = tuple(
            item if isinstance(item, DocumentSecurityFinding) else DocumentSecurityFinding(**item)
            for item in data.get("findings", ())
        )
        unread = _unread_content(findings)
        if not unread:
            coverage = "full"
        elif str(data.get("text", "")).strip():
            coverage = "partial"
        else:
            coverage = "none"
        needs_ocr = any(finding.kind == "document.ocr_required" for finding in findings)
        return {**data, "coverage": coverage, "unread": unread, "needs_ocr": needs_ocr}


class DocumentExtractor(Protocol):
    def extract(self, data: bytes, *, filename: str) -> ExtractedDocument: ...


_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "instruction_override",
        re.compile(r"\b(ignore|disregard|forget)\b.{0,50}\b(previous|prior|above|system)\b", re.I),
    ),
    (
        "screening_bypass",
        re.compile(r"\b(bypass|override|skip)\b.{0,50}\b(screen|policy|guardrail|filter)\b", re.I),
    ),
    (
        "score_manipulation",
        re.compile(
            r"\b(rank|rate|score|select|hire)\b.{0,40}\b(100|maximum|highest|perfect|candidate)\b",
            re.I,
        ),
    ),
    (
        "prompt_exfiltration",
        re.compile(
            r"\b(reveal|print|repeat|expose)\b.{0,50}\b(system prompt|hidden instructions?|secrets?)\b",
            re.I,
        ),
    ),
    ("role_impersonation", re.compile(r"(?:^|\n)\s*(system|assistant|developer)\s*:", re.I)),
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d ().-]{7,}\d)(?!\w)")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
_ADDRESS_LINE = re.compile(
    r"(?:\baddress\s*:|\b\d{1,6}\s+[A-Za-z0-9 .'-]+\s(?:street|st|road|rd|avenue|ave|lane|ln|boulevard|blvd|drive|dr)\b)",
    re.I,
)
_DEMOGRAPHIC_LINE = re.compile(
    r"\b(?:gender|sex|ethnicity|race|religion|marital status|nationality)\s*:", re.I
)


def normalize_document_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("\x00", "")
    normalized = "".join(
        character for character in normalized if unicodedata.category(character) != "Cf"
    )
    return "\n".join(" ".join(line.split()) for line in normalized.splitlines()).strip()


def normalize_pdf_opacity(value: object) -> float:
    """Normalize PDF extractor alpha values expressed as either 0..1 or 0..255."""
    if value is None:
        return 1.0
    if not isinstance(value, str | int | float):
        return 1.0
    try:
        alpha = float(value)
    except (TypeError, ValueError):
        return 1.0
    if alpha > 1:
        alpha /= 255
    return max(0.0, min(1.0, alpha))


def prompt_injection_findings(
    text: str, *, page: int | None = None
) -> tuple[DocumentSecurityFinding, ...]:
    findings: list[DocumentSecurityFinding] = []
    for kind, pattern in _INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(
                DocumentSecurityFinding(
                    kind=f"prompt_injection.{kind}",
                    severity=FindingSeverity.CRITICAL
                    if kind == "instruction_override"
                    else FindingSeverity.HIGH,
                    message="The document contains text resembling instructions to an AI system.",
                    page=page,
                    evidence=" ".join(match.group(0).split())[:300],
                )
            )
    return tuple(findings)


def sanitize_untrusted_text(text: str) -> tuple[str, tuple[DocumentSecurityFinding, ...]]:
    normalized = normalize_document_text(text)
    findings = prompt_injection_findings(normalized)
    safe_lines = [
        line
        for line in normalized.splitlines()
        if not any(pattern.search(line) for _, pattern in _INJECTION_PATTERNS)
    ]
    return "\n".join(safe_lines).strip(), findings


def redact_pii(text: str, *, entities: tuple[str, ...] = ()) -> str:
    value = _EMAIL.sub("[EMAIL REDACTED]", text)
    value = _PHONE.sub("[PHONE REDACTED]", value)
    value = _URL.sub("[URL REDACTED]", value)
    for entity in sorted(
        (item.strip() for item in entities if item.strip()), key=len, reverse=True
    ):
        value = re.sub(re.escape(entity), "[IDENTITY REDACTED]", value, flags=re.I)
    lines = []
    for line in value.splitlines():
        if _ADDRESS_LINE.search(line):
            lines.append("[ADDRESS REDACTED]")
        elif _DEMOGRAPHIC_LINE.search(line):
            lines.append("[DEMOGRAPHIC DATA REDACTED]")
        else:
            lines.append(line)
    return "\n".join(lines)


def classify_findings(
    findings: list[DocumentSecurityFinding],
) -> tuple[tuple[DocumentSecurityFinding, ...], DocumentDisposition]:
    """Keep one finding per kind, page and part, and derive the document's disposition from the
    security findings: coverage findings (content not read) never make a document ``review``."""
    unique: list[DocumentSecurityFinding] = []
    seen: set[tuple[str, int | None, str | None]] = set()
    for finding in findings:
        key = (finding.kind, finding.page, finding.part)
        if key not in seen:
            seen.add(key)
            unique.append(finding)
    security = [finding for finding in unique if finding.category == "security"]
    severities = {finding.severity for finding in security}
    if severities & {FindingSeverity.CRITICAL, FindingSeverity.HIGH}:
        return tuple(unique), DocumentDisposition.QUARANTINE
    return tuple(unique), DocumentDisposition.REVIEW if security else DocumentDisposition.PASS


def recognize_span(
    ocr: TextRecognizer, image: bytes, *, media_type: str, page: int
) -> tuple[DocumentSpan | None, tuple[DocumentSecurityFinding, ...]]:
    """OCR one image into a ``source="ocr"`` span, checked for prompt injection like any text.

    An image with no recognizable text gives no span and a ``document.ocr_no_text`` finding.
    """
    result = ocr.recognize(image, media_type=media_type)
    text = normalize_document_text(result.text)
    if not text:
        return None, (
            DocumentSecurityFinding(
                kind="document.ocr_no_text",
                severity=FindingSeverity.LOW,
                message="OCR found no text in the image.",
                page=page,
            ),
        )
    injection = prompt_injection_findings(text, page=page)
    span = DocumentSpan(
        text=text,
        page=page,
        source="ocr",
        confidence=result.confidence,
        suspicious=bool(injection),
    )
    return span, injection


def decode_text(data: bytes) -> str:
    """Text from bytes of an unknown encoding: UTF-8 (with or without a BOM), UTF-16 with a BOM,
    else Windows-1252, the encoding of Excel's CSV exports, so accented letters survive."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


class PlainTextDocumentExtractor:
    def __init__(self, *, max_bytes: int = 2_000_000, max_characters: int = 250_000) -> None:
        self._max_bytes = max_bytes
        self._max_characters = max_characters

    def extract(self, data: bytes, *, filename: str) -> ExtractedDocument:
        del filename
        if len(data) > self._max_bytes:
            raise ValueError(f"document exceeds {self._max_bytes} byte limit")
        text = decode_text(data)
        if len(text) > self._max_characters:
            raise ValueError(f"document exceeds {self._max_characters} character limit")
        sanitized, findings = sanitize_untrusted_text(text)
        disposition = DocumentDisposition.QUARANTINE if findings else DocumentDisposition.PASS
        return ExtractedDocument(
            media_type="text/plain",
            text=normalize_document_text(text),
            sanitized_text=sanitized,
            spans=(DocumentSpan(text=text, page=1, suspicious=bool(findings)),),
            findings=findings,
            disposition=disposition,
        )


class PdfDocumentExtractor:
    """Inspect PDF text spans before producing downstream-safe plain text.

    A PDF longer than ``max_pages`` is refused, or with ``on_excess="truncate"`` read up to
    there and reported as a ``document.pages_skipped`` coverage finding.

    With an ``ocr`` recognizer, a page that has images and fewer than ``ocr_min_page_chars``
    characters of text is rendered (``ocr_dpi``, at most ``ocr_max_pixels`` pixels) and
    recognized, for at most ``ocr_max_pages`` pages. Without one, nothing is rendered.
    """

    def __init__(
        self,
        *,
        max_bytes: int = 10_000_000,
        max_pages: int = 50,
        on_excess: Literal["refuse", "truncate"] = "refuse",
        max_characters: int = 250_000,
        tiny_font_points: float = 2.0,
        ocr: TextRecognizer | None = None,
        ocr_max_pages: int = 3,
        ocr_min_page_chars: int = 32,
        ocr_dpi: int = 200,
        ocr_max_pixels: int = 12_000_000,
    ) -> None:
        validate_ocr_options(
            max_pages=ocr_max_pages,
            min_page_chars=ocr_min_page_chars,
            dpi=ocr_dpi,
            max_pixels=ocr_max_pixels,
        )
        self._max_bytes = max_bytes
        self._max_pages = max_pages
        self._on_excess = on_excess
        self._max_characters = max_characters
        self._tiny_font_points = tiny_font_points
        self._ocr = ocr
        self._ocr_max_pages = ocr_max_pages
        self._ocr_min_page_chars = ocr_min_page_chars
        self._ocr_dpi = ocr_dpi
        self._ocr_max_pixels = ocr_max_pixels

    def extract(self, data: bytes, *, filename: str) -> ExtractedDocument:
        del filename
        if len(data) > self._max_bytes:
            raise ValueError(f"document exceeds {self._max_bytes} byte limit")
        if not data.startswith(b"%PDF-"):
            raise ValueError("file content is not a PDF")
        try:
            import fitz
        except ImportError as exc:
            raise ImportError(
                "install algen-agent-tools[documents] for PDF inspection", name="fitz"
            ) from exc
        document = fitz.open(stream=data, filetype="pdf")
        excess = document.page_count > self._max_pages
        if document.page_count < 1 or (excess and self._on_excess == "refuse"):
            raise ValueError(f"PDF page count must be between 1 and {self._max_pages}")
        spans: list[DocumentSpan] = []
        findings: list[DocumentSecurityFinding] = []
        image_count = 0
        ocr_pages: list[int] = []
        unread_image_pages: list[int] = []
        if excess:
            findings.append(
                DocumentSecurityFinding(
                    kind="document.pages_skipped",
                    severity=FindingSeverity.LOW,
                    message=f"Pages after page {self._max_pages} were not read.",
                    page=self._max_pages + 1,
                )
            )
        for page_index, page in enumerate(document, start=1):
            if page_index > self._max_pages:
                break
            page_images = len(page.get_images(full=True))
            image_count += page_images
            first_span = len(spans)
            width, height = float(page.rect.width), float(page.rect.height)
            payload = page.get_text("dict")
            for block in payload.get("blocks", ()):
                for line in block.get("lines", ()):
                    for raw in line.get("spans", ()):
                        text = normalize_document_text(str(raw.get("text", "")))
                        if not text:
                            continue
                        raw_bbox = tuple(float(item) for item in raw.get("bbox", (0, 0, 0, 0)))
                        bbox: tuple[float, float, float, float] = (
                            (raw_bbox[0], raw_bbox[1], raw_bbox[2], raw_bbox[3])
                            if len(raw_bbox) >= 4
                            else (0.0, 0.0, 0.0, 0.0)
                        )
                        size = float(raw.get("size", 0))
                        color = int(raw.get("color", 0))
                        opacity = normalize_pdf_opacity(raw.get("alpha"))
                        tiny = size < self._tiny_font_points
                        pale = all(
                            channel >= 245
                            for channel in ((color >> 16) & 255, (color >> 8) & 255, color & 255)
                        )
                        invisible = opacity <= 0.05
                        off_page = (
                            bbox[2] <= 0 or bbox[3] <= 0 or bbox[0] >= width or bbox[1] >= height
                        )
                        injection = prompt_injection_findings(text, page=page_index)
                        suspicious = tiny or pale or invisible or off_page or bool(injection)
                        spans.append(
                            DocumentSpan(
                                text=text,
                                page=page_index,
                                font_size=size,
                                color=color,
                                opacity=opacity,
                                bbox=bbox,
                                suspicious=suspicious,
                            )
                        )
                        if tiny:
                            findings.append(
                                DocumentSecurityFinding(
                                    kind="hidden_text.tiny_font",
                                    severity=FindingSeverity.HIGH,
                                    message="Text uses an unusually small font.",
                                    page=page_index,
                                    evidence=text[:300],
                                )
                            )
                        if pale or invisible:
                            findings.append(
                                DocumentSecurityFinding(
                                    kind="hidden_text.low_visibility",
                                    severity=FindingSeverity.HIGH,
                                    message="Text has very low visual contrast or opacity.",
                                    page=page_index,
                                    evidence=text[:300],
                                )
                            )
                        if off_page:
                            findings.append(
                                DocumentSecurityFinding(
                                    kind="hidden_text.off_page",
                                    severity=FindingSeverity.HIGH,
                                    message="Text is positioned outside the visible page.",
                                    page=page_index,
                                    evidence=text[:300],
                                )
                            )
                        findings.extend(injection)
            if self._ocr is None or not page_images:
                continue
            page_chars = sum(len(span.text) for span in spans[first_span:])
            if page_chars >= self._ocr_min_page_chars:
                unread_image_pages.append(page_index)
            elif len(ocr_pages) >= self._ocr_max_pages:
                findings.append(
                    DocumentSecurityFinding(
                        kind="document.ocr_required",
                        severity=FindingSeverity.MEDIUM,
                        message="The page looks scanned and is past the OCR page limit.",
                        page=page_index,
                    )
                )
            else:
                ocr_pages.append(page_index)
                image = render_page_png(
                    fitz, page, dpi=self._ocr_dpi, max_pixels=self._ocr_max_pixels
                )
                span, ocr_findings = recognize_span(
                    self._ocr, image, media_type="image/png", page=page_index
                )
                if span is not None:
                    spans.append(span)
                findings.extend(ocr_findings)
        if sum(len(span.text) for span in spans) > self._max_characters:
            raise ValueError(f"PDF exceeds {self._max_characters} extracted character limit")
        visible = "\n".join(span.text for span in spans if not span.suspicious)
        sanitized, visible_findings = sanitize_untrusted_text(visible)
        findings.extend(visible_findings)
        if self._ocr is not None:
            if unread_image_pages:
                findings.append(
                    DocumentSecurityFinding(
                        kind="document.images_excluded",
                        severity=FindingSeverity.LOW,
                        message="Embedded images on pages with text were not read.",
                        page=unread_image_pages[0] if len(unread_image_pages) == 1 else None,
                    )
                )
        elif not spans and image_count:
            findings.append(
                DocumentSecurityFinding(
                    kind="document.ocr_required",
                    severity=FindingSeverity.MEDIUM,
                    message="The PDF contains images but no extractable text; OCR review is required.",
                )
            )
        elif image_count:
            findings.append(
                DocumentSecurityFinding(
                    kind="document.images_excluded",
                    severity=FindingSeverity.LOW,
                    message="Embedded images were excluded from downstream screening.",
                )
            )
        unique_findings, disposition = classify_findings(findings)
        return ExtractedDocument(
            media_type="application/pdf",
            text="\n".join(span.text for span in spans),
            sanitized_text=sanitized,
            spans=tuple(spans),
            findings=unique_findings,
            disposition=disposition,
            page_count=document.page_count,
            image_count=image_count,
        )


_ZIP_MEMBERS = (
    ("word/document.xml", ".docx"),
    ("xl/workbook.xml", ".xlsx"),
    ("ppt/presentation.xml", ".pptx"),
)
_ODF_TYPES = {
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
}
_SAME = {".jpg": ".jpeg", ".tif": ".tiff", ".htm": ".html"}


def sniff_suffix(data: bytes) -> str | None:
    """The file suffix the content's own signature declares (``.pdf``, an image, or an Office
    or OpenDocument package), or ``None`` when the content has no signature this package
    knows: text formats are told apart by their name only."""
    if data.startswith(b"%PDF-"):
        return ".pdf"
    from algen_agent_tools.documents.inspection.image import image_media_type

    media_type = image_media_type(data)
    if media_type is not None:
        return {"image/jpeg": ".jpg", "image/tiff": ".tif"}.get(
            media_type, "." + media_type.partition("/")[2]
        )
    if not data.startswith(b"PK\x03\x04"):
        return None
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist()[:10_000])
            if "mimetype" in names and archive.getinfo("mimetype").file_size < 200:
                found = _ODF_TYPES.get(archive.read("mimetype").decode("ascii", "replace").strip())
                if found:
                    return found
            return next((suffix for member, suffix in _ZIP_MEMBERS if member in names), None)
    except (zipfile.BadZipFile, OSError, KeyError, ValueError):
        return None


def extractor_for(
    filename: str,
    *,
    data: bytes | None = None,
    ocr: TextRecognizer | None = None,
    ocr_max_pages: int = 3,
) -> DocumentExtractor:
    """The extractor for ``filename``'s suffix, or with ``data``, for the type the content's
    signature declares when it has one (a ``scan.pdf`` that is really a JPEG is read as an
    image, a renamed ``.docx`` as Word). An email (``.eml``, or Outlook ``.msg`` with the
    ``msg`` extra) has its attachments read by the extractors for their own file names. A PDF
    longer than its page limit is read up to the limit (``document.pages_skipped``).

    With an ``ocr`` recognizer, images (PNG, JPEG, WebP, GIF, BMP, TIFF) are accepted and
    scanned PDF pages are recognized, up to ``ocr_max_pages`` pages per PDF. Without one,
    images are refused and nothing is recognized.
    """
    suffix = Path(filename).suffix.lower()
    sniffed = sniff_suffix(data) if data is not None else None
    if sniffed is not None and _SAME.get(sniffed, sniffed) != _SAME.get(suffix, suffix):
        suffix = sniffed
    if suffix == ".pdf":
        if ocr is None:
            return PdfDocumentExtractor(on_excess="truncate")
        return PdfDocumentExtractor(on_excess="truncate", ocr=ocr, ocr_max_pages=ocr_max_pages)
    if ocr is not None:
        from algen_agent_tools.documents.inspection.image import (
            IMAGE_MEDIA_TYPES,
            ImageDocumentExtractor,
        )

        if suffix in IMAGE_MEDIA_TYPES:
            return ImageDocumentExtractor(ocr=ocr)
    if suffix == ".docx":
        from algen_agent_tools.documents.inspection.docx import DocxDocumentExtractor

        return DocxDocumentExtractor()
    if suffix == ".xlsx":
        from algen_agent_tools.documents.inspection.xlsx import XlsxDocumentExtractor

        return XlsxDocumentExtractor()
    if suffix == ".pptx":
        from algen_agent_tools.documents.inspection.pptx import PptxDocumentExtractor

        return PptxDocumentExtractor()
    if suffix in {".html", ".htm"}:
        from algen_agent_tools.documents.inspection.html import HtmlDocumentExtractor

        return HtmlDocumentExtractor()
    if suffix in {".txt", ".md", ".csv", ".tsv"}:
        return PlainTextDocumentExtractor()
    if suffix == ".eml":
        from algen_agent_tools.documents.inspection.email import EmailDocumentExtractor

        return EmailDocumentExtractor(ocr=ocr, ocr_max_pages=ocr_max_pages)
    if suffix == ".msg":
        from algen_agent_tools.documents.inspection.msg import MsgDocumentExtractor

        return MsgDocumentExtractor(ocr=ocr, ocr_max_pages=ocr_max_pages)
    raise ValueError(
        "only PDF, DOCX, XLSX, PPTX, HTML, CSV, TSV, TXT, Markdown, EML and MSG documents are "
        "supported"
    )
