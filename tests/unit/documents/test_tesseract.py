"""TesseractRecognizer, the ``ocr`` extra, with a fake pytesseract (no tesseract program)."""

from __future__ import annotations

import io
import sys
import types
from typing import Any

import pytest
from algen_agent_runtime.exceptions.errors import ConfigurationError

from algen_agent_tools.documents import TesseractRecognizer, extractor_for


class FakeTesseract(types.ModuleType):
    """Records each frame it is asked to read and returns one line per call."""

    class TesseractNotFoundError(OSError):
        pass

    class Output:
        DICT = "dict"

    def __init__(self, *, languages: tuple[str, ...] = ("eng", "deu"), found: bool = True) -> None:
        super().__init__("pytesseract")
        self.languages = languages
        self.found = found
        self.frames: list[tuple[str, tuple[int, int], str, float]] = []

    def get_languages(self, config: str = "") -> list[str]:
        if not self.found:
            raise self.TesseractNotFoundError()
        return list(self.languages)

    def image_to_data(
        self, image: Any, *, lang: str, output_type: str, timeout: float
    ) -> dict[str, list[Any]]:
        assert output_type == self.Output.DICT
        self.frames.append((image.mode, image.size, lang, timeout))
        number = len(self.frames)
        return {
            "text": ["", "Invoice", f"{number}", "", "Total", "42"],
            "conf": [-1, 90, 80, -1, 70, 60],
            "block_num": [0, 1, 1, 1, 1, 1],
            "par_num": [0, 1, 1, 1, 1, 1],
            "line_num": [0, 1, 1, 0, 2, 2],
        }


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeTesseract:
    pytest.importorskip("PIL")
    fake = FakeTesseract()
    monkeypatch.setitem(sys.modules, "pytesseract", fake)
    return fake


def image(mode: str = "RGB", frames: int = 1, fmt: str = "PNG") -> bytes:
    from PIL import Image

    color: Any = (0, 0, 0, 0) if mode == "RGBA" else "white"
    pictures = [Image.new(mode, (40, 20), color) for _ in range(frames)]
    buffer = io.BytesIO()
    pictures[0].save(buffer, fmt, save_all=frames > 1, append_images=pictures[1:])
    return buffer.getvalue()


def test_without_the_extra_the_error_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pytesseract", None)
    with pytest.raises(ConfigurationError, match=r"algen-agent-tools\[ocr\]"):
        TesseractRecognizer()


def test_without_the_program_the_error_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("PIL")
    monkeypatch.setitem(sys.modules, "pytesseract", FakeTesseract(found=False))
    with pytest.raises(ConfigurationError, match="tesseract program"):
        TesseractRecognizer()


def test_a_language_that_is_not_installed_is_named(engine: FakeTesseract) -> None:
    with pytest.raises(ConfigurationError, match="'hin'"):
        TesseractRecognizer(languages=("eng", "hin"))


@pytest.mark.parametrize(
    "options",
    [
        {"languages": "eng"},
        {"languages": ()},
        {"languages": ("eng --psm 0",)},
        {"timeout": 0},
        {"max_frames": 0},
    ],
)
def test_settings_outside_the_rules_are_refused(engine: FakeTesseract, options: Any) -> None:
    with pytest.raises(ValueError):
        TesseractRecognizer(**options)


def test_words_are_joined_into_lines_with_their_mean_confidence(engine: FakeTesseract) -> None:
    result = TesseractRecognizer(languages=("eng", "deu"), timeout=5).recognize(
        image(), media_type="image/png"
    )

    assert result.text == "Invoice 1\nTotal 42"
    assert result.confidence == pytest.approx(0.75)
    assert engine.frames == [("L", (40, 20), "eng+deu", 5)]


def test_a_transparent_image_is_read_over_white(engine: FakeTesseract) -> None:
    from PIL import Image

    TesseractRecognizer().recognize(image("RGBA"), media_type="image/png")
    assert engine.frames[0][0] == "L"
    # The frame handed to tesseract is white where the source was transparent.
    recognizer = TesseractRecognizer()
    with Image.open(io.BytesIO(image("RGBA"))) as picture:
        assert recognizer._greyscale(picture).getpixel((0, 0)) == 255


def test_every_frame_of_a_tiff_is_read_up_to_max_frames(engine: FakeTesseract) -> None:
    result = TesseractRecognizer(max_frames=2).recognize(
        image(frames=3, fmt="TIFF"), media_type="image/tiff"
    )

    assert len(engine.frames) == 2
    assert result.text == "Invoice 1\nTotal 42\n\nInvoice 2\nTotal 42"


def test_it_plugs_into_the_image_extractor(engine: FakeTesseract) -> None:
    extracted = extractor_for("scan.png", ocr=TesseractRecognizer()).extract(
        image(), filename="scan.png"
    )

    assert extracted.sanitized_text == "Invoice 1\nTotal 42"
    assert (extracted.spans[0].source, extracted.coverage) == ("ocr", "full")
