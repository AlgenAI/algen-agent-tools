"""PDF text is judged against what it is drawn on, not by its colour alone."""

from __future__ import annotations

from typing import Any

import pytest

from algen_agent_tools.documents.inspection.contrast import contrast_ratio
from algen_agent_tools.documents.inspection.core import (
    DocumentDisposition,
    ExtractedDocument,
    FindingSeverity,
    PdfDocumentExtractor,
)
from algen_agent_tools.documents.inspection.docx_contrast import is_invisible
from algen_agent_tools.documents.inspection.pdf_contrast import rgb

fitz = pytest.importorskip("fitz")

WHITE = (1.0, 1.0, 1.0)
NAVY = (0.1, 0.1, 0.3)
TEXT = "Senior Platform Engineer"


def _pdf(draw: Any) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 60), "Visible heading", color=(0, 0, 0))
    draw(page)
    return bytes(document.tobytes())


def _extract(data: bytes, **options: Any) -> ExtractedDocument:
    return PdfDocumentExtractor(**options).extract(data, filename="resume.pdf")


def _kinds(extracted: ExtractedDocument) -> dict[str, FindingSeverity]:
    return {finding.kind: finding.severity for finding in extracted.findings}


def _image(page: Any, rect: Any, grey: int) -> None:
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 16, 16), False)
    pixmap.clear_with(grey)
    page.insert_image(rect, pixmap=pixmap)


def test_white_text_on_a_dark_shape_is_visible() -> None:
    def draw(page: Any) -> None:
        page.draw_rect(fitz.Rect(50, 100, 400, 160), color=None, fill=NAVY)
        page.insert_text((60, 135), TEXT, color=WHITE)

    extracted = _extract(_pdf(draw))

    assert extracted.disposition == DocumentDisposition.PASS
    assert not any(kind.startswith("hidden_text") for kind in _kinds(extracted))
    assert TEXT in extracted.sanitized_text


def test_white_text_on_a_white_page_is_hidden() -> None:
    extracted = _extract(_pdf(lambda page: page.insert_text((60, 135), TEXT, color=WHITE)))

    assert _kinds(extracted)["hidden_text.low_visibility"] == FindingSeverity.HIGH
    assert extracted.disposition == DocumentDisposition.QUARANTINE
    assert TEXT not in extracted.sanitized_text
    assert "Visible heading" in extracted.sanitized_text


def test_white_text_on_a_dark_shape_under_a_white_shape_is_hidden() -> None:
    def draw(page: Any) -> None:
        page.draw_rect(fitz.Rect(50, 100, 400, 160), color=None, fill=NAVY)
        page.draw_rect(fitz.Rect(50, 100, 400, 160), color=None, fill=WHITE)
        page.insert_text((60, 135), TEXT, color=WHITE)

    extracted = _extract(_pdf(draw))

    assert _kinds(extracted)["hidden_text.low_visibility"] == FindingSeverity.HIGH
    assert TEXT not in extracted.sanitized_text


def test_white_text_covered_by_a_later_dark_shape_is_hidden() -> None:
    def draw(page: Any) -> None:
        page.insert_text((60, 135), TEXT, color=WHITE)
        page.draw_rect(fitz.Rect(50, 100, 400, 160), color=None, fill=NAVY)

    extracted = _extract(_pdf(draw))

    assert _kinds(extracted)["hidden_text.covered"] == FindingSeverity.HIGH
    assert extracted.disposition == DocumentDisposition.QUARANTINE
    assert TEXT not in extracted.sanitized_text


def test_dark_text_covered_by_a_later_white_shape_is_hidden() -> None:
    def draw(page: Any) -> None:
        page.insert_text((60, 135), TEXT, color=(0, 0, 0))
        page.draw_rect(fitz.Rect(50, 100, 400, 160), color=None, fill=WHITE)

    extracted = _extract(_pdf(draw))

    assert _kinds(extracted)["hidden_text.covered"] == FindingSeverity.HIGH
    assert TEXT not in extracted.sanitized_text


def test_an_underline_drawn_after_the_text_does_not_cover_it() -> None:
    def draw(page: Any) -> None:
        page.insert_text((60, 135), TEXT, color=(0, 0, 0))
        page.draw_rect(fitz.Rect(60, 137, 230, 138), color=None, fill=(0, 0, 0))

    extracted = _extract(_pdf(draw))

    assert extracted.disposition == DocumentDisposition.PASS
    assert TEXT in extracted.sanitized_text


def test_dark_text_on_a_matching_dark_shape_is_hidden() -> None:
    def draw(page: Any) -> None:
        page.draw_rect(fitz.Rect(50, 100, 400, 160), color=None, fill=NAVY)
        page.insert_text((60, 135), TEXT, color=(0.12, 0.12, 0.32))

    extracted = _extract(_pdf(draw))

    assert _kinds(extracted)["hidden_text.low_visibility"] == FindingSeverity.HIGH
    assert TEXT not in extracted.sanitized_text


def test_dark_text_on_a_light_shape_is_visible() -> None:
    def draw(page: Any) -> None:
        page.draw_rect(fitz.Rect(50, 100, 400, 160), color=None, fill=(0.9, 0.9, 0.9))
        page.insert_text((60, 135), TEXT, color=(0, 0, 0))

    extracted = _extract(_pdf(draw))

    assert extracted.disposition == DocumentDisposition.PASS
    assert TEXT in extracted.sanitized_text


def test_near_white_text_on_a_light_grey_shape_is_hidden() -> None:
    grey = (0.85, 0.85, 0.85)
    assert contrast_ratio(rgb(WHITE), rgb(grey)) < 3

    def draw(page: Any) -> None:
        page.draw_rect(fitz.Rect(50, 100, 400, 160), color=None, fill=grey)
        page.insert_text((60, 135), TEXT, color=(0.97, 0.97, 0.97))

    extracted = _extract(_pdf(draw))

    assert _kinds(extracted)["hidden_text.low_visibility"] == FindingSeverity.HIGH
    assert TEXT not in extracted.sanitized_text


def test_a_faint_dark_shape_does_not_make_white_text_visible() -> None:
    def draw(page: Any) -> None:
        shape = page.new_shape()
        shape.draw_rect(fitz.Rect(50, 100, 400, 160))
        shape.finish(color=None, fill=NAVY, fill_opacity=0.1)
        shape.commit()
        page.insert_text((60, 135), TEXT, color=WHITE)

    extracted = _extract(_pdf(draw))

    assert _kinds(extracted)["hidden_text.low_visibility"] == FindingSeverity.HIGH


def test_a_clipped_dark_shape_does_not_make_white_text_visible() -> None:
    # A page-sized dark rectangle clipped to a strip far from the text.
    def draw(page: Any) -> None:
        page.insert_text((60, 135), TEXT, color=WHITE)

    data = _pdf(draw)
    document = fitz.open(stream=data, filetype="pdf")
    page = document[0]
    page.clean_contents()
    stream = b"q 0 0 612 10 re W n 0.1 0.1 0.3 rg 0 0 612 792 re f Q\n"
    xref = page.get_contents()[0]
    document.update_stream(xref, stream + document.xref_stream(xref))

    extracted = _extract(bytes(document.tobytes()))

    assert _kinds(extracted)["hidden_text.low_visibility"] == FindingSeverity.HIGH


def test_white_text_on_a_dark_image_is_visible() -> None:
    def draw(page: Any) -> None:
        _image(page, fitz.Rect(50, 100, 400, 160), 20)
        page.insert_text((60, 135), TEXT, color=WHITE)

    extracted = _extract(_pdf(draw))

    assert extracted.disposition == DocumentDisposition.PASS
    assert TEXT in extracted.sanitized_text


def test_white_text_on_a_white_image_is_hidden() -> None:
    def draw(page: Any) -> None:
        _image(page, fitz.Rect(50, 100, 400, 160), 255)
        page.insert_text((60, 135), TEXT, color=WHITE)

    extracted = _extract(_pdf(draw))

    assert _kinds(extracted)["hidden_text.low_visibility"] == FindingSeverity.HIGH
    assert TEXT not in extracted.sanitized_text


def test_white_text_on_an_image_past_the_sample_budget_is_low() -> None:
    def draw(page: Any) -> None:
        _image(page, fitz.Rect(50, 100, 400, 160), 20)
        page.insert_text((60, 135), TEXT, color=WHITE)

    extracted = _extract(_pdf(draw), max_background_samples=0)

    assert _kinds(extracted)["hidden_text.on_image"] == FindingSeverity.LOW
    assert "hidden_text.low_visibility" not in _kinds(extracted)
    assert extracted.disposition == DocumentDisposition.REVIEW
    assert TEXT not in extracted.sanitized_text


def test_white_text_on_a_dark_rounded_shape_is_judged_from_the_pixels() -> None:
    def draw(page: Any) -> None:
        page.draw_rect(fitz.Rect(50, 100, 400, 160), color=None, fill=NAVY, radius=0.2)
        page.insert_text((60, 135), TEXT, color=WHITE)

    assert TEXT in _extract(_pdf(draw)).sanitized_text
    assert "hidden_text.on_image" in _kinds(_extract(_pdf(draw), max_background_samples=0))


def test_the_sample_budget_must_not_be_negative() -> None:
    with pytest.raises(ValueError, match="max_background_samples"):
        PdfDocumentExtractor(max_background_samples=-1)


def test_the_docx_check_uses_the_same_contrast_scale() -> None:
    assert is_invisible(0xFFFFFF, 0xFAFAFA)
    assert not is_invisible(0xFFFFFF, 0x1A1A4D)
    assert rgb((0.5,)) == 0x808080
    assert rgb((0, 0, 0, 1)) == 0x000000
