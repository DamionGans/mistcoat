"""Mid-band DCT perturbation.

This is the pass that survives being scraped. Almost every image on the web is
re-encoded as JPEG or WebP at some point, and both quantise the 8x8 DCT. Noise
added in the pixel domain mostly dies there; a perturbation written *directly*
into mid-frequency DCT coefficients, sized against the quantisation step, comes
back out the other side.

Coefficient choice:
  * Skip DC (u=v=0) - that is average block brightness, visible immediately.
  * Skip the far corner - quantised to zero at any realistic quality.
  * Keep 2 <= u+v <= 6, where the step size is small enough to survive and the
    human contrast sensitivity function is already falling off.
"""
from __future__ import annotations

import numpy as np

from .. import imaging
from .base import Pass, PassContext

_U, _V = np.meshgrid(np.arange(8), np.arange(8), indexing="ij")
_RADIAL = _U + _V
MID_BAND = ((_RADIAL >= 2) & (_RADIAL <= 6)).astype(np.float32)
# Weight by how survivable each coefficient is: a coarse quantiser needs a
# bigger nudge, so spend the perturbation budget where the step is modest.
_SURVIVAL = MID_BAND / np.sqrt(imaging.JPEG_Q50_LUMA)
_SURVIVAL /= _SURVIVAL.max()


class DctPoison(Pass):
    name = "dct_poison"
    label = "Compression-resistant pattern"
    blurb = ("Writes the perturbation into the 8x8 DCT coefficients that JPEG and "
             "WebP preserve, sized against the quantisation step. This is what "
             "keeps the protection alive after a site re-compresses the upload.")
    targets = "survival through re-encoding, resizing and screenshotting"
    visibility = "invisible"
    salt = 23

    def apply(self, ctx: PassContext, rgb: np.ndarray, strength: float) -> np.ndarray:
        if strength <= 0:
            return rgb
        rng = ctx.rng(self.salt)

        ycc = imaging.rgb_to_ycbcr(rgb)
        y = ycc[:, :, 0] * 255.0  # DCT maths is conventionally on 0..255

        blocks, shape = imaging.blocks_from(y)
        nby, nbx = blocks.shape[:2]
        coeffs = imaging.dct2(blocks)

        # Per-block texture energy: AC magnitude. Busy blocks tolerate more.
        ac = np.abs(coeffs).sum(axis=(2, 3)) - np.abs(coeffs[:, :, 0, 0])
        ac_norm = ac / max(float(np.percentile(ac, 95)), 1e-6)
        ac_norm = np.clip(ac_norm, 0.0, 1.0)
        block_gain = (0.45 + 0.55 * ac_norm)[:, :, None, None]

        # Pseudo-random sign pattern, fixed per block. A coherent per-block
        # pattern (rather than fresh noise per coefficient) is far harder for a
        # denoiser to average away.
        signs = rng.choice(np.array([-1.0, 1.0], dtype=np.float32), size=(nby, nbx, 8, 8))

        # Step size as a fraction of the q50 divisor. ~20 coefficients per block
        # are perturbed and an orthonormal DCT preserves energy, so the pixel
        # RMS is sqrt(20)/8 times this - which is why the fraction has to stay
        # small to remain invisible. Durability comes from hardening instead.
        amplitude = 0.16 * strength
        delta = signs * _SURVIVAL * imaging.JPEG_Q50_LUMA * amplitude * block_gain

        coeffs = coeffs + delta
        y_new = imaging.blocks_to(imaging.idct2(coeffs), shape) / 255.0

        # Block-edge softening: keep the 8x8 grid from becoming visible as
        # blockiness in smooth passages.
        diff = y_new - ycc[:, :, 0]
        mask = ctx.visibility_mask(floor=0.35)
        diff = diff * mask

        ycc[:, :, 0] = ycc[:, :, 0] + diff
        return np.clip(imaging.ycbcr_to_rgb(ycc), 0.0, 1.0)
