"""Command line interface - for people, scripts, and other programs.

    mistcoat protect art/*.png --preset strong
    mistcoat protect in.png --output-file out.png --json
    mistcoat report piece.png
    mistcoat info --json
    mistcoat serve                 # JSON lines on stdin/stdout, for add-ons

Every command works headless: nothing here imports Qt, and the packaged app
runs these commands without opening a window (see docs/CLI.md for using
Mistcoat from other software).

Exit codes: 0 everything succeeded, 1 at least one image failed (or was
refused), 2 bad arguments, 3 no supported images found.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import __version__
from .core import imaging, output, pipeline, watermark
from .core.passes import ALL_PASSES
from .core.presets import DEFAULT_PRESET, PRESETS, PRESETS_BY_KEY, Settings

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_NO_INPUT = 0, 1, 2, 3
COMMANDS = ("protect", "report", "passes", "info", "serve")
ON_EXISTS = ("rename", "overwrite", "skip", "error")


class UsageError(Exception):
    """Bad arguments: exit code 2."""


# ------------------------------------------------------------------ inputs

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


def settings_from(opts: dict) -> Settings:
    """Settings from plain options - shared by the arguments and ``serve``."""
    preset = opts.get("preset") or DEFAULT_PRESET
    if preset not in PRESETS_BY_KEY:
        raise UsageError(f"unknown preset {preset!r} (choose from {', '.join(PRESETS_BY_KEY)})")
    settings = PRESETS_BY_KEY[preset].to_settings()
    if opts.get("strength") is not None:
        settings.strength = max(0.0, min(1.0, float(opts["strength"]) / 100.0))
    known = {p.name for p in ALL_PASSES}
    for key, value in (("enable", True), ("disable", False)):
        for name in opts.get(key) or []:
            if name not in known:
                raise UsageError(f"unknown pass {name!r} (choose from {', '.join(sorted(known))})")
            settings.enabled[name] = value
    if opts.get("no_harden"):
        settings.harden = False
    if opts.get("seed") is not None:
        settings.seed = int(opts["seed"])
    if opts.get("credit"):
        settings.credit = str(opts["credit"])
    if opts.get("copyright"):
        settings.copyright_notice = str(opts["copyright"])
    wm = opts.get("watermark")
    if wm:
        settings.watermark = True
        settings.watermark_text = "SAMPLE" if wm is True else str(wm)
    if opts.get("watermark_layout"):
        if opts["watermark_layout"] not in watermark.LAYOUTS:
            raise UsageError(f"unknown watermark layout {opts['watermark_layout']!r} "
                             f"(choose from {', '.join(watermark.LAYOUTS)})")
        settings.watermark_layout = opts["watermark_layout"]
    if opts.get("watermark_resistance"):
        if opts["watermark_resistance"] not in watermark.RESISTANCE:
            raise UsageError(f"unknown watermark resistance {opts['watermark_resistance']!r} "
                             f"(choose from {', '.join(watermark.RESISTANCE)})")
        settings.watermark_resistance = opts["watermark_resistance"]
    if opts.get("watermark_opacity") is not None:
        settings.watermark_opacity = max(0.05, min(1.0, float(opts["watermark_opacity"]) / 100.0))
    if opts.get("no_metadata"):
        settings.write_optout_metadata = False
    if opts.get("format"):
        if opts["format"] not in ("source", "png", "jpeg", "webp", "tiff"):
            raise UsageError(f"unknown format {opts['format']!r}")
        settings.output_format = opts["format"]
    if opts.get("jpeg_quality") is not None:
        settings.jpeg_quality = max(70, min(100, int(opts["jpeg_quality"])))
    if opts.get("suffix") is not None:
        settings.filename_suffix = str(opts["suffix"])
    return settings


def _settings_summary(s: Settings) -> dict:
    return {
        "strength": round(s.strength * 100, 1),
        "passes": [p.name for p in ALL_PASSES if s.enabled.get(p.name)],
        "harden": s.harden,
        "format": s.output_format,
        "metadata": s.write_optout_metadata,
        "seed": s.seed,
        "watermark": ({"text": s.watermark_text, "layout": s.watermark_layout,
                       "resistance": s.watermark_resistance,
                       "opacity": round(s.watermark_opacity * 100)}
                      if s.watermark else None),
    }


# ----------------------------------------------------------------- outputs

def _destination(src: Path, settings: Settings, *, output_dir: str | None,
                 output_file: str | None) -> Path:
    if output_file:
        return Path(output_file)
    # Default: a "Protected" folder next to each original, like the app.
    out_dir = Path(output_dir) if output_dir else src.parent / "Protected"
    return output.target_path(src, out_dir, settings.filename_suffix,
                              settings.output_format)


def _free_name(dest: Path) -> Path:
    """dest, or "name (2).ext", "name (3).ext"... - never an existing file."""
    if not dest.exists():
        return dest
    for i in range(2, 10_000):
        candidate = dest.with_name(f"{dest.stem} ({i}){dest.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"no free file name next to {dest}")


def _metrics_dict(m) -> dict | None:
    if m is None:
        return None
    return {
        "psnr_db": round(m.psnr, 2),
        "ssim": round(m.ssim, 5),
        "peak_change": round(m.max_delta, 1),
        "mean_change": round(m.mean_delta, 3),
        "durability": round(m.survival, 4),
        "visibility": m.visibility_label,
        "durability_label": m.survival_label,
    }


def protect_one(src: Path, settings: Settings, *, output_dir: str | None = None,
                output_file: str | None = None, on_exists: str = "rename",
                measure: bool = True, progress=None) -> dict:
    """Protect one image and describe what happened. Never raises for a bad
    image: failures come back as ``status: "failed"``."""
    started = time.perf_counter()
    entry: dict = {"input": str(src), "output": None, "status": "failed", "error": None}
    try:
        dest = _destination(src, settings, output_dir=output_dir, output_file=output_file)
        if dest.resolve() == src.resolve():
            raise ValueError("refusing to overwrite the original image; choose another output")
        if dest.exists():
            if on_exists == "skip":
                entry.update(status="skipped", output=str(dest), error="output exists")
                return entry
            if on_exists == "error":
                raise FileExistsError(f"output exists: {dest}")
            if on_exists == "rename":
                dest = _free_name(dest)
        result = pipeline.protect_file(src, None, settings, progress=progress,
                                       measure=measure, dest=dest)
        h, w = result.rgb.shape[:2]
        entry.update(status="ok", output=str(result.saved_to), width=w, height=h,
                     passes=result.applied, metrics=_metrics_dict(result.metrics),
                     color=result.color, note=result.note or None)
    except Exception as exc:  # noqa: BLE001 - report it, keep the batch going
        entry["error"] = f"{exc.__class__.__name__}: {exc}" if not str(exc) else str(exc)
    entry["seconds"] = round(time.perf_counter() - started, 2)
    return entry


# ---------------------------------------------------------------- printing

class Reporter:
    """Human text, or JSON for programs; progress on stderr."""

    def __init__(self, args):
        self.json = bool(getattr(args, "json", False))
        self.json_file = getattr(args, "json_file", None)
        self.quiet = bool(getattr(args, "quiet", False))
        mode = getattr(args, "progress", "auto")
        if mode == "auto":
            mode = "text" if (not self.json and _isatty(sys.stderr)) else "none"
        self.progress_mode = mode

    def say(self, text: str = "") -> None:
        if not self.json and not self.quiet:
            print(text, flush=True)

    def warn(self, text: str) -> None:
        if not self.json:
            print(text, file=sys.stderr, flush=True)

    def progress_for(self, index: int, total: int, name: str):
        if self.progress_mode == "none":
            return None
        if self.progress_mode == "json":
            def cb(stage, frac):
                _emit(sys.stderr, {"event": "progress", "index": index, "total": total,
                                   "file": name, "stage": stage, "fraction": round(frac, 3)})
            return cb

        def cb(stage, frac):
            print(f"\r[{index}/{total}] {name}: {stage} ({frac * 100:3.0f}%)\033[K",
                  end="", file=sys.stderr, flush=True)
        return cb

    def end_progress_line(self) -> None:
        if self.progress_mode == "text":
            print("\r\033[K", end="", file=sys.stderr, flush=True)

    def finish(self, doc: dict) -> None:
        if self.json:
            _emit(sys.stdout, doc)
        if self.json_file:
            Path(self.json_file).write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _emit(stream, obj: dict) -> None:
    stream.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stream.flush()


def _isatty(stream) -> bool:
    try:
        return stream is not None and stream.isatty()
    except (AttributeError, ValueError):
        return False


def _doc(command: str, **fields) -> dict:
    from .core import accel
    return {"mistcoat": __version__, "command": command,
            "compute": accel.status()["using"], **fields}


# ---------------------------------------------------------------- commands

def cmd_protect(args) -> int:
    rep = Reporter(args)
    files = _expand(args.inputs)
    if not files:
        rep.warn("No supported images found.")
        rep.finish(_doc("protect", ok=False, error="no supported images found", results=[]))
        return EXIT_NO_INPUT
    if args.output_file and len(files) != 1:
        raise UsageError("--output-file needs exactly one input image")
    settings = settings_from(vars(args))
    doc = _doc("protect", settings=_settings_summary(settings))
    rep.say(f"{len(files)} image(s)  [preset {args.preset}, strength "
            f"{settings.strength * 100:.0f}%, {doc['compute']}]")

    results = []
    for i, path in enumerate(files, 1):
        entry = protect_one(path, settings, output_dir=args.output,
                            output_file=args.output_file, on_exists=args.on_exists,
                            measure=not args.quick,
                            progress=rep.progress_for(i, len(files), path.name))
        rep.end_progress_line()
        results.append(entry)
        label = f"[{i}/{len(files)}] {path.name}"
        if entry["status"] == "ok":
            m = entry["metrics"]
            detail = (f"{m['psnr_db']:.1f} dB, {m['visibility'].lower()}, "
                      f"{m['durability'] * 100:.0f}% durable" if m else "saved")
            rep.say(f"{label}: {detail}  ({entry['seconds']:.1f}s) -> {entry['output']}")
            col = entry.get("color") or {}
            if col.get("input") != col.get("output"):
                rep.say(f"    colour: {col.get('input')} -> {col.get('output')}")
        elif entry["status"] == "skipped":
            rep.say(f"{label}: skipped, {entry['output']} exists")
        else:
            rep.warn(f"{label}: FAILED - {entry['error']}")

    failed = sum(r["status"] == "failed" for r in results)
    doc.update(ok=failed == 0, results=results, summary={
        "total": len(results),
        "succeeded": sum(r["status"] == "ok" for r in results),
        "skipped": sum(r["status"] == "skipped" for r in results),
        "failed": failed,
    })
    rep.finish(doc)
    return EXIT_FAILED if failed else EXIT_OK


def cmd_report(args) -> int:
    rep = Reporter(args)
    files = _expand(args.inputs)
    if not files:
        rep.warn("No supported images found.")
        rep.finish(_doc("report", ok=False, error="no supported images found", results=[]))
        return EXIT_NO_INPUT
    settings = settings_from(vars(args))
    results, failed = [], 0
    for i, path in enumerate(files, 1):
        try:
            img = imaging.load_image(path)
            cb = rep.progress_for(i, len(files), path.name)
            result = pipeline.protect(img, settings, measure=True, progress=cb)
            rep.end_progress_line()
            results.append({"input": str(path), "status": "ok", "width": img.size[0],
                            "height": img.size[1], "color": {"input": img.color_summary},
                            "passes": result.applied,
                            "metrics": _metrics_dict(result.metrics),
                            "seconds": round(result.seconds, 2)})
            rep.say(f"\n{path.name}  {img.size[0]}x{img.size[1]}  "
                    f"({img.megapixels:.1f} MP, {result.seconds:.1f}s)")
            rep.say(f"  colour: {img.color_summary}")
            rep.say(f"  passes: {', '.join(result.applied) or 'none'}")
            for name, value in (result.metrics.as_rows() if result.metrics else []):
                rep.say(f"  {name:<24} {value}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            results.append({"input": str(path), "status": "failed", "error": str(exc)})
            rep.warn(f"{path.name}: FAILED - {exc}")
    rep.finish(_doc("report", ok=failed == 0, settings=_settings_summary(settings),
                    results=results))
    return EXIT_FAILED if failed else EXIT_OK


def _catalogue() -> dict:
    return {
        "passes": [{"name": p.name, "label": p.label, "default_on": p.default_on,
                    "visibility": p.visibility, "targets": p.targets} for p in ALL_PASSES],
        "presets": [{"key": p.key, "label": p.label, "strength": round(p.strength * 100),
                     "passes": list(p.enabled), "default": p.key == DEFAULT_PRESET}
                    for p in PRESETS],
    }


def cmd_passes(args) -> int:
    rep = Reporter(args)
    cat = _catalogue()
    rep.say("Passes:\n")
    for p in cat["passes"]:
        state = "on by default" if p["default_on"] else "off by default"
        rep.say(f"  {p['name']:<14} {p['label']}  [{p['visibility']}, {state}]")
        rep.say(f"                 targets: {p['targets']}")
    rep.say("\nPresets:\n")
    for p in cat["presets"]:
        rep.say(f"  {p['key']:<10} {p['label']}  (strength {p['strength']}%)")
        rep.say(f"             {', '.join(p['passes'])}")
    rep.finish(_doc("passes", ok=True, **cat))
    return EXIT_OK


def cmd_info(args) -> int:
    from .core import accel
    rep = Reporter(args)
    st = accel.status()
    doc = _doc("info", ok=True, gpu=st,
               input_formats=sorted({s.lstrip(".") for s in imaging.SUPPORTED_SUFFIXES}),
               output_formats=["source", "png", "jpeg", "webp", "tiff"],
               exit_codes={"0": "success", "1": "one or more images failed",
                           "2": "bad arguments", "3": "no supported images found"},
               **_catalogue())
    rep.say(f"Mistcoat {__version__}")
    rep.say(f"Compute: {st['using']}" + (f"  ({st['gpu_reason']})" if st["gpu_reason"] else ""))
    rep.say(f"Reads:   {', '.join(doc['input_formats'])}")
    rep.say(f"Presets: {', '.join(p['key'] for p in doc['presets'])}")
    rep.finish(doc)
    return EXIT_OK


def cmd_serve(args) -> int:
    """Line-delimited JSON on stdin/stdout, so a host application can keep one
    Mistcoat process running (no start-up cost per image, GPU stays ready).

    Request:  {"id": 1, "command": "protect", "input": "a.png",
               "output_file": "b.png", "preset": "strong", "progress": true}
    Reply:    {"id": 1, "ok": true, "result": {...same as protect --json...}}
    Progress: {"id": 1, "event": "progress", "stage": "...", "fraction": 0.4}
    Commands: protect, report, info, ping, quit. Unknown fields are ignored.
    """
    _emit(sys.stdout, {"event": "ready", "mistcoat": __version__})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        rid = None
        try:
            req = json.loads(line)
            rid = req.get("id")
            command = req.get("command", "protect")
            if command == "quit":
                _emit(sys.stdout, {"id": rid, "ok": True, "result": "bye"})
                break
            if command == "ping":
                _emit(sys.stdout, {"id": rid, "ok": True, "result": "pong"})
                continue
            if command == "info":
                from .core import accel
                if "gpu" in req:
                    accel.set_gpu_enabled(bool(req["gpu"]))
                _emit(sys.stdout, {"id": rid, "ok": True,
                                   "result": _doc("info", gpu=accel.status(), **_catalogue())})
                continue
            if command not in ("protect", "report"):
                raise UsageError(f"unknown command {command!r}")
            src = Path(req["input"])
            if not src.is_file():
                raise UsageError(f"input not found: {src}")
            settings = settings_from(req)
            progress = None
            if req.get("progress"):
                def progress(stage, frac, rid=rid):
                    _emit(sys.stdout, {"id": rid, "event": "progress", "stage": stage,
                                       "fraction": round(frac, 3)})
            if command == "protect":
                on_exists = req.get("on_exists", "rename")
                if on_exists not in ON_EXISTS:
                    raise UsageError(f"on_exists must be one of {', '.join(ON_EXISTS)}")
                result = protect_one(src, settings, output_dir=req.get("output_dir"),
                                     output_file=req.get("output_file"),
                                     on_exists=on_exists,
                                     measure=not req.get("quick", False), progress=progress)
                _emit(sys.stdout, {"id": rid, "ok": result["status"] != "failed",
                                   "result": result})
            else:
                img = imaging.load_image(src)
                r = pipeline.protect(img, settings, measure=True, progress=progress)
                _emit(sys.stdout, {"id": rid, "ok": True, "result": {
                    "input": str(src), "passes": r.applied,
                    "metrics": _metrics_dict(r.metrics), "seconds": round(r.seconds, 2)}})
        except (UsageError, KeyError, ValueError, TypeError) as exc:
            _emit(sys.stdout, {"id": rid, "ok": False, "error": f"bad request: {exc}"})
        except Exception as exc:  # noqa: BLE001 - the server keeps running
            _emit(sys.stdout, {"id": rid, "ok": False, "error": str(exc)})
    return EXIT_OK


# ------------------------------------------------------------------ parser

class _Parser(argparse.ArgumentParser):
    """argparse that reports errors as JSON when --json was asked for."""

    json_requested = False      # set by main() from the arguments it parses

    def error(self, message):  # noqa: D401
        if _Parser.json_requested:
            _emit(sys.stdout, {"mistcoat": __version__, "ok": False,
                               "error": f"bad arguments: {message}"})
            raise SystemExit(EXIT_USAGE)
        super().error(message)


def build_parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="mistcoat",
                 description="Protect artwork against AI training. Runs headless; "
                             "see docs/CLI.md for use from other software.")
    ap.add_argument("--version", action="version", version=f"Mistcoat {__version__}")
    gpu = ap.add_mutually_exclusive_group()
    gpu.add_argument("--gpu", dest="gpu", action="store_true", default=None,
                     help="use the graphics card when one is available (default)")
    gpu.add_argument("--no-gpu", dest="gpu", action="store_false",
                     help="run everything on the processor")
    sub = ap.add_subparsers(dest="command", required=True, parser_class=_Parser)

    def machine(p):
        p.add_argument("--json", action="store_true",
                       help="print one JSON document with the results on stdout")
        p.add_argument("--json-file", metavar="FILE",
                       help="also write that JSON document to FILE")
        p.add_argument("--quiet", "-q", action="store_true", help="no text output")

    def common(p):
        p.add_argument("inputs", nargs="+", help="image files, folders or globs")
        p.add_argument("--preset", default=DEFAULT_PRESET, choices=[x.key for x in PRESETS])
        p.add_argument("--strength", type=float, help="override, 0-100")
        p.add_argument("--enable", action="append", metavar="PASS", help="force a pass on")
        p.add_argument("--disable", action="append", metavar="PASS", help="force a pass off")
        p.add_argument("--no-harden", action="store_true",
                       help="skip re-compression hardening (faster, less durable)")
        p.add_argument("--seed", type=int)
        p.add_argument("--credit", help="artist name to embed")
        p.add_argument("--copyright", help="copyright notice to embed")
        p.add_argument("--watermark", nargs="?", const="SAMPLE", metavar="TEXT",
                       help="draw a visible watermark into the picture "
                            "(default text: SAMPLE)")
        p.add_argument("--watermark-layout", choices=list(watermark.LAYOUTS),
                       help="tiled (default), centre or corner")
        p.add_argument("--watermark-resistance", choices=list(watermark.RESISTANCE),
                       help="how hard the watermark is to remove: plain, strong "
                            "(default) or maximum")
        p.add_argument("--watermark-opacity", type=float, metavar="5-100",
                       help="watermark opacity in percent (default 50)")
        p.add_argument("--no-metadata", action="store_true",
                       help="do not embed the 'do not train' metadata")
        p.add_argument("--progress", choices=["auto", "none", "text", "json"], default="auto",
                       help="progress on stderr: text, JSON lines, or none "
                            "(auto: text in a terminal, otherwise none)")
        machine(p)

    p = sub.add_parser("protect", help="protect images and write them out")
    common(p)
    where = p.add_mutually_exclusive_group()
    where.add_argument("-o", "--output", metavar="DIR",
                       help="output folder (default: a 'Protected' folder next to "
                            "each original)")
    where.add_argument("--output-file", metavar="FILE",
                       help="exact output file, for a single input; its extension "
                            "picks the format")
    p.add_argument("--on-exists", choices=ON_EXISTS, default="rename",
                   help="when the output file exists: add a number (default), "
                        "overwrite it, skip the image, or fail")
    p.add_argument("--format", choices=["source", "png", "jpeg", "webp", "tiff"],
                   help="output format (default: same as the source)")
    p.add_argument("--jpeg-quality", type=int, metavar="70-100")
    p.add_argument("--suffix", help="file name suffix (default: _protected)")
    p.add_argument("--quick", action="store_true", help="skip quality measurement")
    p.set_defaults(func=cmd_protect)

    p = sub.add_parser("report", help="measure the effect without writing files")
    common(p)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("passes", help="list passes and presets")
    machine(p)
    p.set_defaults(func=cmd_passes)

    p = sub.add_parser("info", help="version, GPU status, formats and presets")
    machine(p)
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("serve", help="JSON-lines server on stdin/stdout for add-ons")
    p.set_defaults(func=cmd_serve)
    return ap


def main(argv: list[str] | None = None) -> int:
    from ._console import ensure_std_streams
    ensure_std_streams()
    parser = build_parser()
    _Parser.json_requested = "--json" in (sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:      # --help, --version, or bad arguments
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    if args.gpu is not None:
        from .core import accel
        accel.set_gpu_enabled(args.gpu)
    try:
        return args.func(args)
    except UsageError as exc:
        if getattr(args, "json", False):
            _emit(sys.stdout, {"mistcoat": __version__, "ok": False,
                               "error": f"bad arguments: {exc}"})
        else:
            parser.error(str(exc))
        return EXIT_USAGE


def is_cli_invocation(argv: list[str]) -> bool:
    """True when the app was started with a command-line command rather than
    to open the window (``Mistcoat protect ...``, ``Mistcoat --version``)."""
    args = argv[1:]
    if not args:
        return False
    first = args[0]
    return first in COMMANDS or first in ("--version", "-h", "--help", "--gpu", "--no-gpu")


if __name__ == "__main__":
    raise SystemExit(main())
