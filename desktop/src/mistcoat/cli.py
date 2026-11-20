"""Command line interface.

Useful for batch work over a whole portfolio folder, and for checking numbers
without opening the GUI:

    python -m mistcoat.cli protect art/*.png -o out --preset strong
    python -m mistcoat.cli report art/piece.png
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .core import imaging, pipeline
from .core.passes import ALL_PASSES
from .core.presets import DEFAULT_PRESET, PRESETS, PRESETS_BY_KEY, Settings


def _expand(inputs: list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(q for q in p.rglob("*")
                              if q.suffix.lower() in imaging.SUPPORTED_SUFFIXES))
        elif p.exists():
            out.append(p)
        else:
            parent = p.parent if str(p.parent) else Path(".")
            out.extend(sorted(parent.glob(p.name)))
    seen: dict[Path, None] = {}
    for p in out:
        if p.suffix.lower() in imaging.SUPPORTED_SUFFIXES:
            seen.setdefault(p.resolve(), None)
    return list(seen)


def _settings_from_args(args) -> Settings:
    settings = PRESETS_BY_KEY[args.preset].to_settings()
    if args.strength is not None:
        settings.strength = max(0.0, min(1.0, args.strength / 100.0))
    for name in args.enable or []:
        settings.enabled[name] = True
    for name in args.disable or []:
        settings.enabled[name] = False
    if args.no_harden:
        settings.harden = False
    if args.seed is not None:
        settings.seed = args.seed
    if args.credit:
        settings.credit = args.credit
    if args.copyright:
        settings.copyright_notice = args.copyright
    if args.format:
        settings.output_format = args.format
    if args.suffix is not None:
        settings.filename_suffix = args.suffix
    return settings


def cmd_protect(args) -> int:
    files = _expand(args.inputs)
    if not files:
        print("No supported images found.", file=sys.stderr)
        return 1
    settings = _settings_from_args(args)
    out_dir = Path(args.output)
    from .core import accel
    print(f"{len(files)} image(s) -> {out_dir}  "
          f"[preset {args.preset}, strength {settings.strength * 100:.0f}%, "
          f"{accel.status()['using']}]")

    failures = 0
    for i, path in enumerate(files, 1):
        label = f"[{i}/{len(files)}] {path.name}"
        try:
            result = pipeline.protect_file(path, out_dir, settings,
                                           measure=not args.quick)
        except Exception as exc:  # noqa: BLE001 - keep the batch going
            print(f"{label}: FAILED - {exc}", file=sys.stderr)
            failures += 1
            continue
        m = result.metrics
        detail = (f"{m.psnr:.1f} dB, {m.visibility_label.lower()}, "
                  f"{m.survival * 100:.0f}% durable" if m else "saved")
        print(f"{label}: {detail}  ({result.seconds:.1f}s) -> {result.saved_to.name}")
    return 1 if failures else 0


def cmd_report(args) -> int:
    files = _expand(args.inputs)
    if not files:
        print("No supported images found.", file=sys.stderr)
        return 1
    settings = _settings_from_args(args)
    for path in files:
        img = imaging.load_image(path)
        result = pipeline.protect(img, settings, measure=True)
        print(f"\n{path.name}  {img.size[0]}x{img.size[1]}  "
              f"({img.megapixels:.1f} MP, {result.seconds:.1f}s)")
        print(f"  passes: {', '.join(result.applied) or 'none'}")
        for name, value in (result.metrics.as_rows() if result.metrics else []):
            print(f"  {name:<24} {value}")
    return 0


def cmd_passes(_args) -> int:
    print("Passes:\n")
    for p in ALL_PASSES:
        state = "on by default" if p.default_on else "off by default"
        print(f"  {p.name:<14} {p.label}  [{p.visibility}, {state}]")
        print(f"                 targets: {p.targets}")
    print("\nPresets:\n")
    for preset in PRESETS:
        print(f"  {preset.key:<10} {preset.label}  "
              f"(strength {preset.strength * 100:.0f}%)")
        print(f"             {', '.join(preset.enabled)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="mistcoat", description="Protect artwork against AI training.")
    ap.add_argument("--version", action="version", version=f"Mistcoat {__version__}")
    gpu = ap.add_mutually_exclusive_group()
    gpu.add_argument("--gpu", dest="gpu", action="store_true", default=None,
                     help="use the graphics card when one is available (default)")
    gpu.add_argument("--no-gpu", dest="gpu", action="store_false",
                     help="run everything on the processor")
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("inputs", nargs="+", help="files, folders or globs")
        p.add_argument("--preset", default=DEFAULT_PRESET,
                       choices=[x.key for x in PRESETS])
        p.add_argument("--strength", type=float, help="override, 0-100")
        p.add_argument("--enable", action="append", help="force a pass on")
        p.add_argument("--disable", action="append", help="force a pass off")
        p.add_argument("--no-harden", action="store_true",
                       help="skip re-compression hardening (faster, less durable)")
        p.add_argument("--seed", type=int)
        p.add_argument("--credit")
        p.add_argument("--copyright")
        p.add_argument("--format", choices=["source", "png", "jpeg", "webp"])
        p.add_argument("--suffix")

    p_protect = sub.add_parser("protect", help="protect images and write them out")
    common(p_protect)
    p_protect.add_argument("-o", "--output", default="protected")
    p_protect.add_argument("--quick", action="store_true",
                           help="skip quality measurement")
    p_protect.set_defaults(func=cmd_protect)

    p_report = sub.add_parser("report", help="measure without writing files")
    common(p_report)
    p_report.set_defaults(func=cmd_report)

    p_passes = sub.add_parser("passes", help="list passes and presets")
    p_passes.set_defaults(func=cmd_passes)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.gpu is not None:
        from .core import accel
        accel.set_gpu_enabled(args.gpu)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
