"""Tests for the protection engine.

The properties that matter to an artist are the ones worth asserting: the
output is deterministic, the strength slider is monotonic, the fidelity ceiling
is actually enforced, alpha and colour profiles survive, and nothing silently
returns the original image.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from mistcoat.core import imaging, output, pipeline, quality
from mistcoat.core.imaging import LoadedImage
from mistcoat.core.passes import ALL_PASSES
from mistcoat.core.presets import PRESETS, PRESETS_BY_KEY, Settings


def make_art(h: int = 200, w: int = 260, seed: int = 3) -> np.ndarray:
    """Something with flat areas, gradient, texture and a hard edge."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 3), np.float32)
    img[:, :, 0] = 0.3 + 0.4 * (xx / w)
    img[:, :, 1] = 0.5
    img[:, :, 2] = 0.7 - 0.3 * (yy / h)
    img += imaging.blur(rng.standard_normal((h, w)).astype(np.float32), 3.0)[:, :, None] * 0.06
    img[20:70, 20:90] = np.array([0.9, 0.2, 0.2], np.float32)   # flat saturated
    img[120:, :] *= 0.25                                        # dark passage
    return np.clip(img, 0, 1).astype(np.float32)


def as_loaded(rgb: np.ndarray, alpha: np.ndarray | None = None) -> LoadedImage:
    return LoadedImage(rgb, alpha, None, "RGB", "PNG", None)


@pytest.fixture
def art() -> np.ndarray:
    return make_art()


@pytest.fixture
def settings() -> Settings:
    return PRESETS_BY_KEY["balanced"].to_settings().copy(harden=False)


# --------------------------------------------------------------- behaviour

def test_output_differs_from_input(art, settings):
    result = pipeline.protect(as_loaded(art), settings, measure=False)
    assert not np.array_equal(result.rgb, art), "protection was a no-op"
    assert np.abs(result.rgb - art).mean() > 0.0005


def test_deterministic_for_same_seed(art, settings):
    a = pipeline.protect(as_loaded(art), settings, measure=False).rgb
    b = pipeline.protect(as_loaded(art), settings, measure=False).rgb
    np.testing.assert_array_equal(a, b)


def test_seed_changes_the_perturbation(art, settings):
    a = pipeline.protect(as_loaded(art), settings, measure=False).rgb
    b = pipeline.protect(as_loaded(art), settings.copy(seed=settings.seed + 1),
                         measure=False).rgb
    assert not np.array_equal(a, b)


def test_no_passes_means_no_change(art, settings):
    off = settings.copy(enabled={p.name: False for p in ALL_PASSES}, harden=False)
    result = pipeline.protect(as_loaded(art), off, measure=False)
    np.testing.assert_array_equal(result.rgb, art)
    assert "No passes" in result.note


def test_output_stays_in_range(art, settings):
    rgb = pipeline.protect(as_loaded(art), settings.copy(strength=1.0),
                           measure=False).rgb
    assert np.isfinite(rgb).all()
    assert rgb.min() >= 0.0 and rgb.max() <= 1.0


def test_strength_is_monotonic(art, settings):
    deltas = []
    for s in (0.1, 0.35, 0.6, 0.85, 1.0):
        rgb = pipeline.protect(as_loaded(art), settings.copy(strength=s),
                               measure=False).rgb
        deltas.append(float(np.abs(rgb - art).mean()))
    assert deltas == sorted(deltas), f"not monotonic: {deltas}"
    assert deltas[-1] > deltas[0] * 2


def test_fidelity_ceiling_is_enforced(art, settings):
    """The whole promise of the slider: a low setting cannot blow the budget."""
    for strength in (0.1, 0.5, 1.0):
        s = settings.copy(strength=strength,
                          enabled={p.name: True for p in ALL_PASSES})
        rgb = pipeline.protect(as_loaded(art), s, measure=False).rgb
        peak = float(np.abs(rgb - art).max())
        allowed = 0.012 + 0.030 * strength
        assert peak <= allowed + 1e-5, (
            f"strength {strength}: peak {peak * 255:.1f}/255 exceeds "
            f"ceiling {allowed * 255:.1f}/255")


def test_presets_order_by_visible_change(art):
    psnrs = []
    for preset in PRESETS:
        s = preset.to_settings().copy(harden=False)
        rgb = pipeline.protect(as_loaded(art), s, measure=False).rgb
        psnrs.append(quality.psnr(art, rgb))
    # Stronger preset => lower PSNR (more change).
    assert psnrs == sorted(psnrs, reverse=True), f"presets out of order: {psnrs}"


def test_each_pass_does_something(art, settings):
    for p in ALL_PASSES:
        only = settings.copy(
            enabled={q.name: q.name == p.name for q in ALL_PASSES},
            strength=1.0)
        rgb = pipeline.protect(as_loaded(art), only, measure=False).rgb
        assert not np.array_equal(rgb, art), f"{p.name} did nothing"


def test_hardening_improves_survival(art, settings):
    soft = pipeline.protect(as_loaded(art), settings.copy(harden=False))
    hard = pipeline.protect(as_loaded(art), settings.copy(harden=True))
    assert hard.metrics.survival >= soft.metrics.survival - 0.02
    # And it must not break the fidelity ceiling.
    assert float(np.abs(hard.rgb - art).max()) <= 0.012 + 0.030 * settings.strength + 1e-5


def test_alpha_is_preserved_exactly(art, settings):
    alpha = np.linspace(0, 1, art.shape[0] * art.shape[1],
                        dtype=np.float32).reshape(art.shape[:2])
    img = as_loaded(art, alpha)
    result = pipeline.protect(img, settings, measure=False)
    pil = imaging.to_pil(img, result.rgb)
    assert pil.mode == "RGBA"
    back = np.asarray(pil)[:, :, 3].astype(np.float32) / 255.0
    np.testing.assert_allclose(back, alpha, atol=1.5 / 255)


def test_tiny_image_does_not_crash(settings):
    tiny = make_art(5, 7)
    rgb = pipeline.protect(as_loaded(tiny), settings.copy(strength=1.0),
                           measure=False).rgb
    assert rgb.shape == tiny.shape
    assert np.isfinite(rgb).all()


def test_non_multiple_of_eight_dimensions(settings):
    """The DCT pass pads to 8x8 blocks; the result must still be the right size."""
    odd = make_art(103, 61)
    rgb = pipeline.protect(as_loaded(odd), settings, measure=False).rgb
    assert rgb.shape == odd.shape


# ------------------------------------------------------------------ signal

def test_dct_roundtrip_is_lossless():
    a = np.random.default_rng(0).random((37, 41)).astype(np.float32)
    blocks, shape = imaging.blocks_from(a)
    back = imaging.blocks_to(imaging.idct2(imaging.dct2(blocks)), shape)
    assert np.abs(back - a).max() < 1e-4


def test_ycbcr_roundtrip():
    a = np.random.default_rng(1).random((9, 11, 3)).astype(np.float32)
    back = imaging.ycbcr_to_rgb(imaging.rgb_to_ycbcr(a))
    assert np.abs(back - a).max() < 1e-5


def test_blur_preserves_mean():
    a = np.random.default_rng(2).random((40, 40)).astype(np.float32)
    assert abs(float(imaging.blur(a, 3.0).mean()) - float(a.mean())) < 0.01


def test_survival_metric_bounds(art, settings):
    result = pipeline.protect(as_loaded(art), settings)
    assert 0.0 <= result.metrics.survival <= 1.0
    assert 0.0 <= result.metrics.ssim <= 1.0


# ------------------------------------------------------------------ output

@pytest.mark.parametrize("fmt,suffix", [("PNG", ".png"), ("JPEG", ".jpg"),
                                        ("WEBP", ".webp")])
def test_saves_with_optout_metadata(tmp_path, art, settings, fmt, suffix):
    img = as_loaded(art)
    opts = output.MetadataOptions(write_optout=True, credit="A. Painter",
                                  copyright_notice="(c) 2026 A. Painter")
    dest = tmp_path / f"piece{suffix}"
    output.save_image(img, art, dest, fmt, opts)
    assert dest.exists() and dest.stat().st_size > 0

    with Image.open(dest) as reopened:
        assert reopened.size == (art.shape[1], art.shape[0])
        raw = dest.read_bytes()
    # The opt-out declaration must actually be in the file.
    assert b"DMI-PROHIBITED-AIMLTRAINING" in raw


def test_protection_marker_is_off_by_default(art):
    """Announcing that an image is protected helps anyone filtering them out."""
    opts = output.MetadataOptions(write_optout=True)
    assert "mistcoat:Protected" not in output.build_xmp(opts)
    opts.declare_protection = True
    assert "mistcoat:Protected" in output.build_xmp(opts)


def test_target_path_adds_suffix(tmp_path):
    src = tmp_path / "Night Study.png"
    dest = output.target_path(src, tmp_path / "out", "_protected", "source")
    assert dest.name == "Night Study_protected.png"


def test_protect_file_end_to_end(tmp_path, art, settings):
    src = tmp_path / "piece.png"
    Image.fromarray((art * 255).astype(np.uint8)).save(src)
    result = pipeline.protect_file(src, tmp_path / "out", settings)
    assert result.saved_to.exists()
    with Image.open(result.saved_to) as im:
        saved = np.asarray(im.convert("RGB")).astype(np.float32) / 255.0
    assert not np.allclose(saved, art, atol=1 / 255)
    assert quality.psnr(art, saved) > 28


def test_icc_profile_is_carried_over(tmp_path, art, settings):
    profile = b"fake-icc-profile-bytes"
    img = LoadedImage(art, None, profile, "RGB", "PNG", None)
    dest = tmp_path / "p.png"
    output.save_image(img, art, dest, "PNG", output.MetadataOptions())
    with Image.open(dest) as im:
        assert im.info.get("icc_profile") == profile


def test_warp_is_not_counted_as_surviving_signal(art, settings):
    """Regression: the limiter's partial undo of the warp used to be scored as
    compression-proof perturbation and pinned durability at 100%."""
    warp_only = settings.copy(
        enabled={p.name: p.name == "texture_warp" for p in ALL_PASSES},
        strength=1.0, harden=True)
    result = pipeline.protect(as_loaded(art), warp_only)
    assert result.metrics.survival < 0.05


def test_durability_not_saturated(settings):
    """A real reading moves with the settings; a pinned 100% is a measuring bug."""
    art = make_art(320, 420)
    readings = {round(pipeline.protect(as_loaded(art), settings.copy(strength=s))
                      .metrics.survival, 3) for s in (0.3, 0.6, 1.0)}
    assert len(readings) > 1
    assert max(readings) < 0.999


# ------------------------------------------------------------ acceleration

def _reference_blur(a, sigma):
    """The original single-threaded, interleaved implementation."""
    radius = max(1, int(round(sigma * 0.886)))
    out = a.astype(np.float32)
    for _ in range(3):
        out = imaging._box_blur_1d(out, radius, 0)
        out = imaging._box_blur_1d(out, radius, 1)
    return out


@pytest.mark.parametrize("shape", [(300, 400), (301, 517, 3), (7, 900)])
@pytest.mark.parametrize("sigma", [1.5, 9.0, 60.0])
def test_cpu_blur_matches_reference(monkeypatch, shape, sigma):
    from mistcoat.core import accel
    monkeypatch.setattr(accel, "_enabled", False)
    a = np.random.default_rng(1).standard_normal(shape).astype(np.float32)
    np.testing.assert_allclose(imaging.blur(a, sigma), _reference_blur(a, sigma), atol=2e-6)


def test_gpu_blur_matches_cpu_when_available(monkeypatch):
    from mistcoat.core import accel
    if not accel.gpu_active():
        pytest.skip(accel.status()["gpu_reason"] or "GPU switched off")
    a = np.random.default_rng(2).standard_normal((600, 800, 3)).astype(np.float32)
    for sigma in (1.5, 9.0, 60.0):
        gpu = imaging.blur(a, sigma)
        monkeypatch.setattr(accel, "_enabled", False)
        cpu = imaging.blur(a, sigma)
        monkeypatch.setattr(accel, "_enabled", True)
        np.testing.assert_allclose(gpu, cpu, atol=2e-6)


def test_gpu_off_switch(monkeypatch):
    from mistcoat.core import accel
    monkeypatch.setattr(accel, "_enabled", False)
    assert not accel.gpu_active()
    assert accel.status()["using"].startswith("CPU")
