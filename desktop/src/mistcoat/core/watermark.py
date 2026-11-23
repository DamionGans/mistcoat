"""A visible watermark ("SAMPLE", the artist's name, ...) drawn into the
picture before the protection runs.

No visible watermark is impossible to remove, but most are easy: removal
tools learn one even, repeated shape and paint it out. The resistance levels
make that harder:

* plain    - an even grid of identical marks. Neat, and easy to remove.
* strong   - every mark differs slightly in position, angle, size and
             opacity, with a soft, unevenly textured edge, so there is no
             single shape to learn.
* maximum  - as strong, plus a second, smaller row of marks between the
             first, and the marks are woven into the picture: the fine detail
             inside the letters is inverted and their edges are bevelled, so
             removing the colour still leaves the lettering in the texture.

In every case the protection passes run afterwards, over the watermarked
picture, so the mark is part of the protected pixels.

All sizes are relative to the picture, and the random choices depend only on
the seed, the text and the aspect ratio, so the downscaled preview shows the
same layout as the full-size result.
"""
from __future__ import annotations

import math
import zlib
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import imaging

LAYOUTS = ("tiled", "centre", "corner")
RESISTANCE = ("plain", "strong", "maximum")

# Bold sans-serif fonts that are usually present; Pillow's own font otherwise.
_FONT_CANDIDATES = (
    "segoeuib.ttf", "arialbd.ttf",                                  # Windows
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",            # macOS
    "/System/Library/Fonts/Helvetica.ttc",
    "DejaVuSans-Bold.ttf",                                          # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "LiberationSans-Bold.ttf",
)


@lru_cache(maxsize=1)
def _font_path() -> str | None:
    for name in _FONT_CANDIDATES:
        try:
            ImageFont.truetype(name, 12)
            return name
        except OSError:
            continue
    return None


@lru_cache(maxsize=96)
def _font(size: int) -> ImageFont.FreeTypeFont:
    path = _font_path()
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


@dataclass
class Mark:
    """One copy of the text: centre, height in pixels, angle, opacity factor."""
    x: float
    y: float
    size: float
    angle: float
    opacity: float = 1.0


def _text_ratio(text: str) -> float:
    """Text width divided by its font size."""
    f = _font(100)
    l, _t, r, _b = f.getbbox(text)
    return max(r - l, 1) / 100.0


def _layout(text: str, w: int, h: int, layout: str, resistance: str,
            rng: np.random.Generator) -> list[Mark]:
    short = min(w, h)
    ratio = _text_ratio(text)
    jitter = resistance != "plain"
    marks: list[Mark] = []

    if layout == "corner":
        size = max(10.0, 0.05 * short)
        size = min(size, 0.45 * w / ratio)
        margin = 0.025 * short
        tw = ratio * size
        s = size * (rng.uniform(0.94, 1.06) if jitter else 1.0)
        marks.append(Mark(w - margin - tw / 2, h - margin - size / 2, s,
                          rng.uniform(-2, 2) if jitter else 0.0, 1.0))
        return marks

    if layout == "centre":
        angle = float(np.clip(math.degrees(math.atan2(h, w)), 15, 40))
        a = math.radians(angle)
        size = min(0.85 * w / (ratio * math.cos(a) + math.sin(a)),
                   0.85 * h / (ratio * math.sin(a) + math.cos(a)))
        if jitter:
            marks.append(Mark(w / 2 + rng.uniform(-0.03, 0.03) * w,
                              h / 2 + rng.uniform(-0.03, 0.03) * h,
                              size * rng.uniform(0.95, 1.0),
                              angle + rng.uniform(-3, 3), 1.0))
        else:
            marks.append(Mark(w / 2, h / 2, size, angle))
        return marks

    # Tiled: staggered rows of diagonal text covering the whole picture.
    size = max(9.0, 0.06 * short)
    size = min(size, 0.7 * max(w, h) / ratio)      # long text: keep it on the page
    tw = ratio * size
    dx = tw * 1.5 + size
    dy = size * 3.2
    rows = int(math.ceil(h / dy)) + 2
    cols = int(math.ceil(w / dx)) + 2
    for layer in ((0, 1.0, 1.0),) + (((0.5, 0.5, 0.6),) if resistance == "maximum" else ()):
        shift, scale, opacity = layer
        for r in range(rows):
            y = (r - 0.5 + shift) * dy
            offset = (dx / 2) * (r % 2) + shift * dx / 2
            for c in range(cols):
                x = (c - 0.5) * dx + offset
                m = Mark(x, y, size * scale, 30.0, opacity)
                if jitter:
                    m.x += rng.uniform(-0.12, 0.12) * dx
                    m.y += rng.uniform(-0.15, 0.15) * dy
                    m.angle += rng.uniform(-7, 7)
                    m.size *= rng.uniform(0.88, 1.12)
                    m.opacity *= rng.uniform(0.75, 1.0)
                marks.append(m)
    return marks


def _stamp(text: str, mark: Mark) -> tuple[np.ndarray, np.ndarray]:
    """The letters (fill) and their outline (stroke) for one mark, rotated."""
    size = max(6, int(round(mark.size)))
    font = _font(size)
    stroke = max(1, int(round(size * 0.06)))
    l, t, r, b = font.getbbox(text, stroke_width=stroke)
    pad = stroke + 2
    box = (r - l + 2 * pad, b - t + 2 * pad)
    at = (pad - l, pad - t)
    outer = Image.new("L", box)
    ImageDraw.Draw(outer).text(at, text, font=font, fill=255,
                               stroke_width=stroke, stroke_fill=255)
    inner = Image.new("L", box)
    ImageDraw.Draw(inner).text(at, text, font=font, fill=255)
    outer = outer.rotate(mark.angle, resample=Image.BICUBIC, expand=True)
    inner = inner.rotate(mark.angle, resample=Image.BICUBIC, expand=True)
    fill = np.asarray(inner, np.float32) / 255.0
    ring = np.clip(np.asarray(outer, np.float32) / 255.0 - fill, 0, 1)
    return fill * mark.opacity, ring * mark.opacity


def _paste_max(dst: np.ndarray, src: np.ndarray, cx: float, cy: float) -> None:
    h, w = dst.shape
    sh, sw = src.shape
    x0 = int(round(cx - sw / 2))
    y0 = int(round(cy - sh / 2))
    xa, ya = max(0, x0), max(0, y0)
    xb, yb = min(w, x0 + sw), min(h, y0 + sh)
    if xa >= xb or ya >= yb:
        return
    region = dst[ya:yb, xa:xb]
    np.maximum(region, src[ya - y0:yb - y0, xa - x0:xb - x0], out=region)


def _smooth_noise(w: int, h: int, rng: np.random.Generator, cells: int) -> np.ndarray:
    """Soft random field in 0..1, the same pattern at any resolution."""
    short = min(w, h)
    gw = int(math.ceil(w / short * cells)) + 1
    gh = int(math.ceil(h / short * cells)) + 1
    grid = rng.random((gh, gw)).astype(np.float32)
    return np.asarray(Image.fromarray(grid, "F").resize((w, h), Image.BICUBIC),
                      np.float32).clip(0, 1)


def _over(rgb: np.ndarray, alpha: np.ndarray | None, colour: float,
          cover: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    """Paint ``colour`` over the picture with per-pixel coverage ``cover``."""
    c = cover[:, :, None]
    if alpha is None:
        return rgb * (1.0 - c) + colour * c, None
    a = alpha[:, :, None]
    premultiplied = rgb * a * (1.0 - c) + colour * c
    new_a = a * (1.0 - c) + c
    return premultiplied / np.maximum(new_a, 1e-6), new_a[:, :, 0]


def apply(rgb: np.ndarray, alpha: np.ndarray | None, *, text: str,
          layout: str = "tiled", resistance: str = "strong",
          opacity: float = 0.5, seed: int = 0
          ) -> tuple[np.ndarray, np.ndarray | None]:
    """Return ``(rgb, alpha)`` with the watermark drawn in. Transparent areas
    under the mark become opaque enough to show it."""
    text = " ".join(text.split())
    opacity = float(np.clip(opacity, 0.0, 1.0))
    if not text or opacity <= 0:
        return rgb, alpha
    layout = layout if layout in LAYOUTS else "tiled"
    resistance = resistance if resistance in RESISTANCE else "strong"
    h, w = rgb.shape[:2]
    aspect = int(round(w / h * 1000))
    key = [int(seed) & 0xFFFFFFFF, zlib.crc32(text.encode("utf-8")), aspect]
    rng = np.random.default_rng(key)
    noise_rng = np.random.default_rng(key + [1])

    marks = _layout(text, w, h, layout, resistance, rng)
    fill = np.zeros((h, w), np.float32)
    ring = np.zeros((h, w), np.float32)
    for m in marks:
        f, r = _stamp(text, m)
        _paste_max(fill, f, m.x, m.y)
        _paste_max(ring, r, m.x, m.y)

    size = marks[0].size
    if resistance != "plain":
        # Uneven, textured coverage: no two letters have the same profile.
        field = 0.55 + 0.45 * _smooth_noise(w, h, noise_rng, 24)
        grain = 1.0 + 0.18 * noise_rng.standard_normal((h, w)).astype(np.float32)
        soft = max(0.6, size * 0.012)
        fill = np.clip(imaging.blur(fill, soft) * field * grain, 0, 1)
        ring = np.clip(imaging.blur(ring, soft) * field, 0, 1)

    out = rgb.astype(np.float32, copy=True)
    strength = 1.5 if layout == "corner" else 1.0
    if resistance == "maximum":
        # Weave the letters into the texture: invert the fine detail inside
        # them and bevel their edges.
        detail = out - imaging.blur(out, max(0.8, size * 0.03))
        k = min(1.0, opacity * 2.0)
        out = out - 1.6 * k * fill[:, :, None] * detail
        shift = max(1, int(round(size * 0.04)))
        soft_fill = imaging.blur(fill, max(0.8, size * 0.03))
        bevel = soft_fill - np.roll(soft_fill, (shift, shift), axis=(0, 1))
        out = out + (0.35 * k * bevel)[:, :, None]
        out = np.clip(out, 0.0, 1.0)

    # A dark outline and a light fill: readable on light and dark pictures.
    out, alpha = _over(out, alpha, 0.0, np.clip(ring * 0.6 * opacity * strength, 0, 1))
    out, alpha = _over(out, alpha, 1.0, np.clip(fill * 0.8 * opacity * strength, 0, 1))
    if alpha is not None:
        alpha = alpha.astype(np.float32)
    return np.clip(out, 0.0, 1.0).astype(np.float32), alpha
