"""Pass framework.

A *pass* is one independent perturbation applied to the image. Each pass gets a
strength in 0..1 (already scaled by the preset) and must return an RGB array of
the same shape. Passes are deliberately small and self-contained so the ML
engine can later slot in beside them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .. import imaging


@dataclass
class PassContext:
    """Shared, cached analysis of the image so passes do not recompute it."""

    rgb: np.ndarray
    seed: int = 0
    _activity: np.ndarray | None = field(default=None, repr=False)

    @property
    def activity(self) -> np.ndarray:
        if self._activity is None:
            self._activity = imaging.local_activity(self.rgb, sigma=2.0)
        return self._activity

    def rng(self, salt: int) -> np.random.Generator:
        """Deterministic per-pass generator, so results are reproducible."""
        return np.random.default_rng((self.seed * 1_000_003 + salt) & 0xFFFFFFFF)

    def visibility_mask(self, floor: float = 0.25, gamma: float = 0.75) -> np.ndarray:
        """Where perturbation can hide: textured areas, mid tones.

        Returns (H, W) in 0..1. ``floor`` keeps some perturbation in flat areas
        (otherwise skies and paper backgrounds stay completely unprotected).
        """
        act = self.activity ** gamma
        m = floor + (1.0 - floor) * act
        lum = imaging.luminance(self.rgb)
        # Taper near black and white so we neither clip nor create visible
        # mottle. The floor is deliberately high: a lot of artists work dark,
        # and an aggressive taper leaves the shadows of those pieces almost
        # unprotected. 0.09 is roughly where banding starts to show.
        head = np.clip(np.minimum(lum, 1.0 - lum) / 0.09, 0.0, 1.0)
        return (m * (0.55 + 0.45 * head)).astype(np.float32)


class Pass:
    """Base class. Subclasses set the metadata and implement ``apply``."""

    name: str = ""
    label: str = ""
    blurb: str = ""
    # What this pass is actually trying to break, in plain language.
    targets: str = ""
    # Rough perceptual cost at full strength: "invisible", "subtle", "visible".
    visibility: str = "subtle"
    default_on: bool = True
    # True if the pass changes geometry rather than adding a signal. Geometric
    # change is invariant to re-compression and denoising by construction, so
    # the durability metric must not try to measure it as a residual pattern.
    geometric: bool = False
    salt: int = 0

    def apply(self, ctx: PassContext, rgb: np.ndarray, strength: float) -> np.ndarray:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<{type(self).__name__} {self.name}>"
