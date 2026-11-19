"""Band-limited perceptual noise.

Convolutional and transformer vision models lean heavily on fine texture
statistics. Flooding a narrow frequency band with structured noise, masked by
local image detail so the eye does not pick it up, degrades those statistics
without changing what a person sees.

Energy is placed at a *scale*, not at the pixel grid: pure per-pixel noise is
the first thing a denoiser or a downscale removes.
"""
from __future__ import annotations

import numpy as np

from .. import imaging
from .base import Pass, PassContext


def band_noise(shape: tuple[int, int], rng: np.random.Generator,
               inner: float, outer: float) -> np.ndarray:
    """Noise with energy between two blur scales (a difference-of-gaussians band)."""
    n = rng.standard_normal(shape).astype(np.float32)
    lo = imaging.blur(n, inner)
    hi = imaging.blur(n, outer)
    band = lo - hi
    s = float(band.std())
    return band / s if s > 1e-8 else band


class HighFrequencyNoise(Pass):
    name = "hf_noise"
    label = "Texture noise"
    blurb = ("Structured noise spread across two frequency bands and hidden in "
             "existing detail. Cheap, and it degrades the fine-texture statistics "
             "that image models learn from.")
    targets = "texture and detail learning; fine-grained style capture"
    visibility = "invisible"
    salt = 11

    # Two octaves: one fine, one coarse enough to survive a 50% downscale.
    BANDS = ((0.8, 2.0, 1.0), (2.2, 5.5, 0.7))

    def apply(self, ctx: PassContext, rgb: np.ndarray, strength: float) -> np.ndarray:
        if strength <= 0:
            return rgb
        rng = ctx.rng(self.salt)
        h, w = rgb.shape[:2]
        mask = ctx.visibility_mask(floor=0.3)[:, :, None]

        # Peak amplitude in 0..1 units. 0.010 ~= 2.5/255 at full strength, below
        # the visibility threshold once masked into existing detail.
        amp = 0.010 * strength

        total = np.zeros((h, w), dtype=np.float32)
        norm = sum(wt for _, _, wt in self.BANDS)
        for inner, outer, weight in self.BANDS:
            total += band_noise((h, w), rng, inner, outer) * (weight / norm)

        # Decorrelate the channels slightly: a per-channel perturbation is
        # harder to strip than one that is identical in R, G and B.
        delta = np.empty_like(rgb)
        for c in range(3):
            jitter = band_noise((h, w), rng, 1.0, 3.0) * 0.35
            delta[:, :, c] = total + jitter
        delta *= amp * mask
        return np.clip(rgb + delta, 0.0, 1.0)
