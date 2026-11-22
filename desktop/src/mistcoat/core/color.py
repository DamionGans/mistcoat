"""Colour profiles and colour spaces.

Mistcoat changes pixel values by a few levels and nothing else, so the safest
way to respect an image's colour is to never convert it: RGB and greyscale
images are processed in their own encoding (sRGB, Adobe RGB, Display P3,
ProPhoto, a scanner or camera profile...) and saved with the same profile.
CMYK and Lab images are the exception, because the protection passes work in
RGB: they are shown and processed through a wide RGB working space and only
the *change* is mapped back, so the original separation (black generation,
ink limits) is kept. See ``imaging.load_image`` and ``output.save_image``.

This module also answers "what does this look like on this screen": the
preview converts from the image's profile to the monitor's profile, because
Qt draws pixel values as they are, without colour management.
"""
from __future__ import annotations

import io
import logging
import struct
import sys
from functools import lru_cache

import numpy as np
from PIL import Image, ImageCms

log = logging.getLogger("mistcoat")

D50 = np.array([0.9642, 1.0, 0.8249])
D65_XY = (0.3127, 0.3290)

_BRADFORD = np.array([[0.8951, 0.2664, -0.1614],
                      [-0.7502, 1.7135, 0.0367],
                      [0.0389, -0.0685, 1.0296]])


# ------------------------------------------------------------ building ICC

def _xy_to_xyz(x: float, y: float) -> np.ndarray:
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def _s15f16(v: float) -> bytes:
    return struct.pack(">i", int(round(v * 65536.0)))


def _xyz_tag(xyz) -> bytes:
    return b"XYZ \0\0\0\0" + b"".join(_s15f16(v) for v in xyz)


def _desc_tag(text: str) -> bytes:
    a = text.encode("ascii", "replace") + b"\0"
    return (b"desc\0\0\0\0" + struct.pack(">I", len(a)) + a
            + b"\0" * 8                       # Unicode: language, count 0
            + b"\0\0\0" + b"\0" * 67)         # ScriptCode: code, count, filler


def _pad4(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 4)


def rgb_profile(name: str, white_xy, primaries_xy, gamma: float) -> bytes:
    """A small ICC v2 matrix/TRC display profile, the same kind Photoshop
    ships for Adobe RGB. ``gamma`` is the encoding exponent (2.2, not 1/2.2)."""
    w = _xy_to_xyz(*white_xy)
    prim = np.column_stack([_xy_to_xyz(*p) for p in primaries_xy])
    to_xyz = prim * np.linalg.solve(prim, w)          # columns scaled to hit white
    cone_src, cone_d50 = _BRADFORD @ w, _BRADFORD @ D50
    adapt = np.linalg.inv(_BRADFORD) @ np.diag(cone_d50 / cone_src) @ _BRADFORD
    colorants = adapt @ to_xyz                        # ICC wants them adapted to D50
    curve = b"curv\0\0\0\0" + struct.pack(">IH", 1, int(round(gamma * 256))) + b"\0\0"
    tags = [
        (b"desc", _desc_tag(name)),
        (b"cprt", b"text\0\0\0\0" + b"No copyright, use freely\0"),
        (b"wtpt", _xyz_tag(w)),
        (b"rXYZ", _xyz_tag(colorants[:, 0])),
        (b"gXYZ", _xyz_tag(colorants[:, 1])),
        (b"bXYZ", _xyz_tag(colorants[:, 2])),
        (b"rTRC", curve), (b"gTRC", curve), (b"bTRC", curve),
    ]
    offset = 128 + 4 + 12 * len(tags)
    table, data, seen = [], b"", {}
    for sig, body in tags:
        if body in seen:                              # the three curves share one copy
            table.append((sig, *seen[body]))
            continue
        body = _pad4(body)
        seen[body] = (offset + len(data), len(body))
        table.append((sig, offset + len(data), len(body)))
        data += body
    head_tail = (struct.pack(">I", len(table))
                 + b"".join(s + struct.pack(">II", o, n) for s, o, n in table) + data)
    size = 128 + len(head_tail)
    header = (struct.pack(">I", size) + b"\0\0\0\0" + struct.pack(">I", 0x02100000)
              + b"mntrRGB XYZ " + b"\0" * 12 + b"acsp" + b"\0" * 24
              + struct.pack(">I", 0)                  # rendering intent: perceptual
              + b"".join(_s15f16(v) for v in D50) + b"\0" * 48)
    assert len(header) == 128
    return header + head_tail


ADOBE_RGB = rgb_profile("Adobe RGB (1998) compatible", D65_XY,
                        ((0.64, 0.33), (0.21, 0.71), (0.15, 0.06)), 563 / 256)
# The space CMYK and Lab images are shown and processed in: wide enough to
# hold any printing gamut without clipping.
WORKING_RGB = ADOBE_RGB


@lru_cache(maxsize=1)
def srgb_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


# ------------------------------------------------------------ inspecting

def profile_space(icc: bytes | None) -> str | None:
    """The profile's data colour space: "RGB", "GRAY", "CMYK", "LAB"... or None."""
    if not icc or len(icc) < 20:
        return None
    return icc[16:20].decode("ascii", "replace").strip() or None


@lru_cache(maxsize=64)
def profile_name(icc: bytes | None) -> str:
    if not icc:
        return "sRGB (assumed)"
    try:
        name = ImageCms.getProfileDescription(_profile(icc)).strip()
        return name or "embedded profile"
    except Exception:  # noqa: BLE001
        return "embedded profile (unreadable)"


@lru_cache(maxsize=64)
def _profile(icc: bytes) -> ImageCms.ImageCmsProfile:
    return ImageCms.ImageCmsProfile(io.BytesIO(icc))


def profile_from_png(info: dict) -> bytes | None:
    """PNGs can describe their colour with gAMA + cHRM chunks instead of a
    profile. Pillow cannot write those back, so turn them into a profile."""
    gamma, chrm = info.get("gamma"), info.get("chromaticity")
    if not gamma or not chrm or len(chrm) != 8:
        return None
    wx, wy, rx, ry, gx, gy, bx, by = (float(v) for v in chrm)
    srgb_like = (abs(rx - 0.64) < 0.002 and abs(gx - 0.30) < 0.002
                 and abs(bx - 0.15) < 0.002 and abs(wx - 0.3127) < 0.002)
    if srgb_like and abs(float(gamma) - 0.45455) < 0.002:
        return None                                   # that is just sRGB
    try:
        return rgb_profile("RGB from PNG gamma and chromaticity", (wx, wy),
                           ((rx, ry), (gx, gy), (bx, by)), 1.0 / float(gamma))
    except (ZeroDivisionError, np.linalg.LinAlgError, ValueError):
        return None


def exif_says_adobe_rgb(exif: Image.Exif | None) -> bool:
    """Cameras set to Adobe RGB often embed no profile, only EXIF
    ColorSpace=Uncalibrated plus the interoperability index "R03"."""
    if not exif:
        return False
    try:
        sub = exif.get_ifd(0x8769)                    # Exif IFD
        if sub.get(0xA001) != 0xFFFF:                 # ColorSpace: uncalibrated
            return False
        interop = exif.get_ifd(0xA005)
        return str(interop.get(0x0001, "")).strip().upper() == "R03"
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------ the monitor

def display_profile(window_id: int | None = None, screen_index: int = 0,
                    point: tuple[float, float] | None = None) -> bytes | None:
    """The ICC profile of the monitor showing a window, or None (= sRGB).

    Windows uses the window handle, macOS a point on the screen (global
    coordinates, which Qt and Quartz share), X11 the screen's index."""
    try:
        if sys.platform == "win32":
            return _display_profile_windows(window_id)
        if sys.platform == "darwin":
            return _display_profile_macos(point)
        return _display_profile_x11(screen_index)
    except Exception as exc:  # noqa: BLE001 - never let this break the preview
        log.info("could not read the display profile: %s", exc)
        return None


def _display_profile_windows(window_id):
    from PIL import ImageWin
    handle = ImageWin.HWND(window_id) if window_id else None
    prof = ImageCms.get_display_profile(handle)
    return prof.tobytes() if prof is not None else None


def _display_profile_macos(point):
    import ctypes
    import ctypes.util

    cg = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreGraphics"))
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    cg.CGMainDisplayID.restype = ctypes.c_uint32
    cg.CGDisplayCopyColorSpace.restype = ctypes.c_void_p
    cg.CGDisplayCopyColorSpace.argtypes = [ctypes.c_uint32]
    cg.CGColorSpaceCopyICCData.restype = ctypes.c_void_p
    cg.CGColorSpaceCopyICCData.argtypes = [ctypes.c_void_p]
    cf.CFDataGetLength.restype = ctypes.c_long
    cf.CFDataGetLength.argtypes = [ctypes.c_void_p]
    cf.CFDataGetBytePtr.restype = ctypes.c_void_p
    cf.CFDataGetBytePtr.argtypes = [ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]

    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    display = cg.CGMainDisplayID()
    if point is not None:
        found, count = ctypes.c_uint32(), ctypes.c_uint32()
        cg.CGGetDisplaysWithPoint.argtypes = [CGPoint, ctypes.c_uint32,
                                              ctypes.POINTER(ctypes.c_uint32),
                                              ctypes.POINTER(ctypes.c_uint32)]
        if (cg.CGGetDisplaysWithPoint(CGPoint(*point), 1, ctypes.byref(found),
                                      ctypes.byref(count)) == 0 and count.value):
            display = found.value
    space = cg.CGDisplayCopyColorSpace(display)
    if not space:
        return None
    try:
        data = cg.CGColorSpaceCopyICCData(space)
        if not data:
            return None
        try:
            n = cf.CFDataGetLength(data)
            return ctypes.string_at(cf.CFDataGetBytePtr(data), n)
        finally:
            cf.CFRelease(data)
    finally:
        cf.CFRelease(space)


def _display_profile_x11(screen_index):
    """The XICC convention: colour managers (colord, DisplayCAL...) put the
    monitor profile on the root window as _ICC_PROFILE (_ICC_PROFILE_1...)."""
    import ctypes
    import ctypes.util
    import os

    if not os.environ.get("DISPLAY"):
        return None
    path = ctypes.util.find_library("X11")
    if not path:
        return None
    x = ctypes.cdll.LoadLibrary(path)
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XDefaultRootWindow.restype = ctypes.c_ulong
    x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x.XInternAtom.restype = ctypes.c_ulong
    x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x.XGetWindowProperty.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long,
        ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
        ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_void_p)]
    x.XFree.argtypes = [ctypes.c_void_p]
    x.XCloseDisplay.argtypes = [ctypes.c_void_p]
    dpy = x.XOpenDisplay(None)
    if not dpy:
        return None
    try:
        name = b"_ICC_PROFILE" + (f"_{screen_index}".encode() if screen_index else b"")
        atom = x.XInternAtom(dpy, name, 1)
        if not atom:
            return None
        typ, fmt = ctypes.c_ulong(), ctypes.c_int()
        n, left, data = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p()
        ok = x.XGetWindowProperty(dpy, x.XDefaultRootWindow(dpy), atom, 0, 1 << 24, 0, 0,
                                  ctypes.byref(typ), ctypes.byref(fmt), ctypes.byref(n),
                                  ctypes.byref(left), ctypes.byref(data))
        if ok != 0 or not data.value:
            return None
        try:
            return ctypes.string_at(data.value, n.value) if fmt.value == 8 else None
        finally:
            x.XFree(data)
    finally:
        x.XCloseDisplay(dpy)


# ------------------------------------------------------------ showing pixels

class DisplayTransform:
    """Converts pixel values in the image's colour space to what the monitor
    needs to show the same colours. Built once per image and monitor."""

    def __init__(self, source_icc: bytes | None, display_icc: bytes | None):
        self.source_icc, self.display_icc = source_icc, display_icc
        self._transform = None
        self._gray = profile_space(source_icc) == "GRAY"
        src = source_icc or srgb_bytes()
        dst = display_icc or srgb_bytes()
        if src == dst:
            return                                    # identity: nothing to do
        try:
            self._transform = _build(src, dst, self._gray)
        except Exception as exc:  # noqa: BLE001 - show it unconverted rather than not at all
            log.warning("colour transform for the preview failed: %s", exc)

    @property
    def active(self) -> bool:
        return self._transform is not None

    def to_display(self, rgb: np.ndarray) -> np.ndarray:
        """(H, W, 3) float 0..1 -> (H, W, 3) uint8 for the screen."""
        arr = np.ascontiguousarray((np.clip(rgb, 0, 1) * 255.0 + 0.5).astype(np.uint8))
        if self._transform is None:
            return arr
        if self._gray:
            im = Image.fromarray(np.ascontiguousarray(arr[:, :, 0]), "L")
        else:
            im = Image.fromarray(arr, "RGB")
        out = ImageCms.applyTransform(im, self._transform)
        return np.asarray(out.convert("RGB"))


@lru_cache(maxsize=16)
def _build(src: bytes, dst: bytes, gray: bool):
    return ImageCms.buildTransform(
        _profile(src), _profile(dst), "L" if gray else "RGB", "RGB",
        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
        flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)


# ------------------------------------------------------------ CMYK and Lab

class Converter:
    """Moves a CMYK or Lab image into the RGB working space for processing,
    and carries the protective change back without re-separating the image.

    Going CMYK -> RGB -> CMYK would rebuild the black channel from scratch and
    change rich blacks, ink limits and pure-K text. Instead only the change is
    converted: ``out = original + (cmyk(protected) - cmyk(unprotected))``.
    """

    def __init__(self, mode: str, icc: bytes | None):
        self.mode, self.icc = mode, icc
        self.naive = mode == "CMYK" and profile_space(icc) != "CMYK"
        if not self.naive:
            src = _profile(icc) if icc else ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB"))
            work = _profile(WORKING_RGB)
            intent = ImageCms.Intent.RELATIVE_COLORIMETRIC
            self._fwd = ImageCms.buildTransform(src, work, mode, "RGB", intent,
                                                ImageCms.Flags.BLACKPOINTCOMPENSATION)
            self._back = ImageCms.buildTransform(work, src, "RGB", mode, intent,
                                                 ImageCms.Flags.BLACKPOINTCOMPENSATION)

    @property
    def working_icc(self) -> bytes | None:
        return None if self.naive else WORKING_RGB

    def to_rgb(self, native: np.ndarray) -> np.ndarray:
        """native (H, W, C) float 0..1 -> working RGB float 0..1."""
        if self.naive:
            k = native[:, :, 3:4]
            return ((1.0 - native[:, :, :3]) * (1.0 - k)).astype(np.float32)
        im = Image.fromarray(_u8(native), self.mode)
        return np.asarray(ImageCms.applyTransform(im, self._fwd), np.float32) / 255.0

    def apply_change(self, native: np.ndarray, before: np.ndarray,
                     after: np.ndarray) -> np.ndarray:
        """The original native pixels plus the change from ``before`` to ``after``."""
        if self.naive:
            # Keep black as it was; solve the inks for the new RGB.
            keep = np.maximum(1.0 - native[:, :, 3:4], 1e-6)
            ink = lambda rgb: 1.0 - rgb / keep          # noqa: E731
            delta = ink(after) - ink(before)
            out = native.copy()
            out[:, :, :3] = np.clip(native[:, :, :3] + delta, 0.0, 1.0)
            return out
        a = self._native_of(after)
        b = self._native_of(before)
        delta = (a - b) / 255.0
        # Pillow keeps Lab's a* and b* offset by 128, like LittleCMS, so the
        # change adds directly in every mode.
        return np.clip(native + delta, 0.0, 1.0).astype(np.float32)

    def _native_of(self, rgb: np.ndarray) -> np.ndarray:
        im = Image.fromarray(_u8(rgb), "RGB")
        return np.asarray(ImageCms.applyTransform(im, self._back), np.float32)


def _u8(a: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray((np.clip(a, 0, 1) * 255.0 + 0.5).astype(np.uint8))
