"""The OCR hook: a recognizer the host supplies, and the page rendering that feeds it.

This package ships no OCR engine. A host passes any object with a ``recognize`` method (a
:class:`TextRecognizer`) to ``extractor_for(..., ocr=...)`` or ``documents_toolkit(ocr=...)``;
without one, images are refused and scanned PDF pages are reported as ``document.ocr_required``,
exactly as before. Recognized text is untrusted like any other document text: the extractors run
it through the same prompt-injection checks and sanitizing.
"""

from __future__ import annotations

import math
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class RecognizedText(BaseModel):
    """The text a recognizer read from one image."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    """The recognizer's overall confidence, from 0 to 1, when it reports one."""


class TextRecognizer(Protocol):
    """Reads the text in one image. ``media_type`` is the image's type, for example
    ``image/png``. Called from a worker thread; a slow recognizer blocks only its own call."""

    def recognize(self, image: bytes, *, media_type: str) -> RecognizedText: ...


def validate_ocr_options(*, max_pages: int, min_page_chars: int, dpi: int, max_pixels: int) -> None:
    """Refuse OCR settings outside sane bounds when an extractor or toolkit is built."""
    if max_pages < 0:
        raise ValueError("ocr_max_pages must be 0 or more")
    if min_page_chars < 1:
        raise ValueError("ocr_min_page_chars must be at least 1")
    if not 50 <= dpi <= 600:
        raise ValueError("ocr_dpi must be between 50 and 600")
    if max_pixels < 10_000:
        raise ValueError("ocr_max_pixels must be at least 10000")


def render_page_png(fitz: Any, page: Any, *, dpi: int, max_pixels: int) -> bytes:
    """Render one PyMuPDF page as a greyscale PNG at ``dpi``, scaled down so the image has at
    most ``max_pixels`` pixels (an oversized page cannot produce an oversized image)."""
    zoom = dpi / 72
    width = max(float(page.rect.width), 1.0) * zoom
    height = max(float(page.rect.height), 1.0) * zoom
    if width * height > max_pixels:
        # A little under the cap: the renderer rounds each side up to a whole pixel.
        zoom *= math.sqrt(max_pixels / (width * height)) * 0.99
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY, alpha=False)
    return bytes(pixmap.tobytes("png"))
