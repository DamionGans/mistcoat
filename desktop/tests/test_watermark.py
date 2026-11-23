"""The visible watermark: off by default, drawn where the layout says, the
same at preview and full size, and kept through protection and saving."""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from mistcoat import cli
from mistcoat.core import imaging, pipeline, watermark
from mistcoat.core.presets import PRESETS_BY_KEY, Settings


def art(h=400, w=600, seed=3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w] / max(h, w)
    base = np.stack([0.2 + 0.5 * xx, 0.4 + 0.3 * yy, 0.6 - 0.3 * xx], axis=2)
    return np.clip(base + rng.normal(0, 0.03, (h, w, 3)), 0, 1).astype(np.float32)


def mark(rgb, alpha=None, **kw):
    kw.setdefault("text", "SAMPLE")
    return watermark.apply(rgb, alpha, seed=7, **kw)


def test_off_by_default():
    s = Settings()
    assert not s.watermark and s.watermark_text == "SAMPLE"
    assert (s.watermark_layout, s.watermark_resistance) == ("tiled", "strong")
    assert s.watermark_opacity == pytest.approx(0.5)
    img = art()
    out, _ = pipeline.apply_watermark(img, None, s)
    assert out is img


@pytest.mark.parametrize("layout", watermark.LAYOUTS)
@pytest.mark.parametrize("resistance", watermark.RESISTANCE)
def test_every_combination_draws_a_visible_mark(layout, resistance):
    img = art()
    out, alpha = mark(img, layout=layout, resistance=resistance)
    assert alpha is None and out.shape == img.shape and out.dtype == np.float32
    changed = np.abs(out - img).max(axis=2) > 0.08
    assert changed.mean() > 0.001, "something readable was drawn"
    if layout == "tiled":
        # Every part of the picture carries the mark.
        for ys in (slice(0, 200), slice(200, 400)):
            for xs in (slice(0, 300), slice(300, 600)):
                assert changed[ys, xs].mean() > 0.01
    if layout == "corner":
        assert changed[:300, :].sum() == 0 and changed[:, :350].sum() == 0


def test_resistance_varies_the_marks():
    img = np.full((400, 600, 3), 0.5, np.float32)
    plain, _ = mark(img, resistance="plain")
    strong, _ = mark(img, resistance="strong")
    # Strong moves, turns and textures each mark, so it is not the plain grid.
    assert np.abs(plain - strong).max(axis=2).mean() > 0.01
    # Plain marks are all equally strong; strong ones vary in opacity.
    peaks = lambda a: np.array([a[y:y + 100, x:x + 100, 0].max()       # noqa: E731
                                for y in range(0, 400, 100) for x in range(0, 600, 100)])
    assert peaks(plain).std() < peaks(strong).std()
    maximum, _ = mark(art(), resistance="maximum")
    assert np.abs(maximum - art()).mean() > np.abs(mark(art(), resistance="strong")[0] - art()).mean()


def test_empty_text_or_zero_opacity_changes_nothing():
    img = art()
    assert mark(img, text="   ")[0] is img
    assert mark(img, opacity=0)[0] is img


def test_preview_matches_full_size():
    """Sizes are relative and the layout is seeded, so the downscaled preview
    shows the marks in the same places as the saved file."""
    big = art(800, 1200)
    small = imaging.resize_longest(big, 600)
    full, _ = mark(big, resistance="strong")
    prev, _ = mark(small, resistance="strong")
    d_full = imaging.resize_longest(np.abs(full - big).mean(axis=2, keepdims=True)
                                    .repeat(3, 2), 600)[..., 0]
    d_prev = np.abs(prev - small).mean(axis=2)
    assert np.corrcoef(d_full.ravel(), d_prev.ravel())[0, 1] > 0.7


def test_transparent_areas_show_the_mark():
    img = art(300, 400)
    alpha = np.zeros((300, 400), np.float32)
    alpha[100:200, 100:300] = 1.0
    out, new_alpha = mark(img, alpha)
    assert new_alpha is not None and new_alpha.max() <= 1.0
    assert new_alpha[:100].max() > 0.2                  # the mark is visible there
    np.testing.assert_allclose(new_alpha[100:200, 100:300], 1.0)


def test_protect_file_keeps_the_watermark(tmp_path):
    src = tmp_path / "art.png"
    Image.fromarray((art() * 255).astype(np.uint8)).save(src)
    base = PRESETS_BY_KEY["balanced"].to_settings().copy(harden=False)
    plain = pipeline.protect_file(src, None, base, measure=False,
                                  dest=tmp_path / "plain.png").saved_to
    s = base.copy(watermark=True, watermark_text="Art sample")
    res = pipeline.protect_file(src, None, s, measure=True, dest=tmp_path / "wm.png")
    a = np.asarray(Image.open(plain), np.float32) / 255
    b = np.asarray(Image.open(res.saved_to), np.float32) / 255
    assert (np.abs(a - b).max(axis=2) > 0.1).mean() > 0.01
    # The quality figures describe the protection only, not the watermark.
    assert res.metrics.psnr > 30


def test_cli_options():
    s = cli.settings_from({"watermark": True})
    assert s.watermark and s.watermark_text == "SAMPLE"
    s = cli.settings_from({"watermark": "© A. Painter", "watermark_layout": "corner",
                           "watermark_resistance": "maximum", "watermark_opacity": 80})
    assert (s.watermark_text, s.watermark_layout, s.watermark_resistance) == \
        ("© A. Painter", "corner", "maximum")
    assert s.watermark_opacity == pytest.approx(0.8)
    assert not cli.settings_from({}).watermark
    with pytest.raises(cli.UsageError):
        cli.settings_from({"watermark": True, "watermark_layout": "diagonal"})
