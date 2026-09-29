"""Colour helpers shared by the config, the effects and the engine."""

from __future__ import annotations

from typing import Tuple

RGB = Tuple[float, float, float]

PALETTE = {
    "orange": "#FF6A00",
    "purple": "#8A2BE2",
    "toxic green": "#39FF14",
    "blood red": "#C81010",
    "ghost white": "#F2F0E6",
    "midnight": "#1A0B3D",
    "amber": "#FFB020",
    "off": "#000000",
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def hex_to_rgb(value) -> RGB:
    """Accepts '#rrggbb', 'rrggbb' or '#rgb' and returns floats in 0..1."""
    if not isinstance(value, str):
        return (0.0, 0.0, 0.0)
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        return (0.0, 0.0, 0.0)
    try:
        return (
            int(text[0:2], 16) / 255.0,
            int(text[2:4], 16) / 255.0,
            int(text[4:6], 16) / 255.0,
        )
    except ValueError:
        return (0.0, 0.0, 0.0)


def rgb_to_hex(rgb) -> str:
    r, g, b = (int(round(_clamp(float(c), 0.0, 1.0) * 255)) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def normalise_hex(value, fallback: str = "#000000") -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    text = value.strip().lstrip("#")
    if len(text) not in (3, 6):
        return fallback
    try:
        int(text, 16)
    except ValueError:
        return fallback
    return rgb_to_hex(hex_to_rgb(value))
