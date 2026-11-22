"""Colour models, bit depths and profiles survive protection unchanged, and
the preview converts to the monitor's profile."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageCms

from mistcoat.core import color, deep, imaging, pipeline
from mistcoat.core.presets import PRESETS_BY_KEY

DISPLAY_P3 = color.rgb_profile("Display P3 test", color.D65_XY,
                               ((0.680, 0.320), (0.265, 0.690), (0.150, 0.060)), 2.2)
SWOP = Path(r"C:\Windows\System32\spool\drivers\color\RSWOP.icm")


@pytest.fixture
def settings():
    return PRESETS_BY_KEY["balanced"].to_settings().copy(harden=False)


def art(h=64, w=80, c=3, seed=5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w] / max(h, w)
    base = np.stack([(0.2 + 0.6 * xx + 0.1 * k * yy) for k in range(c)], axis=2)
    return np.clip(base + rng.normal(0, 0.05, (h, w, c)), 0, 1)


def protect(path: Path, out: Path, settings):
    return pipeline.protect_file(path, None, settings, measure=False, dest=out)


def test_profile_builder_is_valid_and_matches_adobe_rgb():
    prof = ImageCms.ImageCmsProfile(io.BytesIO(color.ADOBE_RGB))
    t = ImageCms.buildTransform(ImageCms.createProfile("sRGB"), prof, "RGB", "RGB")
    red = Image.new("RGB", (1, 1), (255, 0, 0))
    r, g, b = ImageCms.applyTransform(red, t).getpixel((0, 0))
    assert abs(r - 219) <= 2 and g <= 2 and b <= 2      # the known sRGB red in Adobe RGB
    assert color.profile_space(color.ADOBE_RGB) == "RGB"


def test_rgb_jpeg_keeps_its_wide_gamut_profile(tmp_path, settings):
    src = tmp_path / "p3.jpg"
    Image.fromarray((art() * 255).astype(np.uint8)).save(
        src, quality=95, icc_profile=DISPLAY_P3, dpi=(300, 300))
    img = imaging.load_image(src)
    assert img.color_model == "RGB" and img.bits == 8 and img.working_icc == DISPLAY_P3
    out = protect(src, tmp_path / "out.jpg", settings).saved_to
    with Image.open(out) as im:
        assert im.mode == "RGB" and im.info["icc_profile"] == DISPLAY_P3
        assert round(im.info["dpi"][0]) == 300


@pytest.mark.parametrize("kind", ["png", "tif"])
@pytest.mark.parametrize("channels", [3, 4])
def test_16bit_rgb_stays_16bit(tmp_path, settings, kind, channels):
    values = (art(c=channels) * 65535).astype(np.uint16)
    src = tmp_path / f"deep.{kind}"
    if kind == "png":
        deep.write_png16(src, values, icc=color.ADOBE_RGB)
    else:
        deep.write_tiff16(src, values, photometric="rgb", icc=color.ADOBE_RGB)
    img = imaging.load_image(src)
    assert img.bits == 16 and img.color_model == "RGB"
    assert (img.alpha is not None) == (channels == 4)
    np.testing.assert_allclose(img.rgb, values[:, :, :3] / 65535.0, atol=1e-6)
    # Fine gradations are really there, not 8-bit steps.
    assert len(np.unique(values[:, :, 0])) > 300

    out = protect(src, tmp_path / f"out.{kind}", settings).saved_to
    back = imaging.load_image(out)
    assert back.bits == 16 and back.icc_profile == color.ADOBE_RGB
    assert (back.alpha is not None) == (channels == 4)
    if channels == 4:
        np.testing.assert_allclose(back.alpha, img.alpha, atol=1 / 65535)
    # Changed, but only by the protection's small amounts.
    diff = np.abs(back.rgb - img.rgb)
    assert diff.max() > 1 / 255 and diff.max() < 0.06


def test_16bit_rgb_to_jpeg_is_8bit(tmp_path, settings):
    src = tmp_path / "deep.png"
    deep.write_png16(src, (art() * 65535).astype(np.uint16), icc=color.ADOBE_RGB)
    out = protect(src, tmp_path / "out.jpg", settings).saved_to
    with Image.open(out) as im:
        assert im.mode == "RGB" and im.info["icc_profile"] == color.ADOBE_RGB


@pytest.mark.parametrize("bits", [8, 16])
def test_greyscale_stays_greyscale(tmp_path, settings, bits):
    g = art(c=1)[:, :, 0]
    src = tmp_path / "grey.png"
    if bits == 8:
        Image.fromarray((g * 255).astype(np.uint8), "L").save(src)
    else:
        deep.write_png16(src, (g * 65535).astype(np.uint16)[:, :, None])
    img = imaging.load_image(src)
    assert (img.color_model, img.bits) == ("GRAY", bits)
    out = protect(src, tmp_path / "out.png", settings).saved_to
    with Image.open(out) as im:
        assert im.mode == ("L" if bits == 8 else "I;16")


def test_cmyk_keeps_its_black_channel(tmp_path, settings):
    """No profile: the protection changes C, M and Y; K is never rebuilt."""
    cmyk = np.concatenate([art(c=3) * 0.6, np.zeros((64, 80, 1))], axis=2)
    cmyk[:, 40:, 3] = 0.8                                # a heavy black area
    src = tmp_path / "print.tif"
    Image.fromarray((cmyk * 255).astype(np.uint8), "CMYK").save(src, dpi=(600, 600))
    img = imaging.load_image(src)
    assert img.color_model == "CMYK"
    out = protect(src, tmp_path / "out.tif", settings).saved_to
    with Image.open(out) as im:
        assert im.mode == "CMYK" and round(im.info["dpi"][0]) == 600
        res = np.asarray(im).astype(int)
    orig = (cmyk * 255).astype(np.uint8).astype(int)
    np.testing.assert_array_equal(res[:, :, 3], orig[:, :, 3])
    assert np.abs(res[:, :, :3] - orig[:, :, :3]).max() > 0


@pytest.mark.skipif(not SWOP.exists(), reason="needs a CMYK profile (Windows ships RSWOP.icm)")
def test_cmyk_with_profile_roundtrips_the_separation(tmp_path, settings):
    icc = SWOP.read_bytes()
    cmyk = np.concatenate([art(c=3) * 0.5, np.full((64, 80, 1), 0.3)], axis=2)
    src = tmp_path / "swop.jpg"
    Image.fromarray((cmyk * 255).astype(np.uint8), "CMYK").save(src, quality=98, icc_profile=icc)
    img = imaging.load_image(src)
    assert img.working_icc == color.WORKING_RGB
    out = protect(src, tmp_path / "out.jpg", settings).saved_to
    with Image.open(out) as im:
        assert im.mode == "CMYK" and im.info["icc_profile"] == icc
        res = np.asarray(im).astype(int)
    src_px = np.asarray(Image.open(src)).astype(int)
    assert np.abs(res - src_px).mean() < 6                 # same separation, small change


def test_cmyk_to_png_becomes_rgb_with_a_profile(tmp_path, settings):
    src = tmp_path / "print.tif"
    Image.fromarray((art(c=4) * 200).astype(np.uint8), "CMYK").save(src)
    out = protect(src, tmp_path / "out.png", settings).saved_to
    with Image.open(out) as im:
        assert im.mode == "RGB"


def test_camera_adobe_rgb_from_exif(tmp_path, settings):
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0xA001] = 0xFFFF             # ColorSpace: uncalibrated
    exif.get_ifd(0x8769)[0xA005] = {0x0001: "R03"}    # interoperability: Adobe RGB
    src = tmp_path / "camera.jpg"
    Image.fromarray((art() * 255).astype(np.uint8)).save(src, exif=exif.tobytes())
    img = imaging.load_image(src)
    assert img.icc_profile == color.ADOBE_RGB and "EXIF" in img.color_summary
    out = protect(src, tmp_path / "out.jpg", settings).saved_to
    with Image.open(out) as im:
        assert im.info["icc_profile"] == color.ADOBE_RGB


def test_palette_image_is_saved_as_full_colour(tmp_path, settings):
    src = tmp_path / "pal.png"
    Image.fromarray((art() * 255).astype(np.uint8)).convert("P").save(src)
    img = imaging.load_image(src)
    assert img.color_model == "RGB" and "palette" in img.color_summary


def test_display_transform_converts_to_the_monitor():
    srgb_display = color.DisplayTransform(DISPLAY_P3, None)
    assert srgb_display.active
    red = np.zeros((1, 1, 3), np.float32)
    red[..., 0] = 1.0
    shown = srgb_display.to_display(red)[0, 0]
    assert shown[0] == 255 and shown[1] <= 2                # P3 red clips to sRGB red
    mid = np.full((1, 1, 3), 0.5, np.float32)
    assert abs(int(color.DisplayTransform(color.ADOBE_RGB, None).to_display(mid)[0, 0, 0])
               - 128) <= 3                                 # neutrals stay neutral
    assert not color.DisplayTransform(None, None).active    # sRGB on sRGB: untouched
    grey = color.DisplayTransform(None, DISPLAY_P3)
    assert grey.active and grey.to_display(mid).shape == (1, 1, 3)
