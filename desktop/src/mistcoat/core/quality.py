"""Measuring what the protection cost, and whether it will survive.

The numbers here are what let the artist trust the slider: how much the file
actually changed, and how much of the change is still present after a platform
re-compresses it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import accel, imaging


@dataclass
class Metrics:
    psnr: float            # dB; >40 is generally imperceptible, >35 is close
    ssim: float            # 0..1 structural similarity
    max_delta: float       # largest single-channel change, 8-bit units
    mean_delta: float      # average absolute change, 8-bit units
    survival: float        # 0..1 fraction of perturbation energy surviving JPEG

    @property
    def visibility_label(self) -> str:
        if self.psnr >= 42 and self.max_delta <= 10:
            return "Imperceptible"
        if self.psnr >= 36:
            return "Invisible in normal viewing"
        if self.psnr >= 30:
            return "Faint texture visible up close"
        return "Visibly altered"

    @property
    def survival_label(self) -> str:
        if self.survival >= 0.75:
            return "Survives re-compression well"
        if self.survival >= 0.5:
            return "Mostly survives re-compression"
        if self.survival >= 0.3:
            return "Partly survives re-compression"
        return "Largely stripped by re-compression"

    def as_rows(self) -> list[tuple[str, str]]:
        return [
            ("Visible change", self.visibility_label),
            ("PSNR", f"{self.psnr:.1f} dB"),
            ("Structural similarity", f"{self.ssim * 100:.2f}%"),
            ("Peak pixel change", f"{self.max_delta:.0f}/255"),
            ("Durability", f"{self.survival_label} ({self.survival * 100:.0f}%)"),
        ]


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a - b) ** 2))
    if mse <= 1e-12:
        return 99.0
    return float(10.0 * np.log10(1.0 / mse))


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Mean SSIM on luminance with an 11px-equivalent gaussian window."""
    x = imaging.luminance(a)
    y = imaging.luminance(b)
    sigma = 1.5
    c1, c2 = 0.01 ** 2, 0.03 ** 2

    mx = imaging.blur(x, sigma)
    my = imaging.blur(y, sigma)
    mxx = imaging.blur(x * x, sigma)
    myy = imaging.blur(y * y, sigma)
    mxy = imaging.blur(x * y, sigma)

    vx = np.maximum(mxx - mx * mx, 0.0)
    vy = np.maximum(myy - my * my, 0.0)
    cxy = mxy - mx * my

    num = (2 * mx * my + c1) * (2 * cxy + c2)
    den = (mx * mx + my * my + c1) * (vx + vy + c2)
    return float(np.mean(num / np.maximum(den, 1e-12)))


def survival(original: np.ndarray, protected: np.ndarray, quality: int = 72,
             intended: np.ndarray | None = None, transform=None) -> float:
    """How much perturbation is still there after a JPEG round trip.

    The surviving residual is projected onto the perturbation actually in the
    file (noise that survives but has lost its structure is not protection),
    and that surviving magnitude is divided by the magnitude of the
    perturbation we *meant* to apply:

        durability = <surviving, delta> / (|delta| * |intended - original|)

    ``intended`` is the image before hardening. The normalisation matters.
    Divide by the final perturbation's own size instead and the number is
    trivially gamed: shrink the perturbation to whatever compression happens
    to preserve and it reads 100% while protecting nothing. Divided by the
    intended size, throwing perturbation away lowers the score, and the score
    cannot exceed 1 without exceeding what was asked for.
    """
    delta = protected - original
    reference = delta if intended is None else (intended - original)
    d_norm = float(np.sqrt(np.sum(delta * delta)))
    r_norm = float(np.sqrt(np.sum(reference * reference)))
    if d_norm < 1e-9 or r_norm < 1e-9:
        return 0.0
    if transform is None:
        def transform(x):
            return imaging.jpeg_roundtrip(x, quality=quality)
    decoded = transform(protected)
    # Compare against a transformed original so we do not count compression
    # artefacts of the artwork itself as surviving perturbation.
    base = transform(original)
    surviving = decoded - base
    score = float(np.sum(surviving * delta)) / (d_norm * r_norm)
    return float(np.clip(score, 0.0, 1.0))


def measure(original: np.ndarray, protected: np.ndarray,
            check_survival: bool = True, quality: int = 72,
            sample_longest: int = 1400,
            survival_base: np.ndarray | None = None,
            intended: np.ndarray | None = None) -> Metrics:
    """Compare ``protected`` against ``original``.

    ``survival_base`` is the image as it stood after any geometric passes. The
    durability projection is taken against that, because a sub-pixel warp is
    not a residual pattern that compression can strip - it is already baked
    into the geometry - and including it makes the number read lower the
    stronger the protection gets, which is simply wrong.

    ``intended`` is the image before hardening; see ``survival``.
    """
    delta = np.abs(protected - original)
    surv = 0.0
    if check_survival:
        base_for_survival = original if survival_base is None else survival_base
        # Survival is scale-dependent; measure on a sample to keep it quick.
        a = imaging.resize_longest(base_for_survival, sample_longest)
        b = imaging.resize_longest(protected, sample_longest)
        ref = None if intended is None else imaging.resize_longest(intended,
                                                                   sample_longest)
        if not (a.shape == b.shape and (ref is None or ref.shape == a.shape)):
            a, b, ref = base_for_survival, protected, intended
        # Average over held-out transforms - see imaging.HELD_OUT_TRANSFORMS.
        scores = accel.run_parallel(
            [lambda fn=fn: survival(a, b, intended=ref, transform=fn)
             for _name, fn in imaging.HELD_OUT_TRANSFORMS])
        surv = float(np.mean(scores))
    return Metrics(
        psnr=psnr(original, protected),
        ssim=ssim(original, protected),
        max_delta=float(delta.max() * 255.0),
        mean_delta=float(delta.mean() * 255.0),
        survival=surv,
    )
