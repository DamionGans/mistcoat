"""The Mistcoat main window."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QGridLayout, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy, QSlider,
                               QSpinBox, QSplitter, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..core import imaging
from ..core.passes import ALL_PASSES
from ..core.presets import DEFAULT_PRESET, PRESETS, PRESETS_BY_KEY, Settings
from . import preview as preview_mod
from . import theme
from .preview import PreviewCanvas
from .workers import BatchWorker, LoadWorker, PreviewWorker, ThreadKeeper

PREVIEW_DEBOUNCE_MS = 320

DISCLAIMER = (
    "No tool can make an image impossible to train on. Mistcoat raises the "
    "cost and degrades the result; it is not a guarantee. Always keep your "
    "unprotected originals."
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1360, 880)
        self.setAcceptDrops(True)

        self.settings = PRESETS_BY_KEY[DEFAULT_PRESET].to_settings()
        self.paths: list[Path] = []
        self.current_image = None          # LoadedImage
        self.current_preview_rgb = None    # downscaled original
        self.out_dir: Path | None = None

        self._token = 0
        self._preview_worker: PreviewWorker | None = None
        self._batch: BatchWorker | None = None
        self._threads = ThreadKeeper(self)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(PREVIEW_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._render_preview)

        self._build_ui()
        self._sync_controls_from_settings()
        self._update_actions()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 0)
        root.setSpacing(10)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_file_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.addWidget(self._build_controls_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([250, 760, 350])
        root.addWidget(splitter, 1)
        root.addWidget(self._build_action_bar())

        disclaimer = QLabel(DISCLAIMER)
        disclaimer.setObjectName("Disclaimer")
        disclaimer.setWordWrap(True)
        root.addWidget(disclaimer)

        self.setCentralWidget(central)
        self._build_menus()

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        add = QAction("&Add images...", self)
        add.setShortcut(QKeySequence.Open)
        add.triggered.connect(self.add_images)
        file_menu.addAction(add)

        out = QAction("Choose &output folder...", self)
        out.triggered.connect(self.choose_output)
        file_menu.addAction(out)
        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("What this actually does", self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)

    def _build_file_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)

        heading = QLabel("IMAGES")
        heading.setObjectName("SectionHeading")
        lay.addWidget(heading)

        self.file_list = QListWidget()
        self.file_list.currentRowChanged.connect(self._on_file_selected)
        lay.addWidget(self.file_list, 1)

        row = QHBoxLayout()
        add_btn = QPushButton("Add images")
        add_btn.clicked.connect(self.add_images)
        remove_btn = QPushButton("Remove")
        remove_btn.clicked.connect(self.remove_selected)
        row.addWidget(add_btn)
        row.addWidget(remove_btn)
        lay.addLayout(row)

        self.file_hint = QLabel("You can also drop files or a folder here.")
        self.file_hint.setObjectName("Subtle")
        self.file_hint.setWordWrap(True)
        lay.addWidget(self.file_hint)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("Panel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(8)

        bar = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        for label, mode in (("Split", preview_mod.MODE_SPLIT),
                            ("Original", preview_mod.MODE_BEFORE),
                            ("Protected", preview_mod.MODE_AFTER),
                            ("Difference", preview_mod.MODE_DIFF)):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("Preset")
            btn.clicked.connect(lambda _c, m=mode: self.canvas.set_mode(m))
            self.mode_group.addButton(btn)
            bar.addWidget(btn)
            if mode == preview_mod.MODE_SPLIT:
                btn.setChecked(True)
        bar.addStretch(1)

        fit_btn = QPushButton("Fit")
        fit_btn.clicked.connect(lambda: self.canvas.fit())
        full_btn = QPushButton("100%")
        full_btn.clicked.connect(lambda: self.canvas.set_zoom(1.0))
        self.zoom_label = QLabel("--")
        self.zoom_label.setObjectName("Subtle")
        self.zoom_label.setMinimumWidth(52)
        self.zoom_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bar.addWidget(self.zoom_label)
        bar.addWidget(fit_btn)
        bar.addWidget(full_btn)
        lay.addLayout(bar)

        self.canvas = PreviewCanvas()
        self.canvas.zoom_changed.connect(
            lambda z: self.zoom_label.setText(f"{z * 100:.0f}%"))
        lay.addWidget(self.canvas, 1)

        self.preview_note = QLabel(
            "Preview is rendered at screen size. Hold Space to see the original; "
            "drag the divider; scroll to zoom.")
        self.preview_note.setObjectName("Subtle")
        self.preview_note.setWordWrap(True)
        lay.addWidget(self.preview_note)

        self.metrics_box = QFrame()
        self.metrics_box.setObjectName("Panel")
        self.metrics_grid = QGridLayout(self.metrics_box)
        self.metrics_grid.setContentsMargins(10, 8, 10, 8)
        self.metrics_grid.setHorizontalSpacing(18)
        self.metrics_grid.setVerticalSpacing(3)
        lay.addWidget(self.metrics_box)
        self._set_metrics(None)
        return panel

    def _build_controls_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(2, 0, 8, 0)
        lay.setSpacing(10)

        # --- presets
        preset_box = QGroupBox("PROTECTION LEVEL")
        pv = QVBoxLayout(preset_box)
        self.preset_group = QButtonGroup(self)
        self.preset_buttons: dict[str, QPushButton] = {}
        for preset in PRESETS:
            btn = QPushButton(preset.label)
            btn.setObjectName("Preset")
            btn.setCheckable(True)
            btn.setToolTip(preset.blurb)
            btn.clicked.connect(lambda _c, k=preset.key: self._apply_preset(k))
            self.preset_group.addButton(btn)
            self.preset_buttons[preset.key] = btn
            pv.addWidget(btn)
        self.preset_blurb = QLabel()
        self.preset_blurb.setObjectName("Subtle")
        self.preset_blurb.setWordWrap(True)
        pv.addWidget(self.preset_blurb)
        lay.addWidget(preset_box)

        # --- strength
        strength_box = QGroupBox("STRENGTH")
        sv = QVBoxLayout(strength_box)
        row = QHBoxLayout()
        self.strength_slider = QSlider(Qt.Horizontal)
        self.strength_slider.setRange(5, 100)
        self.strength_slider.valueChanged.connect(self._on_strength)
        self.strength_value = QLabel("60%")
        self.strength_value.setMinimumWidth(42)
        self.strength_value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(self.strength_slider, 1)
        row.addWidget(self.strength_value)
        sv.addLayout(row)
        hint = QLabel("Higher means more protection and more visible change. "
                      "The numbers under the preview tell you where you are.")
        hint.setObjectName("Subtle")
        hint.setWordWrap(True)
        sv.addWidget(hint)
        lay.addWidget(strength_box)

        # --- passes
        pass_box = QGroupBox("WHAT TO DISRUPT")
        pb = QVBoxLayout(pass_box)
        self.pass_checks: dict[str, QCheckBox] = {}
        for p in ALL_PASSES:
            cb = QCheckBox(p.label)
            cb.setToolTip(f"{p.blurb}\n\nTargets: {p.targets}\n"
                          f"Visible change at full strength: {p.visibility}")
            cb.toggled.connect(lambda on, n=p.name: self._on_pass_toggled(n, on))
            self.pass_checks[p.name] = cb
            pb.addWidget(cb)
            sub = QLabel(p.targets)
            sub.setObjectName("Subtle")
            sub.setWordWrap(True)
            sub.setContentsMargins(23, 0, 0, 4)
            pb.addWidget(sub)
        lay.addWidget(pass_box)

        # --- durability
        dur_box = QGroupBox("DURABILITY")
        dv = QVBoxLayout(dur_box)
        self.harden_check = QCheckBox("Harden against re-compression")
        self.harden_check.setToolTip(
            "Re-projects the perturbation so it survives the JPEG/WebP "
            "re-encoding that galleries and social platforms apply on upload.\n"
            "Costs a few seconds per image. Strongly recommended.")
        self.harden_check.toggled.connect(self._on_harden)
        dv.addWidget(self.harden_check)
        note = QLabel("Without this, a platform's own re-encode strips much of "
                      "the protection on upload.")
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        dv.addWidget(note)
        lay.addWidget(dur_box)

        # --- metadata
        meta_box = QGroupBox("CREDIT AND OPT-OUT")
        mv = QVBoxLayout(meta_box)
        self.optout_check = QCheckBox("Embed 'do not train' metadata")
        self.optout_check.setToolTip(
            "Writes the IPTC/PLUS Data Mining property and matching usage "
            "terms. Any scraper can strip this - it is a legal and policy "
            "signal, not a technical defence - but it costs nothing.")
        self.optout_check.toggled.connect(self._on_optout)
        mv.addWidget(self.optout_check)
        self.credit_edit = QLineEdit()
        self.credit_edit.setPlaceholderText("Artist name (optional)")
        self.credit_edit.textChanged.connect(
            lambda t: setattr(self.settings, "credit", t))
        mv.addWidget(self.credit_edit)
        self.copyright_edit = QLineEdit()
        self.copyright_edit.setPlaceholderText("Copyright notice (optional)")
        self.copyright_edit.textChanged.connect(
            lambda t: setattr(self.settings, "copyright_notice", t))
        mv.addWidget(self.copyright_edit)
        lay.addWidget(meta_box)

        # --- output
        out_box = QGroupBox("OUTPUT")
        ov = QVBoxLayout(out_box)
        self.out_label = QLabel("Alongside the originals, in 'Protected'")
        self.out_label.setObjectName("Subtle")
        self.out_label.setWordWrap(True)
        ov.addWidget(self.out_label)
        out_btn = QPushButton("Choose output folder...")
        out_btn.clicked.connect(self.choose_output)
        ov.addWidget(out_btn)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format"))
        self.format_combo = QComboBox()
        for label, key in (("Same as source", "source"), ("PNG (lossless)", "png"),
                           ("JPEG", "jpeg"), ("WebP", "webp")):
            self.format_combo.addItem(label, key)
        self.format_combo.currentIndexChanged.connect(self._on_format)
        fmt_row.addWidget(self.format_combo, 1)
        ov.addLayout(fmt_row)

        q_row = QHBoxLayout()
        q_row.addWidget(QLabel("JPEG quality"))
        self.quality_spin = QSpinBox()
        self.quality_spin.setRange(70, 100)
        self.quality_spin.valueChanged.connect(
            lambda v: setattr(self.settings, "jpeg_quality", v))
        q_row.addWidget(self.quality_spin, 1)
        ov.addLayout(q_row)

        self.format_warning = QLabel()
        self.format_warning.setObjectName("Subtle")
        self.format_warning.setWordWrap(True)
        ov.addWidget(self.format_warning)
        lay.addWidget(out_box)

        lay.addStretch(1)
        scroll.setWidget(inner)
        scroll.setMinimumWidth(330)
        scroll.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        return scroll

    def _build_action_bar(self) -> QWidget:
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)

        self.status = QLabel("Add an image to begin.")
        self.status.setObjectName("Subtle")
        lay.addWidget(self.status, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setMaximumWidth(260)
        self.progress.hide()
        lay.addWidget(self.progress)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_batch)
        self.cancel_btn.hide()
        lay.addWidget(self.cancel_btn)

        self.protect_btn = QPushButton("Protect and save")
        self.protect_btn.setObjectName("Primary")
        self.protect_btn.clicked.connect(self.run_batch)
        lay.addWidget(self.protect_btn)
        return bar

    # ------------------------------------------------------------ settings

    def _sync_controls_from_settings(self) -> None:
        blocked = [self.strength_slider, self.harden_check, self.optout_check,
                   self.quality_spin, *self.pass_checks.values()]
        for w in blocked:
            w.blockSignals(True)
        self.strength_slider.setValue(int(round(self.settings.strength * 100)))
        self.strength_value.setText(f"{self.settings.strength * 100:.0f}%")
        self.harden_check.setChecked(self.settings.harden)
        self.optout_check.setChecked(self.settings.write_optout_metadata)
        self.quality_spin.setValue(self.settings.jpeg_quality)
        for name, cb in self.pass_checks.items():
            cb.setChecked(self.settings.enabled.get(name, False))
        for w in blocked:
            w.blockSignals(False)
        self._update_preset_selection()

    def _update_preset_selection(self) -> None:
        """Highlight the preset only while the settings still match it exactly."""
        match = None
        for preset in PRESETS:
            same_strength = abs(preset.strength - self.settings.strength) < 0.005
            same_passes = all(
                self.settings.enabled.get(p.name, False) == (p.name in preset.enabled)
                for p in ALL_PASSES)
            if same_strength and same_passes and preset.harden == self.settings.harden:
                match = preset
                break
        for key, btn in self.preset_buttons.items():
            btn.setChecked(match is not None and key == match.key)
        self.preset_blurb.setText(match.blurb if match else
                                  "Custom settings. Check the numbers under the "
                                  "preview before you commit to a batch.")

    def _apply_preset(self, key: str) -> None:
        preset = PRESETS_BY_KEY[key]
        keep = (self.settings.credit, self.settings.copyright_notice,
                self.settings.write_optout_metadata, self.settings.output_format,
                self.settings.jpeg_quality, self.settings.seed)
        self.settings = preset.to_settings(self.settings)
        (self.settings.credit, self.settings.copyright_notice,
         self.settings.write_optout_metadata, self.settings.output_format,
         self.settings.jpeg_quality, self.settings.seed) = keep
        self._sync_controls_from_settings()
        self._schedule_preview()

    def _on_strength(self, value: int) -> None:
        self.settings.strength = value / 100.0
        self.strength_value.setText(f"{value}%")
        self._update_preset_selection()
        self._schedule_preview()

    def _on_pass_toggled(self, name: str, on: bool) -> None:
        self.settings.enabled[name] = on
        self._update_preset_selection()
        self._schedule_preview()

    def _on_harden(self, on: bool) -> None:
        self.settings.harden = on
        self._update_preset_selection()

    def _on_optout(self, on: bool) -> None:
        self.settings.write_optout_metadata = on

    def _on_format(self, _index: int) -> None:
        key = self.format_combo.currentData()
        self.settings.output_format = key
        lossy = key in ("jpeg", "webp")
        self.quality_spin.setEnabled(lossy or key == "source")
        self.format_warning.setText(
            "JPEG re-encodes on save, which costs some protection. Keep "
            "hardening on, or export PNG." if key == "jpeg" else "")

    # -------------------------------------------------------------- files

    def add_images(self) -> None:
        patterns = " ".join(f"*{s}" for s in sorted(imaging.SUPPORTED_SUFFIXES))
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add images", "", f"Images ({patterns});;All files (*)")
        self._add_paths([Path(f) for f in files])

    def _add_paths(self, paths: list[Path]) -> None:
        added = 0
        for path in paths:
            if path.is_dir():
                found = sorted(q for q in path.rglob("*")
                               if q.suffix.lower() in imaging.SUPPORTED_SUFFIXES)
                added += self._append(found)
            elif path.suffix.lower() in imaging.SUPPORTED_SUFFIXES:
                added += self._append([path])
        if added and self.file_list.currentRow() < 0:
            self.file_list.setCurrentRow(0)
        self._update_actions()
        if added:
            self.status.setText(f"{len(self.paths)} image(s) ready.")

    def _append(self, paths: list[Path]) -> int:
        count = 0
        for path in paths:
            resolved = path.resolve()
            if resolved in self.paths:
                continue
            self.paths.append(resolved)
            item = QListWidgetItem(resolved.name)
            item.setToolTip(str(resolved))
            self.file_list.addItem(item)
            count += 1
        return count

    def remove_selected(self) -> None:
        row = self.file_list.currentRow()
        if row < 0:
            return
        self.file_list.takeItem(row)
        self.paths.pop(row)
        if not self.paths:
            self.current_image = None
            self.current_preview_rgb = None
            self.canvas.set_images(None, None)
            self._set_metrics(None)
        self._update_actions()

    def _on_file_selected(self, row: int) -> None:
        if row < 0 or row >= len(self.paths):
            return
        self._token += 1
        token = self._token
        self.canvas.set_placeholder("Loading...")
        self.canvas.set_images(None, None)
        worker = LoadWorker(token, self.paths[row], self)
        worker.loaded.connect(self._on_loaded)
        worker.failed.connect(self._on_worker_failed)
        self._threads.add(worker)
        worker.start()

    @Slot(int, object, object)
    def _on_loaded(self, token: int, img, small) -> None:
        if token != self._token:
            return
        self.current_image = img
        self.current_preview_rgb = small
        self.canvas.set_images(small, None)
        w, h = img.size
        self.status.setText(f"{img.path.name} - {w}x{h} ({img.megapixels:.1f} MP)")
        self._schedule_preview()

    @Slot(int, str)
    def _on_worker_failed(self, token: int, message: str) -> None:
        if token == self._token:
            self.canvas.set_placeholder(message)
            self.status.setText(message)

    # ------------------------------------------------------------ preview

    def _schedule_preview(self) -> None:
        if self.current_preview_rgb is not None:
            self._debounce.start()

    def _render_preview(self) -> None:
        if self.current_preview_rgb is None:
            return
        self._token += 1
        token = self._token
        if self._preview_worker is not None:
            self._preview_worker.requestInterruption()
        worker = PreviewWorker(token, self.current_preview_rgb, self.settings, self)
        worker.finished_preview.connect(self._on_preview_ready)
        worker.failed.connect(self._on_worker_failed)
        self._preview_worker = worker
        self._threads.add(worker)
        worker.start()

    @Slot(int, object, object)
    def _on_preview_ready(self, token: int, rgb, metrics) -> None:
        if token != self._token:
            return                      # a newer request has already superseded this
        self.canvas.set_images(self.current_preview_rgb, rgb)
        self._set_metrics(metrics)

    def _set_metrics(self, metrics) -> None:
        while self.metrics_grid.count():
            item = self.metrics_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if metrics is None:
            label = QLabel("Quality and durability figures appear here once a "
                           "preview has been rendered.")
            label.setObjectName("Subtle")
            label.setWordWrap(True)
            self.metrics_grid.addWidget(label, 0, 0, 1, 4)
            return
        rows = metrics.as_rows()
        for i, (name, value) in enumerate(rows):
            col = (i % 2) * 2
            row = i // 2
            key = QLabel(name)
            key.setObjectName("Subtle")
            val = QLabel(value)
            if name == "Visible change":
                good = metrics.psnr >= 36
                val.setStyleSheet(f"color: {theme.GOOD if good else theme.WARN};")
            if name == "Durability":
                good = metrics.survival >= 0.5
                val.setStyleSheet(f"color: {theme.GOOD if good else theme.WARN};")
            self.metrics_grid.addWidget(key, row, col)
            self.metrics_grid.addWidget(val, row, col + 1)
        note = QLabel("Durability is measured against re-encodes the hardening never saw "
                      "(JPEG q85/q60, WebP, downscaling). Figures are from the "
                      "preview-sized copy; the saved file is "
                      "processed at full resolution with hardening applied.")
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        self.metrics_grid.addWidget(note, len(rows) // 2 + 1, 0, 1, 4)

    # -------------------------------------------------------------- batch

    def choose_output(self) -> None:
        start = str(self.out_dir or (self.paths[0].parent if self.paths else Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder", start)
        if folder:
            self.out_dir = Path(folder)
            self.out_label.setText(str(self.out_dir))

    def _resolve_out_dir(self) -> Path:
        if self.out_dir is not None:
            return self.out_dir
        return self.paths[0].parent / "Protected"

    def run_batch(self) -> None:
        if not self.paths or self._batch is not None:
            return
        out_dir = self._resolve_out_dir()
        clashes = [p for p in self.paths if p.parent == out_dir]
        if clashes and not self.settings.filename_suffix:
            QMessageBox.warning(self, APP_NAME,
                                "The output folder is the same as the source and "
                                "no filename suffix is set - that would overwrite "
                                "your originals. Choose another folder.")
            return

        self._batch = BatchWorker(list(self.paths), out_dir, self.settings, self)
        self._batch.file_started.connect(self._on_batch_file_started)
        self._batch.file_progress.connect(self._on_batch_progress)
        self._batch.all_done.connect(self._on_batch_done)
        self._threads.add(self._batch)

        self.progress.setValue(0)
        self.progress.show()
        self.cancel_btn.show()
        self._batch_total = len(self.paths)
        self._batch_index = 0
        self._update_actions()
        self._batch.start()

    @Slot(int, str)
    def _on_batch_file_started(self, index: int, name: str) -> None:
        self._batch_index = index
        self.status.setText(f"[{index + 1}/{self._batch_total}] {name}")

    @Slot(float, str)
    def _on_batch_progress(self, frac: float, stage: str) -> None:
        overall = (self._batch_index + frac) / max(self._batch_total, 1)
        self.progress.setValue(int(overall * 1000))
        name = self.paths[min(self._batch_index, len(self.paths) - 1)].name
        self.status.setText(
            f"[{self._batch_index + 1}/{self._batch_total}] {name} - {stage}")

    @Slot(list, bool)
    def _on_batch_done(self, outcomes: list, cancelled: bool) -> None:
        self._batch = None
        self.progress.hide()
        self.cancel_btn.hide()
        self._update_actions()

        ok = [o for o in outcomes if o.ok]
        failed = [o for o in outcomes if not o.ok]
        out_dir = self._resolve_out_dir()

        if cancelled:
            self.status.setText(f"Cancelled. {len(ok)} image(s) were saved.")
        else:
            self.status.setText(f"Saved {len(ok)} image(s) to {out_dir}")

        if failed:
            detail = "\n".join(f"{o.path.name}: {o.error}" for o in failed[:8])
            QMessageBox.warning(self, APP_NAME,
                                f"{len(failed)} image(s) could not be "
                                f"processed:\n\n{detail}")
        elif ok and not cancelled:
            worst = min(o.metrics.survival for o in ok if o.metrics)
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setText(f"Protected {len(ok)} image(s).")
            box.setInformativeText(
                f"Saved to {out_dir}\n\n"
                f"Lowest durability in this batch: {worst * 100:.0f}%.\n\n"
                "Keep your originals. Upload only the protected copies.")
            open_btn = box.addButton("Open folder", QMessageBox.ActionRole)
            box.addButton(QMessageBox.Close)
            box.exec()
            if box.clickedButton() is open_btn:
                QDesktopServices.openUrl(
                    __import__("PySide6.QtCore", fromlist=["QUrl"])
                    .QUrl.fromLocalFile(str(out_dir)))

    def _cancel_batch(self) -> None:
        if self._batch is not None:
            self._batch.cancel()
            self.status.setText("Finishing the current image, then stopping...")

    def _update_actions(self) -> None:
        busy = self._batch is not None
        self.protect_btn.setEnabled(bool(self.paths) and not busy)
        self.protect_btn.setText(
            "Protecting..." if busy else
            f"Protect and save ({len(self.paths)})" if len(self.paths) > 1 else
            "Protect and save")

    # ------------------------------------------------------- window events

    def dragEnterEvent(self, event):  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls()
                 if u.isLocalFile()]
        if paths:
            self._add_paths(paths)
            event.acceptProposedAction()

    def show_about(self) -> None:
        QMessageBox.information(
            self, f"About {APP_NAME}",
            "<b>What Mistcoat does</b>"
            "<p>It makes small, mostly invisible changes to an image that "
            "degrade its value as AI training data: it disrupts the fine "
            "texture statistics models learn from, scrambles the local stroke "
            "patterns that make up your style, perturbs colour where the eye "
            "cannot follow, and writes the perturbation into the parts of the "
            "file that survive being re-compressed.</p>"
            "<p>It also embeds the IPTC/PLUS <i>do not mine</i> metadata that "
            "states, in a machine-readable way, that the work is not licensed "
            "for AI training.</p>"
            "<b>What it does not do</b>"
            "<p>It cannot make an image impossible to train on. A determined "
            "party with enough compute can reduce or defeat any protection of "
            "this kind, and published research has broken every tool in this "
            "category to some degree. Treat this as raising the cost and "
            "spoiling the result, not as a lock.</p>"
            "<p><b>Always keep your unprotected originals</b>, and upload only "
            "the protected copies.</p>")

    def closeEvent(self, event):  # noqa: N802
        self._debounce.stop()
        self._threads.stop_all()
        super().closeEvent(event)
