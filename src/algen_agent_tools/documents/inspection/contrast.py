"""Colour contrast on the WCAG scale, shared by the DOCX and PDF hidden-text checks.

Colours are ``0xRRGGBB`` integers. The ratio runs from 1 (the same colour) to 21 (black on
white).
"""

from __future__ import annotations

INVISIBLE_RATIO = 1.25
"""Below this, text is indistinguishable from its background."""
VISIBLE_RATIO = 3.0
"""At or above this, text stands out from its background (the WCAG minimum for large text)."""


def luminance(rgb: int) -> float:
    """Relative luminance of an sRGB colour, from 0 (black) to 1 (white)."""

    def channel(value: int) -> float:
        scaled = value / 255
        return scaled / 12.92 if scaled <= 0.03928 else ((scaled + 0.055) / 1.055) ** 2.4

    red, green, blue = (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(first: int, second: int) -> float:
    lighter, darker = sorted((luminance(first), luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)
