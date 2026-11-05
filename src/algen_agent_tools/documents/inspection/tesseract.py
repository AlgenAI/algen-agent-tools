"""Tesseract OCR for document inspection: the optional ``ocr`` extra.

:class:`TesseractRecognizer` is a :class:`~algen_agent_tools.documents.inspection.ocr.TextRecognizer`
backed by the Tesseract engine through pytesseract. It is never the default: a host builds one and
passes it as ``documents_toolkit(ocr=...)`` or ``extractor_for(..., ocr=...)``. It needs the
``ocr`` extra (pytesseract and Pillow) and the ``tesseract`` program with the trained data for
its languages, which the host installs.
"""

from __future__ import annotations

import io
import re
from collections.abc import Sequence
from typing import Any

from algen_agent_runtime.exceptions.errors import ConfigurationError

from algen_agent_tools._env import missing_extra
from algen_agent_tools.documents.inspection.ocr import RecognizedText

_LANGUAGE = re.compile(r"[A-Za-z0-9_]{1,32}")


class TesseractRecognizer:
    """Read the text in an image with Tesseract.

    ``languages`` are Tesseract language codes (``eng``, ``deu``, ``hin``); each must be
    installed. Every frame of a multi-page TIFF or an animated GIF is read, up to
    ``max_frames``, and ``timeout`` seconds bound each frame. The construction fails, naming the
    fix, when the extra, the program or a language is missing.
    """

    def __init__(
        self,
        *,
        languages: Sequence[str] = ("eng",),
        timeout: float = 30,
        max_frames: int = 64,
    ) -> None:
        if isinstance(languages, str) or not languages:
            raise ValueError("languages is a list of Tesseract language codes, such as ['eng']")
        if not all(_LANGUAGE.fullmatch(language) for language in languages):
            raise ValueError("languages are Tesseract language codes, such as 'eng' or 'deu'")
        if timeout <= 0:
            raise ValueError("timeout must be more than 0 seconds")
        if max_frames < 1:
            raise ValueError("max_frames must be at least 1")
        try:
            import pytesseract
            from PIL import Image, ImageSequence
        except ImportError as exc:
            raise missing_extra("pytesseract", "ocr") from exc
        try:
            installed = set(pytesseract.get_languages(config=""))
        except pytesseract.TesseractNotFoundError:
            raise ConfigurationError(
                "the ocr extra needs the tesseract program on PATH; install it (for example "
                "'apt install tesseract-ocr' or 'brew install tesseract')"
            ) from None
        missing = [language for language in languages if language not in installed]
        if missing:
            raise ConfigurationError(
                f"tesseract has no trained data for {missing}; install those languages"
            )
        self._pytesseract: Any = pytesseract
        self._image: Any = Image
        self._sequence: Any = ImageSequence
        self._lang = "+".join(languages)
        self._timeout = timeout
        self._max_frames = max_frames

    def recognize(self, image: bytes, *, media_type: str) -> RecognizedText:
        del media_type
        pages: list[str] = []
        confidences: list[float] = []
        with self._image.open(io.BytesIO(image)) as picture:
            for index, frame in enumerate(self._sequence.Iterator(picture)):
                if index >= self._max_frames:
                    break
                text, frame_confidences = self._read(self._greyscale(frame))
                if text:
                    pages.append(text)
                confidences.extend(frame_confidences)
        confidence = sum(confidences) / len(confidences) / 100 if confidences else None
        return RecognizedText(
            text="\n\n".join(pages),
            confidence=None if confidence is None else max(0.0, min(1.0, confidence)),
        )

    def _greyscale(self, frame: Any) -> Any:
        # Transparent pixels would turn black; lay the frame over white first.
        if frame.mode in {"RGBA", "LA", "PA"} or "transparency" in frame.info:
            rgba = frame.convert("RGBA")
            background = self._image.new("RGBA", rgba.size, (255, 255, 255, 255))
            return self._image.alpha_composite(background, rgba).convert("L")
        return frame.convert("L")

    def _read(self, frame: Any) -> tuple[str, list[float]]:
        """The frame's text, one line per Tesseract line, and each word's confidence (0-100)."""
        data = self._pytesseract.image_to_data(
            frame,
            lang=self._lang,
            output_type=self._pytesseract.Output.DICT,
            timeout=self._timeout,
        )
        lines: dict[tuple[int, int, int], list[str]] = {}
        confidences: list[float] = []
        for position, word in enumerate(data["text"]):
            if not str(word).strip():
                continue
            key = (
                data["block_num"][position],
                data["par_num"][position],
                data["line_num"][position],
            )
            lines.setdefault(key, []).append(str(word).strip())
            confidence = float(data["conf"][position])
            if confidence >= 0:
                confidences.append(confidence)
        return "\n".join(" ".join(words) for words in lines.values()), confidences
