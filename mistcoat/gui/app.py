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
        app = _QA.instance() or _QA(["selftest", "-platform", "offscreen"])
        from ..core import pipeline
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
                      seconds=round(result.seconds, 2))
        del app
    except Exception:  # noqa: BLE001
        report["error"] = traceback.format_exc()
    Path(report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


LINUX_APP_ID = "app.mistcoat.Mistcoat"


def _icon_path():
    """assets/icon.png, from the PyInstaller bundle or the source tree."""
    from pathlib import Path
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    path = base / "assets" / "icon.png"
    return path if path.exists() else None


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
