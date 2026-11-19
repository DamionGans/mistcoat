"""Protection settings and the presets exposed in the UI."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .passes import ALL_PASSES


@dataclass
class Settings:
    """Everything that determines the output for one image."""

    # Master strength, 0..1. The slider in the UI.
    strength: float = 0.60
    # Pass name -> enabled.
    enabled: dict[str, bool] = field(
        default_factory=lambda: {p.name: p.default_on for p in ALL_PASSES})
    # Pass name -> relative weight, for fine tuning without touching the master.
    weights: dict[str, float] = field(
        default_factory=lambda: {p.name: 1.0 for p in ALL_PASSES})

    # Re-project the perturbation so it survives a JPEG re-encode. Costs a few
    # seconds per image; the single biggest factor in real-world durability.
    harden: bool = True
    harden_quality: int = 72
    harden_iterations: int = 2

    # Deterministic output. Keeping this fixed per artist is actually useful:
    # a consistent perturbation across a body of work is harder to average out
    # than a fresh one per image.
    seed: int = 20260101

    # Metadata and provenance.
    write_optout_metadata: bool = True
    credit: str = ""
    copyright_notice: str = ""

    # Output.
    output_format: str = "source"   # "source" | "png" | "jpeg" | "webp"
    jpeg_quality: int = 95
    filename_suffix: str = "_protected"

    def pass_strength(self, name: str) -> float:
        if not self.enabled.get(name, False):
            return 0.0
        return max(0.0, min(1.0, self.strength * self.weights.get(name, 1.0)))

    def copy(self, **changes) -> "Settings":
        s = replace(self)
        s.enabled = dict(self.enabled)
        s.weights = dict(self.weights)
        for k, v in changes.items():
            setattr(s, k, v)
        return s


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    blurb: str
    strength: float
    enabled: tuple[str, ...]
    harden: bool = True

    def to_settings(self, base: Settings | None = None) -> Settings:
        base = base or Settings()
        return base.copy(
            strength=self.strength,
            enabled={p.name: p.name in self.enabled for p in ALL_PASSES},
            harden=self.harden,
        )


_SIGNAL = ("texture_warp", "dct_poison", "chroma", "hf_noise")

PRESETS: tuple[Preset, ...] = (
    Preset(
        key="light",
        label="Light",
        blurb="Imperceptible at 100%. Safe for print proofs and client previews. "
              "Modest protection - best treated as a deterrent, not a wall.",
        strength=0.32,
        enabled=("dct_poison", "chroma", "hf_noise"),
    ),
    Preset(
        key="balanced",
        label="Balanced",
        blurb="The default. Invisible in normal viewing, including at full "
              "screen. Disrupts texture, colour and style learning, and survives "
              "being re-compressed by a gallery or social platform.",
        strength=0.60,
        enabled=_SIGNAL,
    ),
    Preset(
        key="strong",
        label="Strong",
        blurb="A faint texture may be visible if someone pixel-peeps a flat area. "
              "Recommended for anything posted publicly where mimicry is the "
              "actual worry.",
        strength=0.85,
        enabled=_SIGNAL,
    ),
    Preset(
        key="maximum",
        label="Maximum",
        blurb="Everything on, including ghost contours. Visibly altered up close. "
              "For work you are willing to degrade to keep out of a dataset.",
        strength=1.0,
        enabled=_SIGNAL + ("decoy",),
    ),
)

PRESETS_BY_KEY = {p.key: p for p in PRESETS}
DEFAULT_PRESET = "balanced"
