"""GUI entry point."""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

from .. import APP_NAME
from . import theme
from .main_window import MainWindow


class MistcoatApplication(QApplication):
    """Routes macOS 'open file' events (Dock drops, Finder 'Open With') to the
    window. On macOS those never arrive as command-line arguments."""

    def __init__(self, argv):
        super().__init__(argv)
        self.window = None
        self._pending: list[str] = []

    def event(self, e):  # noqa: N802
        if e.type() == QEvent.FileOpen:
            path = e.file()
            if self.window is not None:
                from pathlib import Path
                self.window._add_paths([Path(path)])
            else:
                self._pending.append(path)
            return True
        return super().event(e)


def _selftest(report_path: str) -> int:
    """Exercise the frozen build end to end without a window.

    Used by CI on platforms nobody can click through (the macOS runner) and to
    check a packaged .exe/.app actually contains everything it needs.
    """
    import json
    import tempfile
    import traceback
    from pathlib import Path

    import numpy as np
    from PIL import Image

    report: dict = {"ok": False}
    try:
        from PySide6.QtWidgets import QApplication as _QA
        import os
        # Headless by default. With QT_QPA_PLATFORM set (e.g. "xcb" under
        # Xvfb in CI) the real display plugin is loaded instead, which proves
        # every library it needs is bundled.
        args = ["selftest"]
        if not os.environ.get("QT_QPA_PLATFORM"):
            args += ["-platform", "offscreen"]
        app = _QA.instance() or _QA(args)
        from ..core import accel, pipeline
        from ..core.presets import PRESETS_BY_KEY

        tmp = Path(tempfile.mkdtemp(prefix="mistcoat-selftest-"))
        rng = np.random.default_rng(0)
        art = (rng.random((240, 320, 3)) * 255).astype(np.uint8)
        src = tmp / "selftest.png"
        Image.fromarray(art).save(src)
        result = pipeline.protect_file(src, tmp / "out",
                                       PRESETS_BY_KEY["balanced"].to_settings())
        theme.apply(app)
        window = MainWindow()          # builds every widget, no show()
        window.close()
        report.update(ok=result.saved_to.exists(),
                      saved=str(result.saved_to),
                      psnr=round(result.metrics.psnr, 2),
                      durability=round(result.metrics.survival, 3),
                      seconds=round(result.seconds, 2),
                      qt_platform=app.platformName(),
                      compute=accel.status()["using"],
                      arch=__import__("platform").machine())
        del app
    except Exception:  # noqa: BLE001
        report["error"] = traceback.format_exc()
    Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


LINUX_APP_ID = "app.mistcoat.Mistcoat"


def _icon_path():
    """The window icon, shipped as package data (mistcoat/resources)."""
    from importlib.resources import files
    path = files("mistcoat") / "resources" / "icon.png"
    return path if path.is_file() else None


def run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    if "--selftest" in argv:
        i = argv.index("--selftest")
        out = argv[i + 1] if i + 1 < len(argv) else "mistcoat-selftest.json"
        return _selftest(out)
    QApplication.setAttribute(Qt.AA_DontUseNativeMenuBar, False)
    app = MistcoatApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    if sys.platform.startswith("linux"):
        # Lets Wayland/GNOME/KDE match the window to the installed launcher
        # (app.mistcoat.Mistcoat.desktop) for its icon and taskbar grouping.
        app.setDesktopFileName(LINUX_APP_ID)
    icon = _icon_path()
    if icon is not None:
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(icon)))
    theme.apply(app)       # native style, follows system light/dark

    # The GPU preference, remembered between sessions (on unless switched off).
    from PySide6.QtCore import QSettings
    from ..core import accel
    stored = QSettings().value("performance/use_gpu", True)
    accel.set_gpu_enabled(accel.gpu_enabled() and str(stored).lower() not in ("false", "0"))

    window = MainWindow()
    app.window = window
    if app._pending:
        from pathlib import Path
        window._add_paths([Path(a) for a in app._pending])
    # Files passed on the command line (or dropped on the .exe) open straight away.
    extra = [a for a in argv[1:] if not a.startswith("-")]
    if extra:
        from pathlib import Path
        window._add_paths([Path(a) for a in extra])
    window.show()
    return app.exec()
