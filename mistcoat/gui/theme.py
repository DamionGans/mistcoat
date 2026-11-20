"""Platform look.

Mistcoat uses each OS's own widgets instead of a custom skin:

* Windows: the Windows 11 style (Fluent controls, system accent colour).
* macOS:   the native Aqua style (system controls, accent colour, menu bar).
* Linux:   KDE's Breeze style when it is loadable, otherwise Fusion with the
           desktop's palette, which is what Qt apps on GNOME get.

All three follow the system's light/dark setting live. There is deliberately
no application style sheet: Qt draws every widget a style sheet touches with
its own generic renderer, which is what makes Qt apps look foreign.

The one thing kept constant is the preview canvas: a neutral grey with no hue,
so the window never tints the artist's judgement of colour.
"""
from __future__ import annotations

import re
import sys

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import (QApplication, QLabel, QPushButton, QStyle,
                               QStyleFactory, QWidget)

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")
IS_LINUX = sys.platform.startswith("linux")

ELLIPSIS = "…"


# ---------------------------------------------------------------- the style

def native_style_name() -> str | None:
    available = {k.lower(): k for k in QStyleFactory.keys()}
    if IS_MAC:
        wanted = ["macos"]
    elif IS_WIN:
        wanted = ["windows11", "windowsvista"]
    else:
        wanted = ["breeze", "fusion"]
    for name in wanted:
        if name in available:
            return available[name]
    return None


class _SchemeWatcher(QObject):
    """Fires after the OS switches light/dark and our palette has caught up,
    so custom-painted widgets can repaint."""
    changed = Signal()


watcher: _SchemeWatcher | None = None
_platform_palette: QPalette | None = None


def apply(app: QApplication) -> None:
    global watcher, _platform_palette
    name = native_style_name()
    if name:
        app.setStyle(name)
    _platform_palette = QPalette(app.palette())
    watcher = _SchemeWatcher(app)
    _sync_palette(app)
    hints = QGuiApplication.styleHints()
    if hasattr(hints, "colorSchemeChanged"):
        hints.colorSchemeChanged.connect(lambda *_: _on_scheme_changed(app))


def _on_scheme_changed(app: QApplication) -> None:
    global _platform_palette
    if IS_WIN:
        # The Windows 11 style caches some indicator colours; re-applying it
        # makes a live light/dark switch repaint check marks correctly.
        name = native_style_name()
        if name:
            app.setStyle(name)
    if IS_LINUX:
        # Drop our override so we see what the desktop now supplies.
        app.setPalette(QPalette())
        _platform_palette = QPalette(QGuiApplication.palette())
    _sync_palette(app)
    if watcher is not None:
        watcher.changed.emit()


def _sync_palette(app: QApplication) -> None:
    """On Linux, Qt learns about the desktop's dark mode (from the GTK theme
    or the XDG portal) but not every desktop hands Fusion a dark palette to
    go with it. Fill that gap. On Windows and macOS the native style already
    switches by itself."""
    if not IS_LINUX:
        return
    base = _platform_palette or app.palette()
    window_is_dark = base.color(QPalette.Window).lightness() < 128
    if scheme_is_dark() and not window_is_dark:
        app.setPalette(_dark_palette(base))


def scheme_is_dark() -> bool:
    hints = QGuiApplication.styleHints()
    scheme = getattr(hints, "colorScheme", lambda: None)()
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    return QGuiApplication.palette().color(QPalette.Window).lightness() < 128


def _dark_palette(base: QPalette) -> QPalette:
    """A neutral dark palette (close to Adwaita dark) keeping the desktop's
    accent colour."""
    p = QPalette()
    roles = {
        QPalette.Window: "#242424", QPalette.WindowText: "#ffffff",
        QPalette.Base: "#1e1e1e", QPalette.AlternateBase: "#2a2a2a",
        QPalette.Text: "#ffffff", QPalette.Button: "#303030",
        QPalette.ButtonText: "#ffffff", QPalette.ToolTipBase: "#383838",
        QPalette.ToolTipText: "#ffffff", QPalette.BrightText: "#ff7b63",
        QPalette.Light: "#3c3c3c", QPalette.Midlight: "#343434",
        QPalette.Mid: "#2c2c2c", QPalette.Dark: "#1a1a1a",
        QPalette.Shadow: "#000000", QPalette.Link: "#78aeed",
        QPalette.LinkVisited: "#c19af0", QPalette.PlaceholderText: "#9a9a9a",
    }
    for role, colour in roles.items():
        p.setColor(role, QColor(colour))
    p.setColor(QPalette.Highlight, base.color(QPalette.Highlight))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#6e6e6e"))
    return p


# ------------------------------------------------------- semantic helpers

def secondary(label: QLabel) -> QLabel:
    """Secondary text in the platform's own dimmed text colour."""
    label.setForegroundRole(QPalette.PlaceholderText)
    return label


def caption(label: QLabel) -> QLabel:
    """Small secondary text, for footnotes."""
    f = QFont(label.font())
    if f.pointSizeF() > 0:
        f.setPointSizeF(max(7.5, f.pointSizeF() * 0.9))
    label.setFont(f)
    return secondary(label)


def heading(label: QLabel) -> QLabel:
    f = QFont(label.font())
    f.setWeight(QFont.DemiBold if IS_WIN else QFont.Bold)
    label.setFont(f)
    return label


def status_colour(good: bool) -> QColor:
    """Pass/warn colours legible on both light and dark windows."""
    if scheme_is_dark():
        return QColor("#6fcf7f" if good else "#f0b45c")
    return QColor("#1b7f33" if good else "#a85b00")


def tint(label: QLabel, colour: QColor) -> QLabel:
    pal = label.palette()
    pal.setColor(QPalette.WindowText, colour)
    label.setPalette(pal)
    return label


def canvas_colour() -> QColor:
    """Neutral grey, no hue, in either mode."""
    return QColor("#1c1c1c" if scheme_is_dark() else "#dcdcdc")


def canvas_text_colour() -> QColor:
    return QColor("#9a9a9a" if scheme_is_dark() else "#5c5c5c")


def accent() -> QColor:
    pal = QGuiApplication.palette()
    return pal.color(getattr(QPalette, "Accent", QPalette.Highlight))


def primary(button: QPushButton) -> QPushButton:
    """The window's main action, drawn the platform's way.

    macOS: the default button (the blue one). Windows 11: an accent button,
    filled with the system accent colour. GNOME/KDE: a "suggested action",
    also accent-filled. Call again after a light/dark switch.
    """
    button.setDefault(True)
    if IS_MAC:
        return button
    pal = QPalette(QGuiApplication.palette())
    fill = accent()
    # Dark text on light accents (Windows' dark-mode accent is a pale tint).
    luma = 0.2126 * fill.redF() + 0.7152 * fill.greenF() + 0.0722 * fill.blueF()
    text = QColor("#000000" if luma > 0.5 else "#ffffff")
    for group in (QPalette.Active, QPalette.Inactive):   # disabled stays native
        pal.setColor(group, QPalette.Button, fill)
        pal.setColor(group, QPalette.ButtonText, text)
    button.setPalette(pal)
    return button


# ------------------------------------------------------------ wording

_SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "nor",
                "of", "on", "or", "the", "to", "up", "via"}


def command(text: str) -> str:
    """Text for buttons and menu items, in the platform's convention.

    macOS uses title case ("Protect and Save"). Windows, GNOME and KDE use
    sentence case, which is how the strings are written in the code.
    """
    text = text.replace("...", ELLIPSIS)
    if not IS_MAC:
        return text
    words = text.split(" ")
    out = []
    for i, word in enumerate(words):
        lead = len(word) - len(re.sub(r"^[&(]+", "", word))
        bare = word[lead:]
        small = bare.lower().replace("&", "") in _SMALL_WORDS
        if bare and bare[0].islower() and not (small and 0 < i < len(words) - 1):
            out.append(word[:lead] + bare[0].upper() + bare[1:])
        else:
            out.append(word)
    return " ".join(out)


def file_manager_action() -> str:
    if IS_MAC:
        return "Show in Finder"
    if IS_WIN:
        return "Open in File Explorer"
    return "Open folder"


def window_title(document: str | None, app_name: str) -> str:
    if not document:
        return app_name
    if IS_MAC:
        return document           # macOS titles show the document alone
    sep = " — " if IS_LINUX else " - "
    return f"{document}{sep}{app_name}"


def indicator_indent(widget: QWidget) -> int:
    """Left inset that lines text up with a check box's label."""
    st = widget.style()
    return (st.pixelMetric(QStyle.PM_IndicatorWidth, None, widget)
            + st.pixelMetric(QStyle.PM_CheckBoxLabelSpacing, None, widget))
