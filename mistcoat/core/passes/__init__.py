"""Registry of available perturbation passes, in application order."""
from __future__ import annotations

from .base import Pass, PassContext
from .chroma import ChromaPerturb
from .dct_poison import DctPoison
from .decoy import GhostContours
from .hf_noise import HighFrequencyNoise
from .texture import TextureWarp

# Order matters: geometric warping first (so later additive passes are not
# resampled and softened), then shape, then additive signal passes.
ALL_PASSES: tuple[Pass, ...] = (
    TextureWarp(),
    GhostContours(),
    DctPoison(),
    ChromaPerturb(),
    HighFrequencyNoise(),
)

PASSES_BY_NAME = {p.name: p for p in ALL_PASSES}

__all__ = ["Pass", "PassContext", "ALL_PASSES", "PASSES_BY_NAME"]
