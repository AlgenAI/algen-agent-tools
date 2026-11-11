"""A PDF is read in a bounded page window: ``start_page`` and ``max_pages``."""

from __future__ import annotations

from typing import Any

import pytest
from algen_agent_runtime.tools.testing import fake_tool_context

from algen_agent_tools.documents import DocumentRefused
from algen_agent_tools.documents.inspection.core import PdfDocumentExtractor, extractor_for

from ..test_documents import Artifacts, docx, paragraph, ref, tool_for
from .test_ocr import pdf


def read(data: bytes, filename: str = "long.pdf", **options: Any) -> Any:
    return extractor_for(filename, data=data, **options).extract(data, filename=filename)


def pages(count: int) -> bytes:
    return pdf(*[f"Page {number}" for number in range(1, count + 1)])


def skipped(extracted: Any) -> list[tuple[int | None, str]]:
    return [
        (finding.page, finding.message)
        for finding in extracted.findings
        if finding.kind == "document.pages_skipped"
    ]


def test_a_window_in_the_middle_reports_the_pages_before_and_after_it() -> None:
    extracted = read(pages(12), start_page=4, max_pages=3)

    assert extracted.sanitized_text.split("\n") == ["Page 4", "Page 5", "Page 6"]
    assert extracted.page_window == (4, 6)
    assert extracted.page_count == 12
    assert skipped(extracted) == [
        (1, "Pages before page 4 were not read."),
        (7, "Pages after page 6 were not read."),
    ]
    assert extracted.coverage == "partial"
    assert extracted.disposition == "pass"


def test_a_window_reaching_the_end_reports_only_the_pages_before_it() -> None:
    extracted = read(pages(55), start_page=51)

    assert extracted.sanitized_text.split("\n") == [f"Page {n}" for n in range(51, 56)]
    assert extracted.page_window == (51, 55)
    assert skipped(extracted) == [(1, "Pages before page 51 were not read.")]


def test_the_default_window_is_the_first_fifty_pages() -> None:
    extracted = read(pages(3))

    assert extracted.page_window == (1, 3)
    assert not extracted.findings


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"start_page": 0}, "start_page must be 1 or more"),
        ({"max_pages": 0}, "max_pages must be between 1 and 50"),
        ({"max_pages": 51}, "max_pages must be between 1 and 50"),
    ],
)
def test_out_of_range_windows_are_refused(options: dict[str, int], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        extractor_for("long.pdf", **options)


def test_a_start_page_past_the_end_is_refused() -> None:
    with pytest.raises(ValueError, match=r"start_page 5 is past the PDF's last page \(3\)"):
        read(pages(3), start_page=5)


def test_a_start_page_for_another_format_is_refused() -> None:
    with pytest.raises(ValueError, match="start_page applies to PDF documents only"):
        extractor_for("notes.txt", start_page=2)
    # max_pages has nothing to bound in other formats and is ignored.
    data = docx(paragraph("Hello"))
    assert read(data, "cv.docx", max_pages=1).page_window is None


def test_the_extractor_refuses_pages_after_its_window_unless_truncating() -> None:
    data = pages(5)
    with pytest.raises(ValueError, match="pages after page 3"):
        PdfDocumentExtractor(start_page=2, max_pages=2).extract(data, filename="a.pdf")
    with pytest.raises(ValueError, match="start_page must be 1 or more"):
        PdfDocumentExtractor(start_page=0)


async def test_extract_text_takes_a_page_window_and_reports_it() -> None:
    tool = tool_for(Artifacts({"a1": pages(8)}))
    result = await tool.execute(
        {"document": ref("a1", "long.pdf", "application/pdf"), "start_page": 7, "max_pages": 5},
        fake_tool_context(tool),
    )

    assert result["text"] == "Page 7\nPage 8"
    assert result["page_window"] == {"first": 7, "last": 8}
    assert result["truncated"] is True
    assert [(f["kind"], f["page"]) for f in result["findings"]] == [("document.pages_skipped", 1)]


async def test_extract_text_reports_no_window_for_other_formats() -> None:
    tool = tool_for(Artifacts({"a1": b"plain"}))
    result = await tool.execute(
        {"document": ref("a1", "notes.txt", "text/plain")}, fake_tool_context(tool)
    )
    assert result["page_window"] is None

    with pytest.raises(DocumentRefused, match="PDF documents only"):
        await tool.execute(
            {"document": ref("a1", "notes.txt", "text/plain"), "start_page": 2},
            fake_tool_context(tool),
        )


def test_extract_text_bounds_the_window_in_its_schema() -> None:
    tool = tool_for(Artifacts({}))
    properties = tool.definition.input_schema["properties"]
    assert (properties["start_page"]["minimum"], properties["start_page"]["maximum"]) == (1, 10_000)
    assert (properties["max_pages"]["minimum"], properties["max_pages"]["maximum"]) == (1, 50)
