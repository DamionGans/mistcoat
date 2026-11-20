"""Ghost-contour overlay - mid-level structure confusion.

The passes above attack texture, compression and style. None of them attack
*shape*. Vision models build their idea of what an image depicts from mid-level
structure: contours, closed regions, junctions, figure/ground. This pass
overlays a faint field of synthetic contours at object scale, so the mid-level
layers of a feature extractor see edges and regions that are not there.

Honest limits: without a model in the loop this is untargeted. It makes the
image harder to parse; it does not steer a model toward a specific wrong label.
That requires the ML engine, which optimises against a real text-image model.
It is also the only pass here that a careful viewer can see, so it ships off by
default.
"""
from __future__ import annotations

import numpy as np

from .. import imaging
from .base import Pass, PassContext


class GhostContours(Pass):
    name = "decoy"
    label = "Ghost contours"
    blurb = ("Overlays faint synthetic edges and regions at object scale, so a "
             "model's shape layers see structure that is not in the artwork. The "
             "only pass here a careful viewer may notice - off by default.")
    targets = "shape and object recognition; automatic captioning and tagging"
    visibility = "visible"
    default_on = False
    salt = 71

    def apply(self, ctx: PassContext, rgb: np.ndarray, strength: float) -> np.ndarray:
        if strength <= 0:
            return rgb
        rng = ctx.rng(self.salt)
        h, w = rgb.shape[:2]

        # Scale the contour field to the image, so it lands at object scale
        # rather than being tied to a pixel count.
        span = max(h, w)
        field = np.zeros((h, w), dtype=np.float32)
        for sigma_frac, weight, freq in ((0.045, 1.0, 7.0), (0.018, 0.55, 15.0)):
            sigma = max(1.5, span * sigma_frac)
            n = imaging.blur(rng.standard_normal((h, w)).astype(np.float32), sigma)
            s = float(n.std())
            if s > 1e-8:
                n /= s
            # sin() of smooth noise gives closed, organic level-set contours.
            bands = np.sin(n * freq).astype(np.float32)
            # Take the edges of those bands, not the bands themselves.
            edges = bands - imaging.blur(bands, max(1.5, sigma * 0.08))
            field += edges * weight

        s = float(field.std())
        if s > 1e-8:
            field /= s

        # Keep the overlay out of the artwork's own strong edges, where it would
        # read as a halo, and out of flat areas, where it would read as mottling.
        act = ctx.activity
        gate = np.clip(act * 2.2, 0.0, 1.0) * (1.0 - np.clip((act - 0.75) / 0.25, 0.0, 1.0))
        gate = 0.25 + 0.75 * gate

        lum = imaging.luminance(rgb)
        head = np.clip(np.minimum(lum, 1.0 - lum) / 0.15, 0.0, 1.0)

        amp = 0.028 * strength
        delta = (field * gate * head * amp)[:, :, None]
        return np.clip(rgb + delta, 0.0, 1.0)
