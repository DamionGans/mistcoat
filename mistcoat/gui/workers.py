"""Background threads.

Protection is seconds of solid numpy work, so none of it may happen on the GUI
thread. Preview requests carry a generation token: the artist will drag the
strength slider faster than previews can render, and stale results must be
dropped rather than flashed on screen.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal

from ..core import imaging, pipeline
from ..core.imaging import LoadedImage
from ..core.presets import Settings
from ..core.quality import Metrics


class Cancelled(Exception):
    pass


class PreviewWorker(QThread):
    """Renders one downscaled protection preview."""

    finished_preview = Signal(int, object, object)   # token, rgb, metrics
    failed = Signal(int, str)

    def __init__(self, token: int, base_rgb: np.ndarray, settings: Settings,
                 parent=None):
        super().__init__(parent)
        self.token = token
        self.base_rgb = base_rgb
        self.settings = settings

    def run(self) -> None:
        try:
            stub = LoadedImage(self.base_rgb, None, None, "RGB", None, None)
            result = pipeline.protect(stub, self.settings.copy(harden=False),
                                      measure=True)
            if not self.isInterruptionRequested():
                self.finished_preview.emit(self.token, result.rgb, result.metrics)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.token, str(exc))


class LoadWorker(QThread):
    """Decodes a file and prepares the preview-sized copy."""

    loaded = Signal(int, object, object)   # token, LoadedImage, preview rgb
    failed = Signal(int, str)

    PREVIEW_LONGEST = 1100

    def __init__(self, token: int, path: Path, parent=None):
        super().__init__(parent)
        self.token = token
        self.path = path

    def run(self) -> None:
        try:
            img = imaging.load_image(self.path)
            small = imaging.resize_longest(img.rgb, self.PREVIEW_LONGEST)
            self.loaded.emit(self.token, img, small)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self.token, f"Could not open {self.path.name}: {exc}")


@dataclass
class FileOutcome:
    path: Path
    ok: bool
    saved_to: Path | None = None
    metrics: Metrics | None = None
    seconds: float = 0.0
    error: str = ""


class BatchWorker(QThread):
    """Protects a list of files and writes them out."""

    file_started = Signal(int, str)            # index, filename
    file_progress = Signal(float, str)         # 0..1 within file, stage
    file_done = Signal(object)                 # FileOutcome
    all_done = Signal(list, bool)              # outcomes, cancelled

    def __init__(self, paths: list[Path], out_dir: Path, settings: Settings,
                 parent=None):
        super().__init__(parent)
        self.paths = paths
        self.out_dir = out_dir
        self.settings = settings
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True
        self.requestInterruption()

    def run(self) -> None:
        outcomes: list[FileOutcome] = []
        for i, path in enumerate(self.paths):
            if self._cancel:
                break
            self.file_started.emit(i, path.name)

            def on_progress(stage: str, frac: float) -> None:
                if self._cancel:
                    raise Cancelled
                self.file_progress.emit(frac, stage)

            try:
                result = pipeline.protect_file(path, self.out_dir, self.settings,
                                               progress=on_progress, measure=True)
                outcome = FileOutcome(path, True, result.saved_to,
                                      result.metrics, result.seconds)
            except Cancelled:
                break
            except Exception as exc:  # noqa: BLE001 - one bad file must not
                outcome = FileOutcome(path, False, error=str(exc))  # stop the batch
            outcomes.append(outcome)
            self.file_done.emit(outcome)

        self.all_done.emit(outcomes, self._cancel)


class ThreadKeeper(QObject):
    """Keeps finished QThreads alive until Qt has cleaned them up.

    Without this, a worker that goes out of scope while still winding down
    takes the process with it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._threads: list[QThread] = []

    def add(self, thread: QThread) -> None:
        self._threads.append(thread)
        thread.finished.connect(lambda: self._threads.remove(thread)
                                if thread in self._threads else None)

    def stop_all(self) -> None:
        for thread in list(self._threads):
            thread.requestInterruption()
            if hasattr(thread, "cancel"):
                thread.cancel()
            thread.wait(4000)
        self._threads.clear()
