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
from PIL import Image

from . import color, deep

Image.MAX_IMAGE_PIXELS = None  # artists legitimately have huge scans

SUPPORTED_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp", ".jfif",
}


@dataclass
class LoadedImage:
    """An image decoded to float32 RGB in 0..1 plus everything needed to save
    it back the way it came in: colour model, bit depth, profile, resolution.

    ``rgb`` holds the file's own values for RGB and greyscale images (no
    colour conversion), and values in ``working_icc`` for CMYK and Lab, whose
    original pixels stay in ``native``.
    """

    rgb: np.ndarray                 # (H, W, 3) float32, 0..1
    alpha: np.ndarray | None        # (H, W) float32, 0..1, or None
    icc_profile: bytes | None       # the profile to embed when saving
    source_mode: str
    source_format: str | None
    path: Path | None = None
    exif: bytes | None = None
    extra: dict = field(default_factory=dict)
    bits: int = 8                   # 8 or 16 bits per channel in the file
    color_model: str = "RGB"        # "RGB", "GRAY", "CMYK" or "LAB"
    working_icc: bytes | None = None  # what ``rgb`` is in; None means sRGB
    native: np.ndarray | None = None  # CMYK/Lab pixels, 0..1
    converter: object = None        # color.Converter for CMYK/Lab
    dpi: tuple[float, float] | None = None
    profile_note: str = ""          # where the profile came from, if not embedded

    def __post_init__(self):
        if (self.working_icc is None and self.icc_profile
                and self.color_model in ("RGB", "GRAY")):
            self.working_icc = self.icc_profile

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.rgb.shape[:2]
        return w, h

    @property
    def megapixels(self) -> float:
        h, w = self.rgb.shape[:2]
        return (h * w) / 1_000_000

    @property
    def color_summary(self) -> str:
        """e.g. "16-bit RGB, Adobe RGB (1998)" for the status line and reports."""
        model = {"GRAY": "greyscale"}.get(self.color_model, self.color_model)
        name = color.profile_name(self.icc_profile)
        if self.color_model == "CMYK" and not self.icc_profile:
            name = "no profile"
        if self.color_model == "LAB":
            name = "CIE L*a*b*"
        note = f" ({self.profile_note})" if self.profile_note else ""
        return f"{self.bits}-bit {model}, {name}{note}"


_DEEP_MODES = ("RGB", "RGBA", "CMYK", "LA")


def _dpi(info: dict) -> tuple[float, float] | None:
    dpi = info.get("dpi")
    try:
        if dpi and float(dpi[0]) > 0 and float(dpi[1]) > 0:
            return float(dpi[0]), float(dpi[1])
    except (TypeError, ValueError, IndexError):
        pass
    return None


def load_image(path: str | Path) -> LoadedImage:
    """Decode an image without changing its colours.

    Handles RGB, greyscale, palette, CMYK and Lab, 8 and 16 bits per channel,
    any embedded ICC profile, and the profiles PNGs and cameras imply without
    embedding one (PNG gAMA/cHRM, EXIF "Adobe RGB").
    """
    path = Path(path)
    with Image.open(path) as im:
        source_mode = im.mode
        source_format = im.format
        info = dict(im.info)
        icc = info.get("icc_profile") or None
        exif_bytes = info.get("exif")
        # Before anything else: reading EXIF can make Pillow decode the image,
        # after which it no longer says how the file stores its samples.
        deep16 = source_mode in _DEEP_MODES and deep.is_16bit(im)
        gray_alpha = deep.first_rawmode(im).startswith("LA")   # 16-bit LA opens as RGBA
        try:
            exif_obj = im.getexif()
        except Exception:  # noqa: BLE001
            exif_obj = None
        if not deep16:
            im.load()
            values, model, bits, has_alpha, note = _decode(im)

    if deep16:
        arr = deep.read_16bit(path, source_mode)
        if arr is None:                             # fall back to Pillow's 8 bits
            with Image.open(path) as im:
                im.load()
                values, model, bits, has_alpha, note = _decode(im)
        else:
            values = arr.astype(np.float32) / 65535.0
            model = {"RGB": "RGB", "RGBA": "RGB", "CMYK": "CMYK", "LA": "GRAY"}[source_mode]
            bits, has_alpha, note = 16, source_mode in ("RGBA", "LA"), ""
            if gray_alpha and values.shape[2] == 4:
                values, model = values[:, :, [0, 3]], "GRAY"

    alpha = None
    if has_alpha:
        alpha = np.ascontiguousarray(values[:, :, -1])
        values = values[:, :, :-1]

    # A profile only counts if it describes this kind of data.
    want = {"RGB": "RGB", "GRAY": "GRAY", "CMYK": "CMYK", "LAB": "Lab"}[model]
    if icc and color.profile_space(icc) != want:
        icc = None
    if model == "RGB" and not icc:
        if source_format == "PNG" and "srgb" not in info:
            icc = color.profile_from_png(info)
            note = note or ("from the PNG's gamma and chromaticity" if icc else "")
        if not icc and color.exif_says_adobe_rgb(exif_obj):
            icc, note = color.ADOBE_RGB, "from the camera's EXIF"

    img = LoadedImage(
        rgb=None, alpha=alpha, icc_profile=icc, source_mode=source_mode,
        source_format=source_format, path=path, exif=exif_bytes, bits=bits,
        color_model=model, dpi=_dpi(info), profile_note=note)
    if model in ("CMYK", "LAB"):
        conv = color.Converter(model, icc)
        img.native, img.converter = values, conv
        img.rgb = conv.to_rgb(values)
        img.working_icc = conv.working_icc
    elif model == "GRAY":
        img.rgb = np.ascontiguousarray(np.repeat(values[:, :, :1], 3, axis=2))
        img.working_icc = icc
    else:
        img.rgb = np.ascontiguousarray(values[:, :, :3])
        img.working_icc = icc
    img.rgb = img.rgb.astype(np.float32, copy=False)
    return img


def _decode(im: Image.Image):
    """Pillow's decoded image -> (values (H, W, C) float 0..1, model, bits,
    has_alpha, note)."""
    mode = im.mode
    note = ""
    if mode in ("I;16", "I;16B", "I;16L", "I;16N", "I", "F"):
        arr = np.asarray(im).astype(np.float32)
        hi = float(arr.max()) if arr.size and arr.max() > 0 else 1.0
        is16 = mode.startswith("I;16") or hi > 255
        arr = np.clip(arr / (65535.0 if is16 else 255.0), 0, 1)
        return arr[:, :, None], "GRAY", 16 if is16 else 8, False, ""
    if mode == "1":
        im, note = im.convert("L"), "black-and-white, saved as greyscale"
        mode = "L"
    if mode == "La":
        im, mode = im.convert("LA"), "LA"
    if mode in ("L", "LA"):
        arr = np.asarray(im).astype(np.float32) / 255.0
        arr = arr[:, :, None] if arr.ndim == 2 else arr
        return arr, "GRAY", 8, mode == "LA", note
    if mode == "CMYK":
        return np.asarray(im).astype(np.float32) / 255.0, "CMYK", 8, False, ""
    if mode == "LAB":
        return np.asarray(im).astype(np.float32) / 255.0, "LAB", 8, False, ""
    if mode in ("P", "PA"):
        note = "palette image, saved as full colour"
    has_alpha = mode in ("RGBA", "RGBa", "PA") or (
        mode in ("P", "L", "RGB") and "transparency" in im.info)
    im = im.convert("RGBA" if has_alpha else "RGB")
    return np.asarray(im).astype(np.float32) / 255.0, "RGB", 8, has_alpha, note


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
    # Box passes along different axes commute, so all three vertical passes
    # then all three horizontal ones equal the interleaved order. accel runs
    # them on the GPU or across CPU cores.
    from . import accel
    return accel.box_blur3(a, radius, _box_blur_1d)


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
