"""TesseractRecognizer against the real tesseract program.

Run with ``ALGEN_TOOLS_LIVE=1 pytest tests/integration/test_tesseract_live.py`` after installing
the ``ocr`` extra and tesseract with English trained data.
"""

from __future__ import annotations

import io

import pytest

pytestmark = pytest.mark.live


def test_tesseract_reads_a_rendered_line_of_text() -> None:
    pytest.importorskip("pytesseract")
    from PIL import Image, ImageDraw, ImageFont

    from algen_agent_tools.documents import TesseractRecognizer, extractor_for

    picture = Image.new("L", (900, 160), "white")
    ImageDraw.Draw(picture).text(
        (30, 40), "INVOICE 4217", fill="black", font=ImageFont.load_default(64)
    )
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")

    extracted = extractor_for("scan.png", ocr=TesseractRecognizer()).extract(
        buffer.getvalue(), filename="scan.png"
    )

    assert "INVOICE 4217" in extracted.sanitized_text
    assert extracted.spans[0].confidence is not None
