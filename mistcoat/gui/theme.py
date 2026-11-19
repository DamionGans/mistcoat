"""Dark theme. Neutral grey on purpose: the window sits next to the artwork and
must not tint the artist's judgement of colour."""
from __future__ import annotations

BG = "#1e1f22"
PANEL = "#26282c"
PANEL_HI = "#2f3238"
LINE = "#3a3d44"
TEXT = "#e4e6ea"
TEXT_DIM = "#9aa0a8"
ACCENT = "#5a9ad6"
ACCENT_DIM = "#3d6c99"
WARN = "#d6a35a"
GOOD = "#6fbf7f"
CANVAS = "#141518"

STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 13px;
}}
QLabel#Title {{ font-size: 19px; font-weight: 600; }}
QLabel#Subtle {{ color: {TEXT_DIM}; }}
QLabel#SectionHeading {{
    color: {TEXT_DIM};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    padding-top: 4px;
}}
QLabel#Disclaimer {{
    color: {TEXT_DIM};
    font-size: 11px;
    padding: 6px 10px;
    border-top: 1px solid {LINE};
}}
QFrame#Panel {{
    background: {PANEL};
    border: 1px solid {LINE};
    border-radius: 8px;
}}
QPushButton {{
    background: {PANEL_HI};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 6px 12px;
}}
QPushButton:hover {{ background: #373b42; }}
QPushButton:disabled {{ color: {TEXT_DIM}; background: #24262a; }}
QPushButton#Primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #10151b;
    font-weight: 600;
    padding: 9px 16px;
}}
QPushButton#Primary:hover {{ background: #6ba9e0; }}
QPushButton#Primary:disabled {{ background: {ACCENT_DIM}; color: #0d1115; }}
QPushButton#Preset {{ text-align: left; padding: 8px 10px; }}
QPushButton#Preset:checked {{
    background: {ACCENT_DIM};
    border: 1px solid {ACCENT};
}}
QListWidget, QPlainTextEdit, QLineEdit, QComboBox, QSpinBox {{
    background: {CANVAS};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 4px 6px;
    selection-background-color: {ACCENT_DIM};
}}
QListWidget::item {{ padding: 5px 4px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {ACCENT_DIM}; }}
QGroupBox {{
    border: 1px solid {LINE};
    border-radius: 8px;
    margin-top: 10px;
    padding-top: 10px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    color: {TEXT_DIM};
    font-weight: 700;
    font-size: 11px;
}}
QSlider::groove:horizontal {{
    height: 5px; background: {LINE}; border-radius: 3px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QSlider::handle:horizontal {{
    background: {TEXT}; width: 15px; margin: -6px 0; border-radius: 8px;
}}
QProgressBar {{
    background: {CANVAS}; border: 1px solid {LINE};
    border-radius: 6px; height: 7px; text-align: center;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QCheckBox {{ spacing: 8px; padding: 2px 0; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {LINE}; background: {CANVAS};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {LINE}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QToolTip {{
    background: {PANEL_HI}; color: {TEXT};
    border: 1px solid {LINE}; padding: 6px; border-radius: 4px;
}}
"""
