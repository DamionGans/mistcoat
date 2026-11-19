"""Image I/O and numpy signal helpers.

No scipy dependency: blurs are built from cumulative-sum box filters and the
DCT is a small matrix multiply over 8x8 blocks.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

Image.MAX_IMAGE_PIXELS = None  # artists legitimately have huge scans

SUPPORTED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".jfif",
}


@dataclass
class LoadedImage:
    """An image decoded to float32 RGB in 0..1 plus everything needed to save it back."""

    rgb: np.ndarray                 # (H, W, 3) float32, 0..1
    alpha: np.ndarray | None        # (H, W) float32, 0..1, or None
    icc_profile: bytes | None
    source_mode: str
    source_format: str | None
    path: Path | None = None
    exif: bytes | None = None
    extra: dict = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.rgb.shape[:2]
        return w, h

    @property
    def megapixels(self) -> float:
        h, w = self.rgb.shape[:2]
        return (h * w) / 1_000_000


def load_image(path: str | Path) -> LoadedImage:
    path = Path(path)
    with Image.open(path) as im:
        im.load()
        source_mode = im.mode
        source_format = im.format
        icc = im.info.get("icc_profile")
        exif_bytes = im.info.get("exif")

        # Normalise exotic modes (CMYK scans, palettes, 16-bit TIFFs) to 8-bit RGB(A).
        if im.mode == "CMYK":
            if icc:
                try:
                    srgb = ImageCms.createProfile("sRGB")
                    src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                    im = ImageCms.profileToProfile(im, src, srgb, outputMode="RGB")
                    icc = None  # now plain sRGB
                except Exception:
                    im = im.convert("RGB")
            else:
                im = im.convert("RGB")

        if im.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
            arr = np.asarray(im).astype(np.float32)
            hi = float(arr.max()) if arr.max() > 0 else 1.0
            scale = 65535.0 if hi > 255 else 255.0
            arr = np.clip(arr / scale, 0, 1)
            rgb = np.repeat(arr[:, :, None], 3, axis=2)
            return LoadedImage(rgb.astype(np.float32), None, None, source_mode,
                               source_format, path, exif_bytes)

        has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        im = im.convert("RGBA" if has_alpha else "RGB")
        arr = np.asarray(im).astype(np.float32) / 255.0

    if arr.shape[2] == 4:
        return LoadedImage(np.ascontiguousarray(arr[:, :, :3]),
                           np.ascontiguousarray(arr[:, :, 3]),
                           icc, source_mode, source_format, path, exif_bytes)
    return LoadedImage(arr, None, icc, source_mode, source_format, path, exif_bytes)


def to_pil(img: LoadedImage, rgb: np.ndarray | None = None) -> Image.Image:
    data = img.rgb if rgb is None else rgb
    arr = np.clip(data, 0.0, 1.0)
    out = (arr * 255.0 + 0.5).astype(np.uint8)
    if img.alpha is not None:
        a = (np.clip(img.alpha, 0, 1) * 255.0 + 0.5).astype(np.uint8)
        return Image.fromarray(np.dstack([out, a]), mode="RGBA")
    return Image.fromarray(out, mode="RGB")


# ---------------------------------------------------------------- filters

def _box_blur_1d(a: np.ndarray, radius: int, axis: int) -> np.ndarray:
    """O(n) box blur along one axis with edge clamping."""
    if radius < 1:
        return a
    a = np.moveaxis(a, axis, 0)
    n = a.shape[0]
    radius = min(radius, max(n - 1, 1))
    pad = np.concatenate([
        np.repeat(a[:1], radius, axis=0), a, np.repeat(a[-1:], radius, axis=0)
    ], axis=0)
    cs = np.cumsum(pad, axis=0, dtype=np.float64)
    cs = np.concatenate([np.zeros_like(cs[:1]), cs], axis=0)
    width = 2 * radius + 1
    out = (cs[width:width + n] - cs[:n]) / width
    return np.moveaxis(out.astype(np.float32), 0, axis)


def blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian approximation: three box passes (error < 3% for sigma > 1)."""
    if sigma <= 0.01:
        return a.astype(np.float32, copy=True)
    radius = max(1, int(round(sigma * 0.886)))
    out = a.astype(np.float32)
    for _ in range(3):
        out = _box_blur_1d(out, radius, 0)
        out = _box_blur_1d(out, radius, 1)
    return out


def luminance(rgb: np.ndarray) -> np.ndarray:
    return (0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1]
            + 0.0722 * rgb[:, :, 2]).astype(np.float32)


def local_activity(rgb: np.ndarray, sigma: float = 2.0) -> np.ndarray:
    """0..1 map of local detail. High where texture can hide perturbation."""
    lum = luminance(rgb)
    mean = blur(lum, sigma)
    var = np.maximum(blur(lum * lum, sigma) - mean * mean, 0.0)
    std = np.sqrt(var)
    hi = float(np.percentile(std, 98))
    if hi < 1e-6:
        hi = 1e-6
    return np.clip(std / hi, 0.0, 1.0).astype(np.float32)


# ---------------------------------------------------------------- colour

_RGB2YCC = np.array([[0.299, 0.587, 0.114],
                     [-0.168736, -0.331264, 0.5],
                     [0.5, -0.418688, -0.081312]], dtype=np.float32)
_YCC2RGB = np.linalg.inv(_RGB2YCC).astype(np.float32)


def rgb_to_ycbcr(rgb: np.ndarray) -> np.ndarray:
    """Y in 0..1, Cb/Cr centred on 0."""
    return (rgb @ _RGB2YCC.T).astype(np.float32)


def ycbcr_to_rgb(ycc: np.ndarray) -> np.ndarray:
    return (ycc @ _YCC2RGB.T).astype(np.float32)


# ---------------------------------------------------------------- DCT

_DCT_N = 8


def _dct_matrix(n: int = _DCT_N) -> np.ndarray:
    k = np.arange(n)[:, None]
    i = np.arange(n)[None, :]
    m = np.cos(math.pi * (2 * i + 1) * k / (2 * n))
    m *= math.sqrt(2.0 / n)
    m[0] *= 1 / math.sqrt(2.0)
    return m.astype(np.float32)


_D = _dct_matrix()


def blocks_from(channel: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Split a 2-D channel into (nby, nbx, 8, 8) blocks, padding by edge replication."""
    h, w = channel.shape
    ph = (-h) % _DCT_N
    pw = (-w) % _DCT_N
    if ph or pw:
        channel = np.pad(channel, ((0, ph), (0, pw)), mode="edge")
    H, W = channel.shape
    blocks = channel.reshape(H // _DCT_N, _DCT_N, W // _DCT_N, _DCT_N).transpose(0, 2, 1, 3)
    return np.ascontiguousarray(blocks), (h, w, ph, pw)


def blocks_to(blocks: np.ndarray, shape: tuple[int, int, int, int]) -> np.ndarray:
    h, w, _ph, _pw = shape
    nby, nbx = blocks.shape[:2]
    out = blocks.transpose(0, 2, 1, 3).reshape(nby * _DCT_N, nbx * _DCT_N)
    return np.ascontiguousarray(out[:h, :w])


def dct2(blocks: np.ndarray) -> np.ndarray:
    return _D @ blocks @ _D.T


def idct2(blocks: np.ndarray) -> np.ndarray:
    return _D.T @ blocks @ _D


# JPEG luminance quantisation table at quality 50 - used to pick coefficients
# that survive re-encoding (small divisor = better survival).
JPEG_Q50_LUMA = np.array([
    [16, 11, 10, 16, 24, 40, 51, 61],
    [12, 12, 14, 19, 26, 58, 60, 55],
    [14, 13, 16, 24, 40, 57, 69, 56],
    [14, 17, 22, 29, 51, 87, 80, 62],
    [18, 22, 37, 56, 68, 109, 103, 77],
    [24, 35, 55, 64, 81, 104, 113, 92],
    [49, 64, 78, 87, 103, 121, 120, 101],
    [72, 92, 95, 98, 112, 100, 103, 99],
], dtype=np.float32)


def warp(rgb: np.ndarray, dx: np.ndarray, dy: np.ndarray) -> np.ndarray:
    """Bilinear resample by a per-pixel displacement field (in pixels)."""
    h, w = rgb.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    sx = np.clip(xx + dx, 0, w - 1)
    sy = np.clip(yy + dy, 0, h - 1)
    x0 = np.floor(sx).astype(np.int32)
    y0 = np.floor(sy).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (sx - x0)[:, :, None]
    fy = (sy - y0)[:, :, None]
    top = rgb[y0, x0] * (1 - fx) + rgb[y0, x1] * fx
    bot = rgb[y1, x0] * (1 - fx) + rgb[y1, x1] * fx
    return (top * (1 - fy) + bot * fy).astype(np.float32)


def jpeg_roundtrip(rgb: np.ndarray, quality: int = 75) -> np.ndarray:
    """Encode/decode through JPEG. Used to test and harden perturbation survival."""
    arr = (np.clip(rgb, 0, 1) * 255.0 + 0.5).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="JPEG", quality=quality,
                                          subsampling=2)
    buf.seek(0)
    with Image.open(buf) as im:
        out = np.asarray(im.convert("RGB")).astype(np.float32) / 255.0
    return out


def resize_longest(rgb: np.ndarray, longest: int) -> np.ndarray:
    h, w = rgb.shape[:2]
    if max(h, w) <= longest:
        return rgb
    scale = longest / max(h, w)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    arr = (np.clip(rgb, 0, 1) * 255.0 + 0.5).astype(np.uint8)
    im = Image.fromarray(arr, mode="RGB").resize((nw, nh), Image.LANCZOS)
    return np.asarray(im).astype(np.float32) / 255.0


def webp_roundtrip(rgb: np.ndarray, quality: int = 75) -> np.ndarray:
    arr = (np.clip(rgb, 0, 1) * 255.0 + 0.5).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, mode="RGB").save(buf, format="WEBP", quality=quality)
    buf.seek(0)
    with Image.open(buf) as im:
        return np.asarray(im.convert("RGB")).astype(np.float32) / 255.0


def rescale_roundtrip(rgb: np.ndarray, factor: float = 0.75,
                      quality: int = 80) -> np.ndarray:
    """Downscale (as a platform thumbnailer would), JPEG-encode, scale back."""
    h, w = rgb.shape[:2]
    arr = (np.clip(rgb, 0, 1) * 255.0 + 0.5).astype(np.uint8)
    im = Image.fromarray(arr, mode="RGB")
    small = im.resize((max(1, int(w * factor)), max(1, int(h * factor))), Image.LANCZOS)
    buf = io.BytesIO()
    small.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    with Image.open(buf) as dec:
        back = dec.convert("RGB").resize((w, h), Image.LANCZOS)
    return np.asarray(back).astype(np.float32) / 255.0


# Transforms hardening optimises against. Deliberately disjoint from the
# held-out set below in quality settings and scale.
TRAINING_TRANSFORMS = (
    ("JPEG q72", lambda x: jpeg_roundtrip(x, 72)),
    ("WebP q82", lambda x: webp_roundtrip(x, 82)),
    ("Resize 80% + JPEG", lambda x: rescale_roundtrip(x, 0.8, 75)),
)

# Held-out transforms for *measuring* durability. Hardening optimises against
# JPEG q72 only; scoring against that same encoder would be grading on the
# training set. These approximate what real platforms do to an upload.
HELD_OUT_TRANSFORMS = (
    ("JPEG q85", lambda x: jpeg_roundtrip(x, 85)),
    ("JPEG q60", lambda x: jpeg_roundtrip(x, 60)),
    ("WebP q75", lambda x: webp_roundtrip(x, 75)),
    ("Resize 75% + JPEG", lambda x: rescale_roundtrip(x, 0.75, 80)),
)
