"""Images read by the host's OCR recognizer.

Used only when a :class:`~algen_agent_tools.documents.inspection.ocr.TextRecognizer` is given;
without one, ``extractor_for`` refuses images as before. The image is checked before the
recognizer sees it: its content must match its suffix, and its dimensions, read from the header
with the standard library, must be within ``max_pixels`` (a small file cannot claim a huge
canvas). A GIF or TIFF can hold many frames and a recognizer may decode them all, so every frame
is read and the limit applies to their total.
"""

from __future__ import annotations

import struct
from pathlib import Path

from algen_agent_tools.documents.inspection.core import (
    DocumentSecurityFinding,
    DocumentSpan,
    ExtractedDocument,
    classify_findings,
    recognize_span,
    sanitize_untrusted_text,
)
from algen_agent_tools.documents.inspection.ocr import TextRecognizer

IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

MAX_FRAMES = 64
"""The most frames a GIF or TIFF may hold."""


def image_media_type(data: bytes) -> str | None:
    """The image type the content's signature declares, or ``None`` for anything else."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data[:4] in {b"II*\x00", b"MM\x00*"}:
        return "image/tiff"
    return None


def image_size(data: bytes, media_type: str) -> tuple[int, int] | None:
    """Width and height from the image header, or ``None`` when the header cannot be read."""
    try:
        if media_type == "image/png":
            if data[12:16] != b"IHDR":
                return None
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if media_type == "image/gif":
            width, height = struct.unpack("<HH", data[6:10])
            return int(width), int(height)
        if media_type == "image/bmp":
            return _bmp_size(data)
        if media_type == "image/jpeg":
            return _jpeg_size(data)
        if media_type == "image/webp":
            return _webp_size(data)
        if media_type == "image/tiff":
            return _tiff_size(data)
    except (struct.error, IndexError):
        return None
    return None


def image_frames(data: bytes, media_type: str) -> tuple[tuple[int, int], ...] | None:
    """The width and height of every frame, or ``None`` when a header cannot be read.

    A GIF or TIFF gives one entry per frame, at most ``MAX_FRAMES + 1`` (the caller refuses
    more); the other types give one.
    """
    try:
        if media_type == "image/gif":
            return _gif_frames(data)
        if media_type == "image/tiff":
            return _tiff_frames(data)
    except (struct.error, IndexError):
        return None
    size = image_size(data, media_type)
    return None if size is None else (size,)


def _gif_frames(data: bytes) -> tuple[tuple[int, int], ...] | None:
    screen_width, screen_height, packed = struct.unpack("<HHB", data[6:11])
    position = 13
    if packed & 0x80:
        position += 3 << ((packed & 0x07) + 1)
    frames: list[tuple[int, int]] = []
    while len(frames) <= MAX_FRAMES and position < len(data):
        block = data[position]
        if block == 0x3B:
            break
        if block == 0x21:
            position = _skip_gif_sub_blocks(data, position + 2)
        elif block == 0x2C:
            left, top, width, height, packed = struct.unpack(
                "<HHHHB", data[position + 1 : position + 10]
            )
            # A frame can extend past the logical screen; decoders size the canvas for both.
            frames.append((max(screen_width, left + width), max(screen_height, top + height)))
            position += 10
            if packed & 0x80:
                position += 3 << ((packed & 0x07) + 1)
            position = _skip_gif_sub_blocks(data, position + 1)
        else:
            return None
    return tuple(frames) or None


def _skip_gif_sub_blocks(data: bytes, position: int) -> int:
    while data[position]:
        position += 1 + data[position]
    return position + 1


def _tiff_frames(data: bytes) -> tuple[tuple[int, int], ...] | None:
    order = "<" if data[:2] == b"II" else ">"
    (offset,) = struct.unpack(f"{order}I", data[4:8])
    frames: list[tuple[int, int]] = []
    seen: set[int] = set()
    while offset and len(frames) <= MAX_FRAMES:
        if offset in seen:
            return None
        seen.add(offset)
        size, offset = _tiff_ifd(data, order, offset)
        if size is None:
            return None
        frames.append(size)
    return tuple(frames) or None


def _bmp_size(data: bytes) -> tuple[int, int]:
    (header,) = struct.unpack("<I", data[14:18])
    if header == 12:
        width, height = struct.unpack("<HH", data[18:22])
    else:
        width, height = struct.unpack("<ii", data[18:26])
    return abs(int(width)), abs(int(height))


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    position = 2
    while position + 9 <= len(data):
        if data[position] != 0xFF:
            return None
        marker = data[position + 1]
        if marker == 0xFF:
            position += 1
            continue
        if 0xD0 <= marker <= 0xD9 or marker == 0x01:
            position += 2
            continue
        (length,) = struct.unpack(">H", data[position + 2 : position + 4])
        if 0xC0 <= marker <= 0xCF and marker not in {0xC4, 0xC8, 0xCC}:
            height, width = struct.unpack(">HH", data[position + 5 : position + 9])
            return int(width), int(height)
        position += 2 + length
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8 ":
        width, height = struct.unpack("<HH", data[26:30])
        return int(width) & 0x3FFF, int(height) & 0x3FFF
    if chunk == b"VP8L":
        b0, b1, b2, b3 = data[21:25]
        return 1 + (b0 | (b1 & 0x3F) << 8), 1 + ((b1 >> 6) | b2 << 2 | (b3 & 0x0F) << 10)
    if chunk == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    return None


def _tiff_size(data: bytes) -> tuple[int, int] | None:
    order = "<" if data[:2] == b"II" else ">"
    (offset,) = struct.unpack(f"{order}I", data[4:8])
    return _tiff_ifd(data, order, offset)[0]


def _tiff_ifd(data: bytes, order: str, offset: int) -> tuple[tuple[int, int] | None, int]:
    """One image file directory's width and height, and the offset of the next one (0 at the
    end)."""
    (count,) = struct.unpack(f"{order}H", data[offset : offset + 2])
    size: dict[int, int] = {}
    for index in range(min(count, 512)):
        entry = data[offset + 2 + index * 12 : offset + 14 + index * 12]
        tag, kind = struct.unpack(f"{order}HH", entry[:4])
        if tag in {256, 257}:
            value_format = f"{order}H" if kind == 3 else f"{order}I"
            (size[tag],) = struct.unpack_from(value_format, entry, 8)
    next_at = offset + 2 + count * 12
    (next_offset,) = struct.unpack(f"{order}I", data[next_at : next_at + 4])
    if 256 in size and 257 in size:
        return (size[256], size[257]), next_offset
    return None, next_offset


class ImageDocumentExtractor:
    """Recognize the text in one PNG, JPEG, WebP, GIF, BMP or TIFF image.

    The recognized text is a ``source="ocr"`` span and passes the same prompt-injection checks
    and sanitizing as any document text. A GIF or TIFF with more than ``MAX_FRAMES`` frames, or
    whose frames together exceed ``max_pixels``, is refused; otherwise it is passed to the
    recognizer whole.
    """

    def __init__(
        self,
        *,
        ocr: TextRecognizer,
        max_bytes: int = 10_000_000,
        max_pixels: int = 40_000_000,
        max_characters: int = 250_000,
    ) -> None:
        self._ocr = ocr
        self._max_bytes = max_bytes
        self._max_pixels = max_pixels
        self._max_characters = max_characters

    def extract(self, data: bytes, *, filename: str) -> ExtractedDocument:
        if len(data) > self._max_bytes:
            raise ValueError(f"document exceeds {self._max_bytes} byte limit")
        declared = IMAGE_MEDIA_TYPES.get(Path(filename).suffix.lower())
        media_type = image_media_type(data)
        # A name that is not an image's (scan.pdf) defers to the content, which extractor_for
        # sniffed; an image name must match the content.
        if media_type is None or (declared is not None and media_type != declared):
            raise ValueError("file content does not match its image type")
        frames = image_frames(data, media_type)
        if not frames or any(width < 1 or height < 1 for width, height in frames):
            raise ValueError("image dimensions could not be read")
        if len(frames) > MAX_FRAMES:
            raise ValueError(f"image has more than {MAX_FRAMES} frames")
        if sum(width * height for width, height in frames) > self._max_pixels:
            raise ValueError(f"image exceeds {self._max_pixels} pixel limit")
        span, ocr_findings = recognize_span(self._ocr, data, media_type=media_type, page=1)
        findings: list[DocumentSecurityFinding] = list(ocr_findings)
        spans: tuple[DocumentSpan, ...] = () if span is None else (span,)
        text = span.text if span is not None else ""
        if len(text) > self._max_characters:
            raise ValueError(f"image exceeds {self._max_characters} recognized character limit")
        visible = text if span is not None and not span.suspicious else ""
        sanitized, visible_findings = sanitize_untrusted_text(visible)
        findings.extend(visible_findings)
        unique_findings, disposition = classify_findings(findings)
        return ExtractedDocument(
            media_type=media_type,
            text=text,
            sanitized_text=sanitized,
            spans=spans,
            findings=unique_findings,
            disposition=disposition,
            image_count=1,
        )
