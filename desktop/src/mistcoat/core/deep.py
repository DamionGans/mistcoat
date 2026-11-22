"""16-bit colour images: reading them in full and writing them back.

Pillow keeps 16-bit greyscale but reduces 16-bit RGB, RGBA and CMYK to 8
bits when it decodes them. Scans and exports from Photoshop or Krita are
often 16-bit, and "save it the way it came in" means keeping all 16 bits, so:

* Reading decodes the file twice with Pillow, once for the high byte of every
  sample (its normal 8-bit result) and once for the low byte, by asking for
  the opposite byte order. The filtering and decompression stay in Pillow's C
  code; only the unpacking differs.
* Writing uses the small PNG and TIFF writers below. Both write lossless,
  compressed files that every image editor reads.
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import numpy as np
from PIL import Image

_SWAP = {";16B": ";16L", ";16L": ";16B"}


def _other_byte(rawmode: str) -> str | None:
    if rawmode.endswith(";16N"):
        rawmode = rawmode[:-4] + (";16L" if sys.byteorder == "little" else ";16B")
    for a, b in _SWAP.items():
        if rawmode.endswith(a):
            return rawmode[: -len(a)] + b
    return None


def _swap_tile(tile, rawmode_of):
    args = tile.args
    if isinstance(args, str):
        new = rawmode_of(args)
        return None if new is None else tile._replace(args=new)
    if isinstance(args, tuple) and args and isinstance(args[0], str):
        new = rawmode_of(args[0])
        return None if new is None else tile._replace(args=(new, *args[1:]))
    return None


def first_rawmode(im: Image.Image) -> str:
    """How Pillow will unpack the file, e.g. "RGB;16B" (before ``load``)."""
    return (_rawmode(im.tile[0]) or "") if im.tile else ""


def is_16bit(im: Image.Image) -> bool:
    """True for a 16-bit colour image Pillow would reduce to 8 bits."""
    return bool(im.tile) and all(
        (_rawmode(t) or "")[-4:] in (";16B", ";16L", ";16N") for t in im.tile)


def read_16bit(path: Path, frame_mode: str) -> np.ndarray | None:
    """All 16 bits of a 16-bit RGB/RGBA/CMYK/LA image as uint16 (H, W, C), or
    None when the file is 8-bit or its layout is one this trick can't read."""
    with Image.open(path) as im:
        if im.mode != frame_mode or not im.tile:
            return None
        if not all(_rawmode(t) and _rawmode(t)[-4:] in (";16B", ";16L", ";16N")
                   for t in im.tile):
            return None
        im.load()
        hi = np.asarray(im)
    with Image.open(path) as im:
        swapped = [_swap_tile(t, _other_byte) for t in im.tile]
        if any(t is None for t in swapped):
            return None
        im.tile = swapped
        try:
            im.load()
        except (OSError, ValueError, SyntaxError):
            return None
        lo = np.asarray(im)
    if hi.shape != lo.shape:
        return None
    out = (hi.astype(np.uint16) << 8) | lo.astype(np.uint16)
    return out if out.ndim == 3 else out[:, :, None]


def _rawmode(tile) -> str | None:
    args = tile.args
    if isinstance(args, str):
        return args
    if isinstance(args, tuple) and args and isinstance(args[0], str):
        return args[0]
    return None


# ------------------------------------------------------------------ PNG

def _chunk(kind: bytes, data: bytes) -> bytes:
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))


def write_png16(path: Path, arr: np.ndarray, *, icc: bytes | None = None,
                dpi: tuple[float, float] | None = None, exif: bytes | None = None,
                xmp: str = "") -> None:
    """arr: (H, W, C) uint16 with C = 1 grey, 2 grey+alpha, 3 RGB, 4 RGBA."""
    h, w, c = arr.shape
    colour_type = {1: 0, 2: 4, 3: 2, 4: 6}[c]
    rows = np.ascontiguousarray(arr.astype(">u2")).view(np.uint8).reshape(h, w * c * 2)
    # "Up" filter on every row: cheap to compute and compresses scans well.
    up = np.empty_like(rows)
    up[0] = rows[0]
    up[1:] = rows[1:] - rows[:-1]                    # wraps mod 256, as PNG wants
    raw = np.hstack([np.full((h, 1), 2, np.uint8), up]).tobytes()

    out = [b"\x89PNG\r\n\x1a\n",
           _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, colour_type, 0, 0, 0))]
    if icc:
        out.append(_chunk(b"iCCP", b"ICC profile\0\0" + zlib.compress(icc)))
    if dpi:
        ppm = [max(1, int(round(float(d) / 0.0254))) for d in dpi]
        out.append(_chunk(b"pHYs", struct.pack(">IIB", ppm[0], ppm[1], 1)))
    if exif:
        out.append(_chunk(b"eXIf", exif[6:] if exif.startswith(b"Exif\0\0") else exif))
    if xmp:
        out.append(_chunk(b"iTXt", b"XML:com.adobe.xmp\0\0\0\0\0" + xmp.encode("utf-8")))
    data = zlib.compress(raw, 6)
    for i in range(0, len(data), 1 << 23):
        out.append(_chunk(b"IDAT", data[i:i + (1 << 23)]))
    out.append(_chunk(b"IEND", b""))
    path.write_bytes(b"".join(out))


# ------------------------------------------------------------------ TIFF

_SHORT, _LONG, _RATIONAL, _ASCII, _BYTE, _UNDEF = 3, 4, 5, 2, 1, 7
_SIZE = {_BYTE: 1, _ASCII: 1, _SHORT: 2, _LONG: 4, _RATIONAL: 8, _UNDEF: 1}


def write_tiff16(path: Path, arr: np.ndarray, *, photometric: str,
                 icc: bytes | None = None, dpi: tuple[float, float] | None = None,
                 xmp: str = "", artist: str = "", copyright_notice: str = "",
                 orientation: int | None = None) -> None:
    """arr: (H, W, C) uint16. photometric: "gray", "rgb" or "cmyk"; a fourth
    channel after RGB or a second after grey is written as alpha."""
    h, w, c = arr.shape
    base = {"gray": 1, "rgb": 3, "cmyk": 4}[photometric]
    extra = c - base

    # Horizontal differencing (predictor 2) then Deflate, like Photoshop's ZIP.
    diff = arr.astype(np.uint16, copy=True)
    diff[:, 1:, :] = arr[:, 1:, :] - arr[:, :-1, :]
    rows_per_strip = max(1, (1 << 20) // max(1, w * c * 2))
    strips = [zlib.compress(diff[y:y + rows_per_strip].astype("<u2").tobytes(), 6)
              for y in range(0, h, rows_per_strip)]

    blobs: list[bytes] = []
    offset = 8

    def place(data: bytes) -> int:
        nonlocal offset
        at = offset
        blobs.append(data + (b"\0" if len(data) % 2 else b""))
        offset += len(blobs[-1])
        return at

    strip_offsets = [place(s) for s in strips]
    tags: list[tuple[int, int, list | bytes]] = [
        (256, _LONG, [w]), (257, _LONG, [h]),
        (258, _SHORT, [16] * c), (259, _SHORT, [8]),
        (262, _SHORT, [{"gray": 1, "rgb": 2, "cmyk": 5}[photometric]]),
        (273, _LONG, strip_offsets), (277, _SHORT, [c]),
        (278, _LONG, [rows_per_strip]), (279, _LONG, [len(s) for s in strips]),
        (284, _SHORT, [1]), (317, _SHORT, [2]),
    ]
    if orientation:
        tags.append((274, _SHORT, [int(orientation)]))
    if dpi:
        tags += [(282, _RATIONAL, [_rational(dpi[0])]),
                 (283, _RATIONAL, [_rational(dpi[1])]), (296, _SHORT, [2])]
    if artist:
        tags.append((315, _ASCII, artist.encode("utf-8") + b"\0"))
    if photometric == "cmyk":
        tags.append((332, _SHORT, [1]))
    if extra > 0:
        tags.append((338, _SHORT, [2] * extra))          # unassociated alpha
    if xmp:
        tags.append((700, _BYTE, xmp.encode("utf-8")))
    if copyright_notice:
        tags.append((33432, _ASCII, copyright_notice.encode("utf-8") + b"\0"))
    if icc:
        tags.append((34675, _UNDEF, icc))
    tags.sort()

    entries = []
    for tag, typ, value in tags:
        if isinstance(value, bytes):
            count, payload = len(value), value
        elif typ == _RATIONAL:
            count = len(value)
            payload = b"".join(struct.pack("<II", *r) for r in value)
        else:
            count = len(value)
            payload = b"".join(struct.pack("<H" if typ == _SHORT else "<I", v)
                               for v in value)
        if len(payload) <= 4:
            field = payload.ljust(4, b"\0")
        else:
            field = struct.pack("<I", place(payload))
        entries.append(struct.pack("<HHI", tag, typ, count) + field)

    ifd_at = offset
    ifd = struct.pack("<H", len(entries)) + b"".join(entries) + struct.pack("<I", 0)
    path.write_bytes(b"II*\0" + struct.pack("<I", ifd_at) + b"".join(blobs) + ifd)


def _rational(v: float) -> tuple[int, int]:
    return int(round(float(v) * 1000)), 1000
