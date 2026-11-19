"""Sub-pixel warping - style and brushstroke disruption.

Style is, to a model, largely a set of local statistics: stroke direction,
curvature, edge sharpness, how paint sits against its neighbour. A smooth,
low-amplitude displacement field (well under a pixel in places, a couple of
pixels at most) leaves composition and colour untouched while scrambling those
statistics. This is the closest the no-ML engine gets to what Glaze does to
style mimicry.

Because it is a *geometric* change rather than additive noise, it cannot be
removed by denoising, and it survives rescaling.
"""
from __future__ import annotations

import numpy as np

from .. import imaging
from .base import Pass, PassContext


def _field(shape: tuple[int, int], rng: np.random.Generator, sigma: float) -> np.ndarray:
    n = rng.standard_normal(shape).astype(np.float32)
    f = imaging.blur(n, sigma)
    s = float(f.std())
    return f / s if s > 1e-8 else f


class TextureWarp(Pass):
    name = "texture_warp"
    label = "Stroke disruption"
    blurb = ("A smooth sub-pixel displacement field. Scrambles the local stroke "
             "and edge statistics a model reads as your style, while leaving "
             "composition, colour and line untouched. Cannot be denoised away.")
    targets = "style mimicry; LoRA and fine-tune training on your hand"
    visibility = "subtle"
    geometric = True
    salt = 37

    # (field scale in px, relative weight) - two octaves so the disruption is
    # not a single smooth drift that a model could learn to ignore.
    OCTAVES = ((3.0, 1.0), (9.0, 0.6))

    def apply(self, ctx: PassContext, rgb: np.ndarray, strength: float) -> np.ndarray:
        if strength <= 0:
            return rgb
        rng = ctx.rng(self.salt)
        h, w = rgb.shape[:2]

        # Max displacement at full strength, in pixels. Sub-pixel by design:
        # anything approaching a whole pixel tears hard edges and linework, and
        # is immediately visible. Scales slightly with image size so a 6000px
        # scan is disturbed as much as a 1000px export.
        base = 0.34 + 0.22 * min(max(h, w) / 3000.0, 1.5)
        amp = base * strength

        dx = np.zeros((h, w), dtype=np.float32)
        dy = np.zeros((h, w), dtype=np.float32)
        norm = sum(weight for _, weight in self.OCTAVES)
        for sigma, weight in self.OCTAVES:
            dx += _field((h, w), rng, sigma) * (weight / norm)
            dy += _field((h, w), rng, sigma) * (weight / norm)

        # Aim at *texture*, not at edges. Warping a flat colour field achieves
        # nothing, and warping a hard edge (linework, a crisp silhouette) is the
        # one thing a viewer notices instantly - so the gate peaks at mid
        # activity and falls away again at the top.
        act = ctx.activity
        gate = act * (1.0 - 0.85 * np.clip((act - 0.55) / 0.45, 0.0, 1.0))
        gate = 0.15 + 1.35 * gate
        dx *= amp * gate
        dy *= amp * gate

        return imaging.warp(rgb, dx, dy)
