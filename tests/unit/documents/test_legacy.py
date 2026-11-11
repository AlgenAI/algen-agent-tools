"""The converter hook: legacy .doc, .xls and .ppt files read through a host's converter (a fake
here), with the converter's output inspected as untrusted."""

from __future__ import annotations

from typing import Any

import pytest
from algen_agent_runtime.tools.testing import fake_tool_context

from algen_agent_tools.documents import DocumentConverter, DocumentRefused, documents_toolkit
from algen_agent_tools.documents.inspection import (
    EmailDocumentExtractor,
    LegacyOfficeDocumentExtractor,
    extractor_for,
)
from algen_agent_tools.documents.inspection.legacy import LEGACY_MEDIA_TYPES, has_macro_project
from algen_agent_tools.documents.toolkit import CONVERTER_NOTE, OCR_NOTE
from algen_agent_tools.testing import assert_toolkit_contract

from ..test_documents import INJECTION, Artifacts, docx, paragraph, pptx, ref, xlsx
from .test_email import email
from .test_ocr import FakeRecognizer, pdf

MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def entry(name: str) -> bytes:
    """One 128-byte compound-file directory entry."""
    encoded = name.encode("utf-16-le") + b"\x00\x00"
    return (
        encoded.ljust(64, b"\x00") + len(encoded).to_bytes(2, "little") + b"\x01".ljust(62, b"\0")
    )


def legacy(*entries: str, body: bytes = b"") -> bytes:
    """A stand-in compound (OLE) file: the signature, a header and a directory sector."""
    header = MAGIC.ljust(512, b"\x00")
    directory = b"".join(entry(name) for name in ("Root Entry", "WordDocument", *entries))
    return header + directory.ljust(512, b"\x00") + body


class FakeConverter:
    """Returns ``output`` (or raises ``error``) and records every call."""

    def __init__(self, output: Any = b"", error: Exception | None = None) -> None:
        self.output = output
        self.error = error
        self.calls: list[tuple[bytes, str]] = []

    def convert(self, data: bytes, *, media_type: str) -> bytes:
        self.calls.append((data, media_type))
        if self.error is not None:
            raise self.error
        return self.output  # type: ignore[no-any-return]


def read(data: bytes, filename: str, converter: DocumentConverter, **options: Any) -> Any:
    return extractor_for(filename, data=data, converter=converter, **options).extract(
        data, filename=filename
    )


# Without a converter


@pytest.mark.parametrize("filename", ["letter.doc", "budget.xls", "deck.ppt"])
def test_without_a_converter_legacy_files_are_refused_with_a_clear_message(filename: str) -> None:
    with pytest.raises(ValueError, match=r"only with a document converter.*DOCX"):
        extractor_for(filename, data=legacy())


async def test_the_toolkit_refuses_a_legacy_file_without_a_converter() -> None:
    artifacts = Artifacts({"a1": legacy()})
    [tool] = documents_toolkit(artifact_loader=artifacts.load).tools
    document = ref("a1", "cv.doc", "application/msword")
    with pytest.raises(DocumentRefused, match="document converter"):
        await tool.execute({"document": document}, fake_tool_context(tool))


def test_without_a_converter_a_legacy_email_attachment_is_a_coverage_finding() -> None:
    message = email(attachments=[("cv.doc", "application/msword", legacy())])
    extracted = EmailDocumentExtractor().extract(message.as_bytes(), filename="mail.eml")
    [finding] = extracted.findings
    assert (finding.kind, finding.part, finding.category) == (
        "document.attachment_unread",
        "cv.doc",
        "coverage",
    )
    assert "document converter" in finding.message
    assert (extracted.disposition, extracted.coverage) == ("pass", "partial")


# With a converter


@pytest.mark.parametrize(
    ("filename", "output", "text"),
    [
        ("letter.doc", docx(paragraph("Dear team, the offer stands.")), "the offer stands"),
        ("budget.xls", xlsx(("Q3", [["Region", "Total"], ["North", "42"]], False)), "North | 42"),
        ("deck.ppt", pptx([("Roadmap for 2027", False)]), "Roadmap for 2027"),
    ],
)
def test_a_converted_file_is_read_as_the_format_the_converter_returned(
    filename: str, output: bytes, text: str
) -> None:
    data = legacy()
    converter = FakeConverter(output)
    extracted = read(data, filename, converter)
    suffix = filename[filename.index(".") :]
    assert converter.calls == [(data, LEGACY_MEDIA_TYPES[suffix])]
    assert text in extracted.sanitized_text
    assert extracted.media_type == LEGACY_MEDIA_TYPES[suffix]
    assert (extracted.disposition, extracted.coverage) == ("pass", "full")


def test_a_converted_pdf_is_read_and_its_scanned_pages_go_to_ocr() -> None:
    output = pdf("Minutes of the March meeting", None)
    recognizer = FakeRecognizer("Signed by the board")
    extracted = read(legacy(), "minutes.doc", FakeConverter(output), ocr=recognizer)
    assert "Minutes of the March meeting" in extracted.sanitized_text
    assert "Signed by the board" in extracted.sanitized_text
    assert extracted.media_type == "application/msword"


def test_hidden_text_and_instructions_in_the_output_are_found() -> None:
    output = docx(paragraph("Visible summary."), paragraph(INJECTION, hidden=True))
    extracted = read(legacy(), "cv.doc", FakeConverter(output))
    assert extracted.disposition == "quarantine"
    assert INJECTION not in extracted.sanitized_text
    assert any(finding.kind.startswith("hidden_text") for finding in extracted.findings)


@pytest.mark.parametrize("project", ["Macros", "_VBA_PROJECT_CUR", "_VBA_PROJECT"])
def test_a_macro_project_in_the_legacy_file_is_reported_although_conversion_drops_it(
    project: str,
) -> None:
    data = legacy(project)
    assert has_macro_project(data)
    extracted = read(data, "invoice.doc", FakeConverter(docx(paragraph("Invoice 7"))))
    assert [finding.kind for finding in extracted.findings] == ["document.macros"]
    assert extracted.disposition == "quarantine"
    assert extracted.media_type == "application/msword"


def test_text_that_spells_a_macro_name_is_not_a_macro_project() -> None:
    text = "See Macros\x00 and _VBA_PROJECT\x00 in the manual".encode("utf-16-le")
    data = legacy(body=b"\x00" * 3 + text)
    assert not has_macro_project(data)
    assert read(data, "manual.doc", FakeConverter(docx(paragraph("Manual")))).findings == ()


# The converter's input and output are bounded and checked


def test_a_file_that_is_not_a_compound_file_never_reaches_the_converter() -> None:
    converter = FakeConverter(docx(paragraph("x")))
    with pytest.raises(ValueError, match="not a legacy Office document"):
        read(b"PLAIN TEXT pretending to be a .doc", "letter.doc", converter)
    assert converter.calls == []


def test_a_renamed_docx_is_still_read_as_word_without_converting() -> None:
    converter = FakeConverter()
    extracted = read(docx(paragraph("Already modern")), "letter.doc", converter)
    assert "Already modern" in extracted.sanitized_text
    assert converter.calls == []


def test_an_oversized_input_never_reaches_the_converter() -> None:
    converter = FakeConverter(docx(paragraph("x")))
    extractor = LegacyOfficeDocumentExtractor(converter=converter, max_bytes=2_000)
    with pytest.raises(ValueError, match="2000 byte limit"):
        extractor.extract(legacy(body=b"\x00" * 2_000), filename="big.doc")
    assert converter.calls == []


def test_an_oversized_output_is_refused() -> None:
    extractor = LegacyOfficeDocumentExtractor(
        converter=FakeConverter(docx(paragraph("x" * 5_000))), max_output_bytes=1_000
    )
    with pytest.raises(ValueError, match="converted document exceeds 1000 byte limit"):
        extractor.extract(legacy(), filename="letter.doc")


@pytest.mark.parametrize(
    "output",
    [
        b"Plain text is not a converted document",
        b"<html><body>Converted</body></html>",
        legacy(),
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
        b"From: a@example.com\nSubject: hi\n\nbody",
        b"",
    ],
)
def test_an_output_of_another_format_is_refused(output: bytes) -> None:
    with pytest.raises(ValueError, match="unsupported format"):
        read(legacy(), "letter.doc", FakeConverter(output))


@pytest.mark.parametrize("output", [None, "a string", 42])
def test_an_output_that_is_not_bytes_is_refused(output: Any) -> None:
    with pytest.raises(ValueError, match="returned no document"):
        read(legacy(), "letter.doc", FakeConverter(output))


def test_bytearray_output_is_accepted() -> None:
    output = bytearray(docx(paragraph("From a buffer")))
    assert "From a buffer" in read(legacy(), "a.doc", FakeConverter(output)).sanitized_text


def test_a_conversion_failure_names_the_rule_not_the_converters_message() -> None:
    converter = FakeConverter(error=ValueError("cannot open /tmp/secret-name.doc"))
    with pytest.raises(ValueError, match="could not convert") as raised:
        read(legacy(), "letter.doc", converter)
    assert "secret" not in str(raised.value)


def test_other_converter_errors_propagate_unchanged() -> None:
    converter = FakeConverter(error=TimeoutError("conversion timed out"))
    with pytest.raises(TimeoutError):
        read(legacy(), "letter.doc", converter)


def test_the_extractor_refuses_other_suffixes_and_bad_limits() -> None:
    with pytest.raises(ValueError, match=r"only \.doc, \.xls and \.ppt"):
        LegacyOfficeDocumentExtractor(converter=FakeConverter()).extract(legacy(), filename="a.msg")
    with pytest.raises(ValueError, match="at least 1"):
        LegacyOfficeDocumentExtractor(converter=FakeConverter(), max_output_bytes=0)


# Emails and the toolkit


def test_an_email_attachment_is_converted_and_its_findings_are_the_messages() -> None:
    output = docx(paragraph("Résumé of Ana"), paragraph(INJECTION))
    message = email(attachments=[("cv.doc", "application/msword", legacy())])
    extracted = EmailDocumentExtractor(converter=FakeConverter(output)).extract(
        message.as_bytes(), filename="mail.eml"
    )
    [part] = extracted.parts
    assert (part.filename, part.media_type) == ("cv.doc", "application/msword")
    assert "Résumé of Ana" in extracted.text
    assert extracted.disposition == "quarantine"
    assert all(finding.part == "cv.doc" for finding in extracted.findings)


async def test_the_toolkit_reads_a_legacy_file_with_a_converter_and_says_so() -> None:
    artifacts = Artifacts({"a1": legacy()})
    converter = FakeConverter(xlsx(("Sheet1", [["Total", "42"]], False)))
    toolkit = documents_toolkit(artifact_loader=artifacts.load, converter=converter)
    assert_toolkit_contract(toolkit)
    [tool] = toolkit.tools
    plain = documents_toolkit(artifact_loader=artifacts.load).tools[0].definition.description
    assert tool.definition.description == f"{plain} {CONVERTER_NOTE}"
    both = documents_toolkit(
        artifact_loader=artifacts.load, converter=converter, ocr=FakeRecognizer("x")
    )
    assert both.tools[0].definition.description == f"{plain} {OCR_NOTE} {CONVERTER_NOTE}"
    document = ref("a1", "budget.xls", "application/vnd.ms-excel")
    result = await tool.execute({"document": document}, fake_tool_context(tool))
    assert "Total | 42" in result["text"]
    assert result["disposition"] == "pass"
    assert converter.calls == [(legacy(), "application/vnd.ms-excel")]
