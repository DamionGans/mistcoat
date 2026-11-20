"""The protection pipeline: run the passes, harden, measure."""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import accel, imaging, output, quality
from .imaging import LoadedImage
from .passes import ALL_PASSES, PassContext
from .presets import Settings

Progress = Callable[[str, float], None]


@dataclass
class Result:
    rgb: np.ndarray
    metrics: quality.Metrics | None
    applied: list[str] = field(default_factory=list)
    seconds: float = 0.0
    saved_to: Path | None = None
    note: str = ""


def _noop(_stage: str, _frac: float) -> None:
    pass


def delta_ceiling(ctx: PassContext, strength: float) -> np.ndarray:
    """Per-pixel limit on how far any channel may move, in 0..1 units.

    Every pass masks its own output, but they stack, and the hardening step
    deliberately amplifies. One ceiling applied at the end is what actually
    makes the strength slider mean something: it is the promise that "Light"
    stays invisible no matter which passes are on.
    """
    # 0.012 (~3/255) at the bottom of the slider, 0.042 (~11/255) at the top.
    peak = 0.012 + 0.030 * float(np.clip(strength, 0.0, 1.0))
    mask = ctx.visibility_mask(floor=0.25)
    return (peak * (0.35 + 0.65 * mask))[:, :, None].astype(np.float32)


def _limit(original: np.ndarray, current: np.ndarray,
           ceiling: np.ndarray) -> np.ndarray:
    delta = np.clip(current - original, -ceiling, ceiling)
    return np.clip(original + delta, 0.0, 1.0).astype(np.float32)


def _rms(a: np.ndarray) -> float:
    return float(np.sqrt(np.mean(a * a)))


def _harden(original: np.ndarray, protected: np.ndarray, settings: Settings,
            ceiling: np.ndarray, progress: Progress,
            span: tuple[float, float],
            survival_base: np.ndarray | None = None) -> np.ndarray:
    """Make the perturbation survive JPEG/WebP re-encoding.

    Approach: *survivable projection*. Pass the protected image through several
    encoders, keep only the part of the perturbation that survived all of them, rescale it to the
    original perturbation's energy, and repeat. The result is close to a
    fixed point of the encoder - re-compressing it changes almost nothing -
    at the same visual cost.

    Things that were tried first and measurably made matters worse, recorded
    so nobody re-tries them:
      * Feeding back ``protected - decode(current)``: dominated by JPEG's error
        on the artwork itself, several times larger than the perturbation.
      * Feeding back the lost perturbation: it is precisely the content the
        encoder discards, so re-adding it is discarded again, and the extra
        energy pushes neighbouring coefficients across quantisation steps.
    Both lowered durability every iteration.

    Every candidate is scored with the same function the UI reports, and the
    best is returned, so hardening can never leave an image worse off.
    """
    base = original if survival_base is None else survival_base
    reference = protected - base
    target_rms = _rms(reference)
    if target_rms < 1e-9:
        return protected
    transforms = [fn for _name, fn in imaging.TRAINING_TRANSFORMS]
    # The round trips are independent; accel runs them on several cores.
    base_decoded = accel.run_parallel([lambda fn=fn: fn(base) for fn in transforms])

    r_norm = float(np.sqrt(np.sum(reference * reference)))

    def score(candidate: np.ndarray,
              decoded: list[np.ndarray] | None = None) -> float:
        """Same formula as quality.survival, with the base encodes cached."""
        delta = candidate - base
        d_norm = float(np.sqrt(np.sum(delta * delta)))
        if d_norm < 1e-9:
            return 0.0
        decoded = decoded or accel.run_parallel(
            [lambda fn=fn: fn(candidate) for fn in transforms])
        vals = [float(np.sum((dec - bd) * delta)) / (d_norm * r_norm)
                for dec, bd in zip(decoded, base_decoded)]
        return float(np.clip(np.mean(vals), 0.0, 1.0))

    current = protected
    decoded = accel.run_parallel([lambda fn=fn: fn(current) for fn in transforms])
    best, best_score = current, score(current, decoded)

    lo, hi = span
    n = max(1, settings.harden_iterations)
    for i in range(n):
        progress("Hardening against re-compression", lo + (hi - lo) * i / n)
        # Keep what survives *every* training transform: the mean surviving
        # residual is the part none of them strips.
        surviving = np.mean([dec - bd for dec, bd in zip(decoded, base_decoded)],
                            axis=0)
        scale = target_rms / max(_rms(surviving), 1e-9)
        current = _limit(original, base + surviving * scale, ceiling)
        decoded = accel.run_parallel(
            [lambda fn=fn, c=current: fn(c) for fn in transforms])
        s_now = score(current, decoded)
        if s_now > best_score:
            best, best_score = current, s_now

    return best.astype(np.float32)


def protect(img: LoadedImage, settings: Settings, *,
            progress: Progress | None = None,
            measure: bool = True) -> Result:
    """Apply the enabled passes to ``img`` and return the protected pixels."""
    progress = progress or _noop
    started = time.perf_counter()

    original = img.rgb
    ctx = PassContext(rgb=original, seed=settings.seed)
    current = original.astype(np.float32, copy=True)

    active = [p for p in ALL_PASSES if settings.pass_strength(p.name) > 0]
    if not active and not settings.harden:
        return Result(current, None, [], time.perf_counter() - started,
                      note="No passes enabled - output is identical to the input.")

    ceiling = delta_ceiling(ctx, settings.strength)

    # Snapshot the image once the geometric passes are done: that is the
    # baseline the durability metric compares against. The snapshot is taken
    # *after* limiting the warp to the ceiling. Taken before, the limiter's
    # partial undo of the warp at hard edges is a smooth, compression-proof
    # difference that gets miscounted as surviving perturbation - it inflated
    # the reported durability to ~100% while the real signal sat near 40%.
    geometric_done: np.ndarray | None = None
    pre_harden: np.ndarray | None = None

    # Progress budget: passes 0..0.55, hardening 0.55..0.85, metrics to 1.
    for i, p in enumerate(active):
        progress(f"Applying {p.label.lower()}", 0.55 * i / max(len(active), 1))
        current = p.apply(ctx, current, settings.pass_strength(p.name))
        if p.geometric:
            current = _limit(original, current, ceiling)
            geometric_done = current.copy()

    current = _limit(original, current, ceiling)

    if settings.harden and active:
        pre_harden = current.copy()
        current = _harden(original, current, settings, ceiling, progress,
                          (0.55, 0.85), survival_base=geometric_done)

    metrics = None
    if measure:
        progress("Measuring quality and durability", 0.88)
        metrics = quality.measure(original, current,
                                  check_survival=True,
                                  quality=settings.harden_quality,
                                  survival_base=geometric_done,
                                  intended=pre_harden)

    progress("Done", 1.0)
    return Result(current, metrics, [p.name for p in active],
                  time.perf_counter() - started)


def protect_file(path: str | Path, out_dir: str | Path, settings: Settings, *,
                 progress: Progress | None = None,
                 measure: bool = True) -> Result:
    """Load, protect and save one file. Returns the result with ``saved_to`` set."""
    img = imaging.load_image(path)
    result = protect(img, settings, progress=progress, measure=measure)

    opts = output.MetadataOptions(
        write_optout=settings.write_optout_metadata,
        credit=settings.credit,
        copyright_notice=settings.copyright_notice,
    )
    fmt = output.resolve_format(img, settings.output_format)
    dest = output.target_path(Path(path), Path(out_dir),
                              settings.filename_suffix, settings.output_format)
    result.saved_to = output.save_image(img, result.rgb, dest, fmt, opts,
                                        jpeg_quality=settings.jpeg_quality)
    return result


def preview(img: LoadedImage, settings: Settings, longest: int = 1100) -> Result:
    """A fast, downscaled protection run for the live preview.

    Perturbation is scale-dependent, so a preview is an honest indication of
    character but not of the exact final numbers. Hardening is skipped: it is
    the slow part and it does not change how the result looks.
    """
    small = imaging.resize_longest(img.rgb, longest)
    stub = LoadedImage(small, None, None, "RGB", None, img.path)
    s = settings.copy(harden=False)
    return protect(stub, s, measure=True)
