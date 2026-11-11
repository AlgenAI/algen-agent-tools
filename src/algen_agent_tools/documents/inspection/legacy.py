"""Legacy binary Office files (``.doc``, ``.xls``, ``.ppt``) read through a host's converter.

This package ships no converter and parses no legacy binary format. A host passes any object
with a ``convert`` method (a :class:`DocumentConverter`, for example a sandboxed LibreOffice
wrapper) to ``extractor_for(..., converter=...)`` or ``documents_toolkit(converter=...)``;
without one, legacy files are refused, exactly as before.

The converter's output is as untrusted as its input: it is bounded (``max_output_bytes``), its
type comes from its own signature (PDF, DOCX, XLSX, PPTX, ODT, ODS or ODP, nothing else), and it
is inspected by that format's extractor with every hidden-text, prompt-injection and packaging
check. A conversion drops a macro project, so the input itself is checked for one first.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from algen_agent_tools.documents.inspection.core import (
    DocumentSecurityFinding,
    ExtractedDocument,
    FindingSeverity,
    classify_findings,
    extractor_for,
    sniff_suffix,
)

if TYPE_CHECKING:
    from algen_agent_tools.documents.inspection.ocr import TextRecognizer

LEGACY_MEDIA_TYPES: dict[str, str] = {
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
}
CONVERTED_SUFFIXES = frozenset({".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"})
"""The formats a converter may return: each has its own signature and extractor."""
LEGACY_REFUSAL = (
    "legacy Word (.doc), Excel (.xls) and PowerPoint (.ppt) files are read only with a document "
    "converter; save the file as DOCX, XLSX, PPTX or PDF"
)

_COMPOUND_FILE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
# Directory entries of a VBA project: Word keeps it under "Macros", Excel under
# "_VBA_PROJECT_CUR", and both hold a "_VBA_PROJECT" stream.
_VBA_ENTRIES = tuple(
    name.encode("utf-16-le") + b"\x00\x00"
    for name in ("_VBA_PROJECT", "_VBA_PROJECT_CUR", "Macros")
)


class DocumentConverter(Protocol):
    """Converts one legacy Office file into a modern format and returns its bytes.

    ``media_type`` is the input's type (``application/msword``, ``application/vnd.ms-excel`` or
    ``application/vnd.ms-powerpoint``); the converter chooses the output format (PDF, DOCX,
    XLSX, PPTX, ODT, ODS or ODP), which is read from the output's own signature. Raise
    ``ValueError`` when the file cannot be converted; it is reported as refused, or for an email
    attachment as not read. Called from a worker thread, with the untrusted file: run the
    conversion sandboxed, without network access, and with a time limit of its own.
    """

    def convert(self, data: bytes, *, media_type: str) -> bytes: ...


def has_macro_project(data: bytes) -> bool:
    """Whether a compound (OLE) file has a VBA project directory entry.

    Entries are 128 bytes, aligned to 128 bytes in the file, with the name's byte length (with
    its terminator) at offset 64, so text that merely spells a name does not count.
    """
    for name in _VBA_ENTRIES:
        start = data.find(name)
        while start != -1:
            if start % 128 == 0 and int.from_bytes(data[start + 64 : start + 66], "little") == len(
                name
            ):
                return True
            start = data.find(name, start + 1)
    return False


class LegacyOfficeDocumentExtractor:
    """A ``.doc``, ``.xls`` or ``.ppt`` file converted by ``converter``, then inspected as the
    format the converter returned.

    The input must be a compound (OLE) file of at most ``max_bytes``; the output must be at most
    ``max_output_bytes`` and one of :data:`CONVERTED_SUFFIXES` by its signature. A macro project
    in the input is a ``document.macros`` finding, as for DOCX. A converted PDF is read up to
    its page limit and, with ``ocr``, has its scanned pages recognized. The result's
    ``media_type`` is the legacy file's.
    """

    def __init__(
        self,
        *,
        converter: DocumentConverter,
        max_bytes: int = 10_000_000,
        max_output_bytes: int = 10_000_000,
        ocr: TextRecognizer | None = None,
        ocr_max_pages: int = 3,
    ) -> None:
        if max_bytes < 1 or max_output_bytes < 1:
            raise ValueError("max_bytes and max_output_bytes must be at least 1")
        self._converter = converter
        self._max_bytes = max_bytes
        self._max_output_bytes = max_output_bytes
        self._ocr = ocr
        self._ocr_max_pages = ocr_max_pages

    def extract(self, data: bytes, *, filename: str) -> ExtractedDocument:
        suffix = Path(filename).suffix.lower()
        media_type = LEGACY_MEDIA_TYPES.get(suffix)
        if media_type is None:
            raise ValueError("only .doc, .xls and .ppt files are converted")
        if len(data) > self._max_bytes:
            raise ValueError(f"document exceeds {self._max_bytes} byte limit")
        if not data.startswith(_COMPOUND_FILE):
            raise ValueError("file content is not a legacy Office document")
        findings: list[DocumentSecurityFinding] = []
        if has_macro_project(data):
            findings.append(
                DocumentSecurityFinding(
                    kind="document.macros",
                    severity=FindingSeverity.CRITICAL,
                    message="The document carries a macro project.",
                )
            )
        try:
            output = self._converter.convert(data, media_type=media_type)
        except ValueError:
            # The converter's message may quote the file; the error names the rule only.
            raise ValueError("the document converter could not convert the file") from None
        if not isinstance(output, bytes | bytearray | memoryview):
            raise ValueError("the document converter returned no document")
        converted = bytes(output)
        if len(converted) > self._max_output_bytes:
            raise ValueError(f"converted document exceeds {self._max_output_bytes} byte limit")
        converted_suffix = sniff_suffix(converted)
        if converted_suffix not in CONVERTED_SUFFIXES:
            raise ValueError("the document converter returned an unsupported format")
        name = f"{Path(filename).stem or 'document'}{converted_suffix}"
        # No converter here: converted output is never converted again.
        extractor = extractor_for(
            name, data=converted, ocr=self._ocr, ocr_max_pages=self._ocr_max_pages
        )
        extracted = extractor.extract(converted, filename=name)
        if not findings:
            return extracted.model_copy(update={"media_type": media_type})
        unique, disposition = classify_findings([*findings, *extracted.findings])
        return extracted.model_copy(
            update={"media_type": media_type, "findings": unique, "disposition": disposition}
        )
