"""Content over file names, text in any common encoding, and long PDFs read in part."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest
from algen_agent_runtime.tools.contracts import ToolContext
from algen_agent_runtime.tools.testing import fake_tool_context

from algen_agent_tools.attachments import AttachmentRef
from algen_agent_tools.documents import documents_toolkit, extractor_for
from algen_agent_tools.documents.inspection.core import (
    PdfDocumentExtractor,
    decode_text,
    sniff_suffix,
)

from ..test_documents import docx, paragraph
from .test_ocr import FakeRecognizer, pdf, png


def read(data: bytes, filename: str, **options: Any) -> Any:
    return extractor_for(filename, data=data, **options).extract(data, filename=filename)


def test_a_scan_named_pdf_that_is_really_a_jpeg_is_read_as_an_image() -> None:
    data = png()
    extracted = read(data, "scan.pdf", ocr=FakeRecognizer("PERMANENT ACCOUNT NUMBER"))
    assert extracted.sanitized_text == "PERMANENT ACCOUNT NUMBER"


def test_without_a_recognizer_a_misnamed_image_is_still_refused() -> None:
    with pytest.raises(ValueError, match="only PDF"):
        read(png(), "scan.pdf")


def test_a_renamed_docx_is_read_as_word() -> None:
    extracted = read(docx(paragraph("Quarterly results")), "report.doc")
    assert "Quarterly results" in extracted.sanitized_text


def test_a_pdf_named_txt_is_inspected_as_a_pdf() -> None:
    data = pdf("Hello from a PDF")
    assert read(data, "notes.txt").media_type == "application/pdf"


def test_a_png_named_png_and_a_jpg_alias_stay_as_they_are() -> None:
    assert sniff_suffix(png()) == ".png"
    assert sniff_suffix(b"\xff\xd8\xff\xe0rest") == ".jpg"


def test_packages_are_told_apart_by_their_contents() -> None:
    def archive(members: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as package:
            for name, content in members.items():
                package.writestr(name, content)
        return buffer.getvalue()

    assert sniff_suffix(archive({"xl/workbook.xml": b"<x/>"})) == ".xlsx"
    assert sniff_suffix(archive({"ppt/presentation.xml": b"<x/>"})) == ".pptx"
    odt = archive({"mimetype": b"application/vnd.oasis.opendocument.text"})
    assert sniff_suffix(odt) == ".odt"
    assert sniff_suffix(archive({"readme.txt": b"hi"})) is None
    assert sniff_suffix(b"PK\x03\x04 broken") is None
    assert sniff_suffix(b"plain text") is None


@pytest.mark.parametrize(
    ("data", "text"),
    [
        ("Müller,5 €\n".encode("cp1252"), "Müller,5 €\n"),
        ("﻿name,city\nZoë,Paris".encode(), "name,city\nZoë,Paris"),
        ("Grüße".encode("utf-16"), "Grüße"),
        (b"plain ascii", "plain ascii"),
    ],
)
def test_text_decodes_from_utf8_utf16_or_windows_1252(data: bytes, text: str) -> None:
    assert decode_text(data) == text


def test_an_excel_csv_export_keeps_its_accents() -> None:
    data = "Name;Stadt\nJürgen;Köln\n".encode("cp1252")
    assert read(data, "export.csv").sanitized_text == "Name;Stadt\nJürgen;Köln"


def test_html_in_windows_1252_keeps_its_accents() -> None:
    data = "<p>Café crème</p>".encode("cp1252")
    assert read(data, "menu.html").sanitized_text == "Café crème"


def test_a_long_pdf_is_read_up_to_the_page_limit() -> None:
    data = pdf(*[f"Page {number}" for number in range(1, 56)])

    extracted = read(data, "long.pdf")

    assert "Page 50" in extracted.sanitized_text
    assert "Page 51" not in extracted.sanitized_text
    assert [(f.kind, f.page) for f in extracted.findings] == [("document.pages_skipped", 51)]
    assert (extracted.disposition, extracted.coverage, extracted.page_count) == (
        "pass",
        "partial",
        55,
    )
    assert [(item.kind, item.page) for item in extracted.unread] == [("page", 51)]


def test_the_extractor_still_refuses_long_pdfs_unless_asked() -> None:
    data = pdf("one", "two", "three")
    with pytest.raises(ValueError, match="page count"):
        PdfDocumentExtractor(max_pages=2).extract(data, filename="x.pdf")


async def test_the_tool_marks_a_partly_read_pdf_as_truncated() -> None:
    data = pdf(*[f"Page {number}" for number in range(1, 53)])

    async def load(reference: AttachmentRef, context: ToolContext) -> bytes:
        return data

    [tool] = documents_toolkit(artifact_loader=load).tools
    document = {"artifact_id": "a", "filename": "long.pdf", "media_type": "application/pdf"}
    result = await tool.execute({"document": document}, fake_tool_context(tool))

    assert (result["truncated"], result["coverage"]) == (True, "partial")
