# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Dark theme - easy on the eyes at night."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#5aa9ff"
OK = "#6fd08c"
WARN = "#ffb347"
ERROR = "#ff6b6b"
MUTED = "#8b93a1"

STYLE = f"""
QWidget {{ font-size: 10pt; }}
QToolBar {{ border: none; padding: 4px; spacing: 4px; background: #1b1f26; }}
QToolButton {{ padding: 5px 10px; border-radius: 5px; }}
QToolButton:hover {{ background: #2a303a; }}
QToolButton:disabled {{ color: #5a616d; }}
QStatusBar {{ background: #1b1f26; color: {MUTED}; }}
QTabWidget::pane {{ border: 1px solid #2a303a; border-radius: 4px; top: -1px; }}
QTabBar::tab {{ padding: 6px 14px; background: transparent; color: {MUTED}; border: none; }}
QTabBar::tab:selected {{ color: #e6e9ef; border-bottom: 2px solid {ACCENT}; }}
QGroupBox {{ border: 1px solid #2a303a; border-radius: 6px; margin-top: 14px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {MUTED}; }}
QListWidget {{ border: 1px solid #2a303a; border-radius: 4px; }}
QListWidget::item {{ padding: 5px 6px; }}
QListWidget::item:selected {{ background: #25405f; color: #ffffff; }}
QPlainTextEdit {{ font-family: Consolas, 'DejaVu Sans Mono', monospace; font-size: 9pt; }}
QLabel#h1 {{ font-size: 14pt; font-weight: 600; }}
QLabel#muted {{ color: {MUTED}; }}
QPushButton {{ padding: 5px 14px; }}
"""


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    bg, base, text = QColor("#14171c"), QColor("#1b1f26"), QColor("#e6e9ef")
    p.setColor(QPalette.Window, bg)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, QColor("#20252d"))
    p.setColor(QPalette.ToolTipBase, base)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, QColor("#232830"))
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.BrightText, QColor(ERROR))
    p.setColor(QPalette.Highlight, QColor("#25405f"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Link, QColor(ACCENT))
    p.setColor(QPalette.PlaceholderText, QColor(MUTED))
    for role in (QPalette.Text, QPalette.ButtonText, QPalette.WindowText):
        p.setColor(QPalette.Disabled, role, QColor("#5a616d"))
    app.setPalette(p)
    app.setStyleSheet(STYLE)
