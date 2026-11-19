"""Chroma-channel perturbation.

Human vision has roughly a third the spatial acuity in colour that it has in
brightness - which is why JPEG subsamples chroma and nobody notices. Vision
models get the full-resolution RGB tensor and have no such blind spot. That
asymmetry is free perturbation budget: a change in Cb/Cr can be several times
larger than the same change in luma before anyone sees it.
"""
from __future__ import annotations

import numpy as np

from .. import imaging
from .base import Pass, PassContext
from .hf_noise import band_noise


class ChromaPerturb(Pass):
    name = "chroma"
    label = "Colour-channel perturbation"
    blurb = ("Exploits the eye's low resolution for colour. Pushes a much larger "
             "perturbation into the chroma channels than luma would tolerate - "
             "nearly free to a human, expensive to a model.")
    targets = "colour and palette learning; feature extractors reading raw RGB"
    visibility = "invisible"
    salt = 53

    def apply(self, ctx: PassContext, rgb: np.ndarray, strength: float) -> np.ndarray:
        if strength <= 0:
            return rgb
        rng = ctx.rng(self.salt)
        h, w = rgb.shape[:2]
        ycc = imaging.rgb_to_ycbcr(rgb)

        # Mid-scale, not pixel-scale: chroma subsampling would eat pixel noise.
        amp = 0.020 * strength
        lum = ycc[:, :, 0]
        # Saturated areas hide chroma shifts; near-neutral greys show them, so
        # taper where chroma magnitude is low.
        sat = np.sqrt(ycc[:, :, 1] ** 2 + ycc[:, :, 2] ** 2)
        gate = 0.35 + 0.65 * np.clip(sat / 0.25, 0.0, 1.0)
        # Keep away from clipping in either direction.
        head = np.clip(np.minimum(lum, 1.0 - lum) / 0.1, 0.0, 1.0)
        gate = gate * (0.4 + 0.6 * head)

        # The band starts at ~3px. Almost every JPEG and WebP on the web stores
        # chroma at half resolution (4:2:0), which wipes out anything finer than
        # about 2px - measured, a 1.6px band lost three quarters of its energy
        # on the first re-encode. Coarser chroma is still well below what the
        # eye resolves in colour, and it survives.
        for c in (1, 2):
            field = band_noise((h, w), rng, 3.2, 10.0)
            ycc[:, :, c] = ycc[:, :, c] + field * amp * gate

        return np.clip(imaging.ycbcr_to_rgb(ycc), 0.0, 1.0)
