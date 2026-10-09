# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Help › Manual: the user manual with a table of contents and search."""
from __future__ import annotations

import html
import re
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextDocument
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSplitter, QTextBrowser, QVBoxLayout)

from platesolver import __version__

MANUAL = Path(__file__).resolve().parents[1] / "help" / "manual.html"


def _default_text(f) -> str:
    from platesolver.core.settings import ACTION, BOOL, CHOICE
    v = f.default
    if f.type == ACTION:
        return "(button)"
    if f.type == BOOL:
        return "On" if v else "Off"
    if f.type == CHOICE:
        return next((label for val, label in f.choices if val == v), str(v))
    if v in (None, ""):
        return "(empty)"
    if isinstance(v, float):
        text = f"{v:.{f.decimals}f}".rstrip("0").rstrip(".") if f.decimals else f"{v:.0f}"
        return text + f.suffix
    return f"{v}{f.suffix}"


def settings_reference(sections) -> str:
    """HTML describing every settings page, generated from the pages' own field descriptions."""
    from platesolver.core.plugin import Plugin
    out = []
    for sec in sections:
        sid = "settings-" + sec.section_id.replace(".", "-")
        where = f"{sec.kind_label} › {sec.name}" if isinstance(sec, Plugin) else sec.name
        out.append(f'<h3 id="{sid}"><a name="{sid}"></a>{html.escape(where)}</h3>')
        if sec.description:
            out.append(f"<p>{html.escape(sec.description)}</p>")
        rows = []
        if isinstance(sec, Plugin):
            rows.append(("Enabled", "On" if sec.enabled_by_default else "Off",
                         "Switches this module on or off."))
        try:
            schema = sec.settings_schema()
        except Exception:
            schema = []
        for f in schema:
            extra = ""
            if f.type == "choice" and len(f.choices) > 1:
                extra = " Choices: " + "; ".join(label for _, label in f.choices) + "."
            rows.append((f.label, _default_text(f), (f.help or "") + extra))
        if rows:
            out.append("<table><tr><th>Setting</th><th>Default</th><th>What it does</th></tr>" + "".join(
                f"<tr><td>{html.escape(a)}</td><td>{html.escape(b)}</td><td>{html.escape(c)}</td></tr>"
                for a, b, c in rows) + "</table>")
    return "\n".join(out)


def manual_html(sections=()) -> str:
    try:
        text = MANUAL.read_text(encoding="utf-8")
    except OSError:
        return "<h1>Manual not found</h1><p>The file help/manual.html is missing from the installation.</p>"
    return (text.replace("{{VERSION}}", __version__)
            .replace("{{SETTINGS_REFERENCE}}", settings_reference(sections) if sections else
                     "<p>(Open the manual from PlateSolver to see the settings reference.)</p>"))


def write_manual_file(sections) -> Path:
    """A complete copy for reading in a web browser (settings reference included)."""
    from platesolver.core import paths
    out = paths.app_data_dir() / "PlateSolver manual.html"
    out.write_text(manual_html(sections), encoding="utf-8")
    return out


class HelpWindow(QDialog):
    def __init__(self, parent=None, anchor: str = "", sections=()):
        super().__init__(parent)
        self.setWindowTitle("PlateSolver manual")
        self.resize(1100, 780)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.sections = list(sections)
        html = manual_html(self.sections)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.document().setDefaultStyleSheet(
            "h1 { color:#e6e9ef; } h2 { color:#5aa9ff; margin-top:22px; } h3 { color:#9ec9ff; margin-top:14px; }"
            "td, th { padding:3px 8px; } th { background:#232830; text-align:left; }"
            "code, kbd { background:#232830; font-family: Consolas, monospace; }"
            ".tip { color:#6fd08c; } .warn { color:#ffb347; } .muted { color:#8b93a1; }")
        # the file's own <style> is for web browsers (light page); inside the app use the dark theme
        self.browser.setHtml(re.sub(r"<style>.*?</style>", "", html, flags=re.S))

        self.toc = QListWidget()
        self.toc.setMaximumWidth(290)
        for level, anchor_id, title in re.findall(r'<h([23]) id="([^"]+)">(.*?)</h\1>', html, re.S):
            if anchor_id.startswith("settings-") and level == "3":
                title = "  " + re.sub(r"<[^>]+>", "", title).split("›")[-1]
            title = re.sub(r"<[^>]+>", "", title).strip()
            item = QListWidgetItem(("    " if level == "3" else "") + title)
            item.setData(Qt.UserRole, anchor_id)
            if level == "2":
                f = item.font()
                f.setBold(True)
                item.setFont(f)
            self.toc.addItem(item)
        self.toc.itemClicked.connect(lambda it: self.show_anchor(it.data(Qt.UserRole)))

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the manual…  (Enter = next match)")
        self.search.returnPressed.connect(self.find_next)
        self.search.textChanged.connect(lambda _: self.find_next(from_start=True))
        self.found = QLabel("")
        self.found.setObjectName("muted")
        browser_btn = QPushButton("Open in web browser")
        browser_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(write_manual_file(self.sections)))))
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.found)
        top.addWidget(browser_btn)

        split = QSplitter()
        split.addWidget(self.toc)
        split.addWidget(self.browser)
        split.setStretchFactor(1, 1)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(split, 1)
        if anchor:
            self.show_anchor(anchor)

    def show_anchor(self, anchor: str):
        self.browser.scrollToAnchor(anchor)

    def find_next(self, from_start: bool = False):
        text = self.search.text().strip()
        if not text:
            self.found.setText("")
            return
        if from_start:
            cur = self.browser.textCursor()
            cur.movePosition(cur.MoveOperation.Start)
            self.browser.setTextCursor(cur)
        if not self.browser.find(text):
            cur = self.browser.textCursor()
            cur.movePosition(cur.MoveOperation.Start)
            self.browser.setTextCursor(cur)
            ok = self.browser.find(text)
            self.found.setText("" if ok else "Not found")
        else:
            self.found.setText("")
