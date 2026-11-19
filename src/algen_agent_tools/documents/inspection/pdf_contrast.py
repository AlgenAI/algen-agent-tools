"""Work out what a PDF text span is drawn on, so text is judged by contrast, not colour alone.

White text on a dark sidebar or header is ordinary design (Canva and similar résumé builders
draw it everywhere), so near-white text is hidden only when what is behind it is light too.
What is behind a span comes from the page's own drawing order (PyMuPDF's ``seqno``, the index
into :meth:`Page.get_bboxlog`):

- filled rectangles drawn *before* the text are stacked, with their opacity, over a white page;
- an opaque rectangle drawn *after* the text and over most of it hides it, whatever the colours;
- an image, shading or non-rectangular shape under the text cannot be judged from the vectors,
  so a small clip of the rendered page is sampled instead (a bounded number per document).

A page with more filled shapes than :data:`MAX_PAGE_SHAPES`, or a reader without these PyMuPDF
methods, is not analysed: near-white text there is judged by its colour alone, as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from algen_agent_tools.documents.inspection.contrast import (
    INVISIBLE_RATIO,
    VISIBLE_RATIO,
    contrast_ratio,
    luminance,
)

_Box = tuple[float, float, float, float]
UNKNOWN = -1
WHITE = 0xFFFFFF
MAX_PAGE_SHAPES = 2_000
"""More filled shapes and images than this on one page, and the page is not analysed."""
_COVERAGE = 0.6
"""A shape is behind (or over) a span when it covers at least this share of the span's box."""
_OPAQUE = 0.95
_SAMPLE_PIXELS = 64
"""The longer side of a sampled clip, in pixels."""
_SAMPLE_PERCENTILE = 0.25
"""The sampled background is the pixel this far up the luminance order: the darkest quarter,
so the near-white glyphs drawn in the clip do not count as background."""


class Visibility(StrEnum):
    VISIBLE = "visible"
    HIDDEN = "hidden"
    """The text nearly matches what it is drawn on."""
    COVERED = "covered"
    """An opaque shape drawn after the text lies over it."""
    UNJUDGED = "unjudged"
    """Near-white text over an image that was not sampled (the sample budget ran out)."""


@dataclass(frozen=True, slots=True)
class _Layer:
    seqno: int
    rect: _Box
    fill: int
    """``0xRRGGBB``, or ``UNKNOWN`` for an image, shading or non-rectangular shape."""
    opacity: float


def is_pale(color: int) -> bool:
    """Near-white: every channel at least 245."""
    return all(channel >= 245 for channel in ((color >> 16) & 255, (color >> 8) & 255, color & 255))


def rgb(components: Any) -> int:
    """A PyMuPDF colour tuple (grey, RGB or CMYK, each 0 to 1) as ``0xRRGGBB``."""
    try:
        values = [min(max(float(value), 0.0), 1.0) for value in components]
    except (TypeError, ValueError):
        return UNKNOWN
    if len(values) == 1:
        values = values * 3
    elif len(values) == 4:
        cyan, magenta, yellow, black = values
        values = [(1 - cyan) * (1 - black), (1 - magenta) * (1 - black), (1 - yellow) * (1 - black)]
    elif len(values) != 3:
        return UNKNOWN
    red, green, blue = (round(value * 255) for value in values)
    return (red << 16) | (green << 8) | blue


def _blend(top: int, bottom: int, opacity: float) -> int:
    channels = []
    for shift in (16, 8, 0):
        upper, lower = (top >> shift) & 255, (bottom >> shift) & 255
        channels.append(round(upper * opacity + lower * (1 - opacity)))
    return (channels[0] << 16) | (channels[1] << 8) | channels[2]


def _coverage(rect: _Box, box: _Box) -> float:
    area = max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0)
    if area <= 0:
        return 0.0
    width = min(rect[2], box[2]) - max(rect[0], box[0])
    height = min(rect[3], box[3]) - max(rect[1], box[1])
    return max(width, 0.0) * max(height, 0.0) / area


def _box(value: Any) -> _Box | None:
    try:
        x0, y0, x1, y1 = (float(item) for item in tuple(value)[:4])
    except (TypeError, ValueError):
        return None
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def _clipped(rect: _Box, clip: _Box | None) -> _Box | None:
    if clip is None:
        return rect
    return _box(
        (max(rect[0], clip[0]), max(rect[1], clip[1]), min(rect[2], clip[2]), min(rect[3], clip[3]))
    )


def _shape_layers(drawing: dict[str, Any], clip: _Box | None, group_opacity: float) -> list[_Layer]:
    """One layer per rectangle of a filled path; the whole path's box, unjudged, otherwise."""
    if drawing.get("fill") is None or drawing.get("seqno") is None:
        return []
    seqno = int(drawing["seqno"])
    opacity = drawing.get("fill_opacity")
    opacity = (1.0 if opacity is None else float(opacity)) * group_opacity
    fill = rgb(drawing["fill"])
    rects: list[_Box] = []
    exact = not drawing.get("even_odd")
    for item in drawing.get("items", ()):
        kind = item[0] if item else None
        if kind == "re":
            box = _box(item[1])
        elif kind == "qu" and getattr(item[1], "is_rectangular", False):
            box = _box(item[1].rect)
        else:
            box = None
        if box is None:
            exact = False
            break
        rects.append(box)
    if not exact or fill == UNKNOWN:
        whole = _box(drawing.get("rect"))
        rects, fill = ([whole] if whole else []), UNKNOWN
    layers = []
    for box in rects:
        clipped = _clipped(box, clip)
        if clipped is not None:
            layers.append(_Layer(seqno, clipped, fill, opacity))
    return layers


class PageBackdrop:
    """What one page draws, read lazily: nothing is read until a span needs it, and the page's
    shapes, text order and display list are each read once."""

    def __init__(self, fitz: Any, page: Any, budget: SampleBudget) -> None:
        self._fitz = fitz
        self._page = page
        self._budget = budget
        self._layers: list[_Layer] | None = None
        self._analysable = True
        self._text: list[tuple[float, float, int, int]] | None = None
        self._display: Any = None

    def _load_layers(self) -> list[_Layer] | None:
        if self._layers is not None or not self._analysable:
            return self._layers
        self._analysable = False
        bboxlog = getattr(self._page, "get_bboxlog", None)
        if bboxlog is None or getattr(self._page, "get_texttrace", None) is None:
            return None
        log = bboxlog()
        layers: list[_Layer] = []
        for seqno, (kind, rect) in enumerate(log):
            if str(kind).startswith(("fill-image", "fill-shade")):
                box = _box(rect)
                if box is not None:
                    layers.append(_Layer(seqno, box, UNKNOWN, 1.0))
        if any(kind == "fill-path" for kind, _ in log):
            # Extended mode reports clip paths and transparency groups as levels that enclose
            # the drawings after them: a shape counts only where it is clipped to.
            scopes: list[tuple[int, _Box | None, float]] = []
            for drawing in self._page.get_drawings(extended=True):
                level = int(drawing.get("level", 0))
                while scopes and scopes[-1][0] >= level:
                    scopes.pop()
                clip, opacity = (scopes[-1][1], scopes[-1][2]) if scopes else (None, 1.0)
                kind = drawing.get("type")
                if kind == "clip":
                    scissor = _box(drawing.get("scissor"))
                    inner = _clipped(scissor, clip) if scissor else clip
                    scopes.append((level, inner or (0.0, 0.0, 0.0, 0.0), opacity))
                elif kind == "group":
                    group = drawing.get("opacity")
                    scopes.append((level, clip, opacity * (1.0 if group is None else float(group))))
                else:
                    if clip is not None and clip[2] <= clip[0]:
                        continue  # clipped away entirely
                    layers.extend(_shape_layers(drawing, clip, opacity))
                if len(layers) > MAX_PAGE_SHAPES:
                    return None
        if len(layers) > MAX_PAGE_SHAPES:
            return None
        layers.sort(key=lambda layer: layer.seqno)
        self._layers = layers
        self._analysable = True
        return layers

    def _text_steps(self, box: _Box, color: int) -> tuple[int, int] | None:
        """The first and last drawing steps of the text inside ``box``, preferring pieces of
        the same colour (one extracted span can join several drawn pieces)."""
        if self._text is None:
            self._text = []
            for trace in self._page.get_texttrace():
                found = _box(trace.get("bbox"))
                if found is not None:
                    self._text.append(
                        (
                            (found[0] + found[2]) / 2,
                            (found[1] + found[3]) / 2,
                            rgb(trace.get("color", ())),
                            int(trace.get("seqno", -1)),
                        )
                    )
        inside = [
            (seqno, traced == color)
            for x, y, traced, seqno in self._text
            if box[0] - 1 <= x <= box[2] + 1 and box[1] - 1 <= y <= box[3] + 1 and seqno >= 0
        ]
        same = [seqno for seqno, matches in inside if matches]
        candidates = same or [seqno for seqno, _ in inside]
        return (min(candidates), max(candidates)) if candidates else None

    def judge(self, box: _Box, color: int) -> Visibility | None:
        """How visible text of ``color`` in ``box`` is; ``None`` when the page cannot be
        analysed, so the caller falls back to judging the colour alone."""
        layers = self._load_layers()
        if layers is None:
            return None
        pale = is_pale(color)
        over = [layer for layer in layers if _coverage(layer.rect, box) >= _COVERAGE]
        if not over:
            return Visibility.HIDDEN if pale else Visibility.VISIBLE
        steps = self._text_steps(box, color)
        if steps is None:
            return None
        first, last = steps
        if any(
            layer.seqno > last and layer.fill != UNKNOWN and layer.opacity >= _OPAQUE
            for layer in over
        ):
            return Visibility.COVERED
        background = WHITE
        for layer in over:
            if layer.seqno > last:
                break
            if layer.seqno > first:  # drawn between pieces of the text: judge the pixels
                background = UNKNOWN
            elif layer.opacity >= _OPAQUE or layer.fill == UNKNOWN:
                background = layer.fill
            elif background != UNKNOWN and layer.opacity > 0:
                background = _blend(layer.fill, background, layer.opacity)
        if background == UNKNOWN:
            if not pale:
                return Visibility.VISIBLE
            sampled = self._sample(box)
            if sampled is None:
                return Visibility.UNJUDGED
            background = sampled
        ratio = contrast_ratio(color, background)
        if pale:
            return Visibility.VISIBLE if ratio >= VISIBLE_RATIO else Visibility.HIDDEN
        return Visibility.HIDDEN if ratio < INVISIBLE_RATIO else Visibility.VISIBLE

    def _sample(self, box: _Box) -> int | None:
        """The background colour of ``box`` on the rendered page, within the budget."""
        if not self._budget.take():
            return None
        try:
            if self._display is None:
                self._display = self._page.get_displaylist()
            zoom = min(2.0, _SAMPLE_PIXELS / max(box[2] - box[0], box[3] - box[1]))
            pixmap = self._display.get_pixmap(
                matrix=self._fitz.Matrix(zoom, zoom),
                colorspace=self._fitz.csRGB,
                alpha=False,
                clip=self._fitz.Rect(*box),
            )
            samples = bytes(pixmap.samples)
            step = int(pixmap.n)
        except Exception:  # a page that fails to render is not sampled
            return None
        if step < 3 or len(samples) < step:
            return None
        pixels = [
            (samples[index] << 16) | (samples[index + 1] << 8) | samples[index + 2]
            for index in range(0, len(samples) - step + 1, step)
        ]
        pixels.sort(key=luminance)
        return pixels[int(len(pixels) * _SAMPLE_PERCENTILE)]


class SampleBudget:
    """How many clips one document may still render to judge text over an image."""

    def __init__(self, remaining: int) -> None:
        self.remaining = remaining

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True
