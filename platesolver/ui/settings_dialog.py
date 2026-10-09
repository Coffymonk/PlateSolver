# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Settings dialog, built automatically from the General section and every plugin's schema."""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSizePolicy,
                               QSpinBox, QSplitter, QStackedWidget, QVBoxLayout, QWidget)

from platesolver.core.interfaces import ALL_KINDS
from platesolver.core.plugin import Plugin
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import (ACTION, BOOL, CHOICE, FILE, FLOAT, FOLDER, INT, SectionSettings,
                                       SettingField, SettingsSection, SettingsStore)
from platesolver.ui import theme


class FieldEditor:
    """One input widget for a SettingField, plus its live validation message."""

    def __init__(self, field: SettingField, value: Any, parent: QWidget):
        self.field = field
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.status.setVisible(False)
        self.values_getter: Callable[[], dict] = lambda: {}
        self.after_action: Callable[[], None] = lambda: None
        self._worker = None
        self.widget, self._get, self._set, changed = self._build(field, parent)
        self.on_change = changed
        self.initial = value
        self._set(value)
        if field.type == ACTION and field.status:
            self._show(True, field.status())
        if field.validator:
            changed(self.validate)
            self.validate()

    def _build(self, f: SettingField, parent) -> tuple[QWidget, Callable, Callable, Callable]:
        if f.type == ACTION:
            btn = QPushButton(f.label, parent)
            btn.clicked.connect(self._run_action)
            return btn, lambda: None, lambda v: None, lambda slot: None
        if f.type == BOOL:
            w = QCheckBox(f.label, parent)
            return w, w.isChecked, lambda v: w.setChecked(bool(v)), w.toggled.connect
        if f.type == INT:
            w = QSpinBox(parent)
            w.setRange(int(f.minimum if f.minimum is not None else -10**9),
                       int(f.maximum if f.maximum is not None else 10**9))
            w.setSingleStep(int(f.step or 1))
            w.setSuffix(f.suffix)
            return w, w.value, lambda v: w.setValue(int(v or 0)), w.valueChanged.connect
        if f.type == FLOAT:
            w = QDoubleSpinBox(parent)
            w.setDecimals(f.decimals)
            w.setRange(f.minimum if f.minimum is not None else -1e12,
                       f.maximum if f.maximum is not None else 1e12)
            w.setSingleStep(f.step or 0.1)
            w.setSuffix(f.suffix)
            return w, w.value, lambda v: w.setValue(float(v or 0)), w.valueChanged.connect
        if f.type == CHOICE:
            w = QComboBox(parent)
            for val, label in f.choices:
                w.addItem(label, val)
            self._combo = w

            def set_choice(v):
                i = w.findData(v)
                if i < 0 and v not in (None, ""):
                    w.addItem(str(v), v)
                    i = w.count() - 1
                w.setCurrentIndex(max(i, 0))
            return w, w.currentData, set_choice, w.currentIndexChanged.connect
        # text, file and folder
        edit = QLineEdit(parent)
        edit.setClearButtonEnabled(True)
        if f.type in (FILE, FOLDER):
            box = QWidget(parent)
            lay = QHBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(edit, 1)
            btn = QPushButton("Browse…", box)
            lay.addWidget(btn)

            def browse():
                start = edit.text().strip()
                if f.type == FOLDER:
                    path = QFileDialog.getExistingDirectory(box, f.label, start)
                else:
                    path, _ = QFileDialog.getOpenFileName(box, f.label, start, f.file_filter)
                if path:
                    from pathlib import Path
                    edit.setText(str(Path(path)))
            btn.clicked.connect(browse)
            return box, lambda: edit.text().strip(), lambda v: edit.setText(str(v or "")), edit.textChanged.connect
        return edit, edit.text, lambda v: edit.setText(str(v or "")), edit.textChanged.connect

    def value(self):
        return self._get()

    def _show(self, ok: bool, msg: str, busy: bool = False):
        color = theme.MUTED if busy else (theme.OK if ok else theme.ERROR)
        mark = "…" if busy else ("✓" if ok else "✗")
        self.status.setText(f"<span style='color:{color}'>{mark} {msg}</span>")
        self.status.setVisible(bool(msg))

    def _run_action(self):
        from platesolver.ui.workers import TaskWorker
        f = self.field
        if f.action is None or self._worker is not None:
            return
        values = self.values_getter()
        if f.ui:                       # opens its own dialogs, so it runs here in the window
            try:
                msg = f.action(values, self.widget.window())
                if msg:
                    self._show(True, str(msg))
            except Exception as exc:
                self._show(False, str(exc))
            self.after_action()
            return
        self.widget.setEnabled(False)
        self._show(True, "Working…", busy=True)
        worker = TaskWorker(lambda ctx: f.action(values, ctx.log), self.widget)
        worker.message.connect(lambda m: self._show(True, m, busy=True))
        worker.succeeded.connect(lambda msg: self._action_done(True, str(msg or "Done")))
        worker.failed.connect(lambda msg: self._action_done(False, msg))
        self._worker = worker
        worker.start()

    def set_choices(self, choices):
        """New choices for a list (e.g. after a quiz was created), keeping the current one."""
        combo = getattr(self, "_combo", None)
        if combo is None:
            return
        current = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for val, label in choices:
            combo.addItem(label, val)
        i = combo.findData(current)
        combo.setCurrentIndex(max(i, 0))
        combo.blockSignals(False)

    def _action_done(self, ok: bool, msg: str):
        self.widget.setEnabled(True)
        self._worker = None
        self._show(ok, msg)

    def set_value(self, v):
        self._set(v)

    def validate(self, *_):
        ok, msg = self.field.validator(self.value())
        color = theme.OK if ok else theme.ERROR
        self.status.setText(f"<span style='color:{color}'>{'✓' if ok else '✗'} {msg}</span>")
        self.status.setVisible(bool(msg))


class SectionPage(QScrollArea):
    """The page for one section (General or a plugin)."""

    def __init__(self, section: SettingsSection, settings: SectionSettings, parent=None):
        super().__init__(parent)
        self.section = section
        self.settings = settings
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        self.setWidget(inner)
        v = QVBoxLayout(inner)
        v.setContentsMargins(16, 12, 16, 12)

        title = QLabel(section.name)
        title.setObjectName("h1")
        v.addWidget(title)
        if section.description:
            d = QLabel(section.description)
            d.setObjectName("muted")
            d.setWordWrap(True)
            v.addWidget(d)

        self.enabled_box: QCheckBox | None = None
        if isinstance(section, Plugin):
            self.enabled_box = QCheckBox("Enabled")
            self.enabled_box.setChecked(section.enabled)
            v.addWidget(self.enabled_box)
            ok, why = section.is_available()
            if not ok:
                warn = QLabel(f"<span style='color:{theme.WARN}'>⚠ {why}</span>")
                warn.setWordWrap(True)
                v.addWidget(warn)

        self.editors: list[FieldEditor] = []
        parts: dict[str, list] = {}           # field key -> its widgets (label, editor, help), for enabled_by
        schema = section.settings_schema()
        if schema:
            v.addSpacing(6)
            for field in schema:
                ed = FieldEditor(field, None if field.type == ACTION else settings.get(field.key), inner)
                ed.values_getter = lambda: {e.field.key: e.value() for e in self.editors if e.field.type != ACTION}
                ed.after_action = self._refresh_choices
                self.editors.append(ed)
                own = parts.setdefault(field.key, [ed.widget, ed.status])
                if field.help:
                    ed.widget.setToolTip(field.help)
                if field.type not in (BOOL, ACTION):
                    lab = QLabel(field.label)
                    lab.setStyleSheet("font-weight:600")
                    v.addWidget(lab)
                    own.append(lab)
                if field.type in (INT, FLOAT, CHOICE, ACTION):
                    row = QHBoxLayout()
                    ed.widget.setMinimumWidth(200 if field.type == CHOICE else 140)
                    row.addWidget(ed.widget)
                    row.addStretch(1)
                    v.addLayout(row)
                else:
                    v.addWidget(ed.widget)
                v.addWidget(ed.status)
                if field.help:
                    hl = QLabel(field.help)
                    hl.setObjectName("muted")
                    hl.setWordWrap(True)
                    v.addWidget(hl)
                    own.append(hl)
                v.addSpacing(10)
            by_key = {e.field.key: e for e in self.editors}
            for ed in self.editors:
                master = by_key.get(ed.field.enabled_by) if ed.field.enabled_by else None
                if master is None:
                    continue
                widgets = parts.get(ed.field.key, [])

                def follow(_=None, m=master, ws=widgets):
                    on = bool(m.value())
                    for w in ws:
                        w.setEnabled(on)
                master.on_change(follow)
                follow()
        elif not isinstance(section, Plugin):
            v.addWidget(QLabel("No settings."))
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(f"color:{theme.ACCENT}; font-weight:600; padding:6px 0")
        v.addWidget(self.summary)
        for ed in self.editors:
            ed.on_change(self._refresh_summary)
        self._refresh_summary()
        v.addStretch(1)

    def _refresh_choices(self):
        """After a button's dialog: new list entries, and values the dialog stored (e.g. the ASTAP path)."""
        fresh = {f.key: f for f in self.section.settings_schema()}
        for ed in self.editors:
            if ed.field.type == ACTION:
                continue
            if ed.field.type == CHOICE and ed.field.key in fresh:
                ed.set_choices(fresh[ed.field.key].choices)
            stored = self.settings.get(ed.field.key)
            if stored != ed.initial and ed.value() == ed.initial:     # changed by the dialog, not by the user
                ed.set_value(stored)
                ed.initial = stored
                if ed.field.validator:
                    ed.validate()
        self._refresh_summary()

    def _refresh_summary(self, *_):
        text = self.section.describe({ed.field.key: ed.value() for ed in self.editors})
        self.summary.setText(text)
        self.summary.setVisible(bool(text))

    def apply(self):
        if self.enabled_box is not None:
            self.settings.set(Plugin.ENABLED_KEY, self.enabled_box.isChecked())
        for ed in self.editors:
            if ed.field.type != ACTION:
                self.settings.set(ed.field.key, ed.value())

    def restore_defaults(self):
        for ed in self.editors:
            if ed.field.type != ACTION:
                ed.set_value(ed.field.default)
        if self.enabled_box is not None and isinstance(self.section, Plugin):
            self.enabled_box.setChecked(self.section.enabled_by_default)


class SettingsDialog(QDialog):
    help_requested = Signal(str)   # manual anchor

    def __init__(self, store: SettingsStore, general: SettingsSection | list[SettingsSection],
                 registry: PluginRegistry,
                 parent=None, open_section: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(900, 680)
        self.store = store
        self.pages: list[SectionPage] = []

        self.nav = QListWidget()
        self.nav.setMinimumWidth(200)
        self.nav.setMaximumWidth(260)
        self.stack = QStackedWidget()

        def add_header(text):
            item = QListWidgetItem(text.upper())
            item.setFlags(Qt.NoItemFlags)
            f = item.font()
            f.setPointSizeF(f.pointSizeF() * 0.8)
            f.setBold(True)
            item.setFont(f)
            item.setForeground(Qt.gray)
            self.nav.addItem(item)

        def add_page(section, indent=True):
            page = SectionPage(section, store.section(section) if not isinstance(section, Plugin)
                               else section.settings, self)
            self.pages.append(page)
            self.stack.addWidget(page)
            item = QListWidgetItem(("   " if indent else "") + section.name)
            item.setData(Qt.UserRole, len(self.pages) - 1)
            item.setData(Qt.UserRole + 1, section.section_id)
            self.nav.addItem(item)
            return item

        core_sections = general if isinstance(general, (list, tuple)) else [general]
        first = add_page(core_sections[0], indent=False)
        for extra in core_sections[1:]:
            add_page(extra, indent=False)
        for kind in ALL_KINDS:
            plugins = registry.of_kind(kind, enabled_only=False)
            if not plugins:
                continue
            add_header(kind.kind_label)
            for p in plugins:
                add_page(p)

        self.nav.currentItemChanged.connect(self._on_nav)
        target = first
        if open_section:
            for i in range(self.nav.count()):
                if self.nav.item(i).data(Qt.UserRole + 1) == open_section:
                    target = self.nav.item(i)
        self.nav.setCurrentItem(target)

        split = QSplitter()
        split.addWidget(self.nav)
        split.addWidget(self.stack)
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        restore = buttons.addButton("Restore defaults", QDialogButtonBox.ResetRole)
        restore.setToolTip("Restore the defaults on this page")
        restore.clicked.connect(lambda: self.stack.currentWidget().restore_defaults())
        help_btn = buttons.addButton("Help", QDialogButtonBox.HelpRole)
        help_btn.setToolTip("Explain the settings on this page (F1)")
        help_btn.clicked.connect(self._help)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addWidget(buttons)

    def _help(self):
        page = self.stack.currentWidget()
        self.help_requested.emit("settings-" + page.section.section_id.replace(".", "-"))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_F1:
            self._help()
            return
        super().keyPressEvent(event)

    def _on_nav(self, item, _prev):
        if item is not None and item.data(Qt.UserRole) is not None:
            self.stack.setCurrentIndex(item.data(Qt.UserRole))

    def accept(self):
        for page in self.pages:
            page.apply()
        self.store.save()
        super().accept()
