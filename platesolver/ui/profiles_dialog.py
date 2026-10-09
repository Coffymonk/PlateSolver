# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Tools › Profiles…: create, edit and choose equipment profiles."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QSplitter,
                               QVBoxLayout, QWidget)

from platesolver.core import cameras
from platesolver.core.profiles import KINDS, Profile, ProfileManager
from platesolver.ui.theme import ERROR, MUTED, OK, WARN

HEADER_ROLE = Qt.UserRole + 1


class ProfilesDialog(QDialog):
    def __init__(self, manager: ProfileManager, parent=None, select_id: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Profiles")
        self.manager = manager
        self.current: Profile | None = None
        self._loading = False

        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._selected)

        self.title = QLabel()
        self.title.setObjectName("h1")
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {MUTED};")
        self.name = QLineEdit()
        self.kind = QComboBox()
        for k, label in KINDS.items():
            self.kind.addItem(label, k)
        self.camera = QComboBox()
        self.camera.setMaxVisibleItems(25)
        self.focal = QDoubleSpinBox()
        self.focal.setRange(0, 20000)
        self.focal.setDecimals(1)
        self.focal.setSuffix(" mm")
        self.pixel = QDoubleSpinBox()
        self.pixel.setRange(0, 50)
        self.pixel.setDecimals(2)
        self.pixel.setSingleStep(0.01)
        self.pixel.setSuffix(" µm")
        self.sw, self.sh = QSpinBox(), QSpinBox()
        for sp in (self.sw, self.sh):
            sp.setRange(0, 20000)
            sp.setSuffix(" px")
        self.f35 = QDoubleSpinBox()
        self.f35.setRange(0, 2000)
        self.f35.setDecimals(0)
        self.f35.setSuffix(" mm")
        self.fov = QDoubleSpinBox()                 # photos of a screen: height of the sky shown (optional)
        self.fov.setRange(0, 90)
        self.fov.setDecimals(2)
        self.fov.setSingleStep(0.1)
        self.fov.setSuffix(" °")
        self.fov.setSpecialValueText("unknown")
        self.fov.setToolTip("How much sky the picture on the screen shows, top to bottom. Leave it at 'unknown' "
                            "if you're not sure – the scale is then found while solving.")
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.checks = QLabel()
        self.checks.setWordWrap(True)
        self.checks.setTextFormat(Qt.RichText)
        self.checks.setStyleSheet("QLabel { background: #1b1f26; border: 1px solid #2a303a; border-radius: 6px; "
                                  "padding: 8px; }")

        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Type", self.kind)
        form.addRow("Camera", self.camera)
        self.scope_rows = []
        for label, w in (("Telescope focal length", self.focal), ("Pixel size", self.pixel),
                         ("Sensor width", self.sw), ("Sensor height", self.sh)):
            form.addRow(label, w)
            self.scope_rows.append(w)
        form.addRow("Lens without EXIF (35 mm equiv.)", self.f35)
        form.addRow("Height of the sky shown (optional)", self.fov)
        self.form = form

        self.btn_copy = QPushButton("Make my own profile from this template")
        self.btn_copy.clicked.connect(self._copy_template)
        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self._save)
        self.btn_from_current = QPushButton("Take values from current settings")
        self.btn_from_current.setToolTip("Copy the values now in Settings › Equipment and ASTAP into this profile")
        self.btn_from_current.clicked.connect(self._from_current)
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.clicked.connect(self._delete)
        self.btn_use = QPushButton("Use this profile")
        self.btn_use.setDefault(True)
        self.btn_use.clicked.connect(self._use)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(self.title)
        rl.addWidget(self.note)
        rl.addLayout(form)
        rl.addWidget(self.summary)
        rl.addSpacing(6)
        rl.addWidget(self.checks)
        rl.addStretch(1)
        row = QHBoxLayout()
        for b in (self.btn_copy, self.btn_save, self.btn_from_current, self.btn_delete):
            row.addWidget(b)
        row.addStretch(1)
        rl.addLayout(row)

        split = QSplitter()
        split.addWidget(self.list)
        split.addWidget(right)
        split.setSizes([300, 640])
        foot = QHBoxLayout()
        foot.addStretch(1)
        foot.addWidget(self.btn_use)
        foot.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(split, 1)
        lay.addLayout(foot)
        self.resize(1000, 560)

        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.camera.currentIndexChanged.connect(self._camera_changed)
        for w in (self.focal, self.pixel, self.sw, self.sh, self.f35, self.fov):
            w.valueChanged.connect(self._update_summary)
        self._fill_list(select_id or manager.chosen_id)

    # ------------------------------------------------------------------ list
    def _header(self, text: str):
        it = QListWidgetItem(text)
        it.setFlags(Qt.NoItemFlags)
        f = QFont()
        f.setBold(True)
        it.setFont(f)
        it.setData(HEADER_ROLE, True)
        self.list.addItem(it)

    def _fill_list(self, select_id: str = ""):
        self.list.blockSignals(True)
        self.list.clear()
        users = self.manager.user_profiles()
        self._header("My profiles")
        if not users:
            it = QListWidgetItem("  (none yet – start from a template below)")
            it.setFlags(Qt.NoItemFlags)
            self.list.addItem(it)
        chosen = self.manager.chosen_id
        for p in users:
            it = QListWidgetItem(("✓ " if p.id == chosen else "   ") + p.name)
            it.setData(Qt.UserRole, p.id)
            self.list.addItem(it)
        self._header("Templates")
        for p in self.manager.profiles():
            if p.builtin:
                label = p.name.replace("Template: ", "")
                it = QListWidgetItem(("✓ " if p.id == chosen else "   ") + label[:1].upper() + label[1:])
                it.setData(Qt.UserRole, p.id)
                self.list.addItem(it)
        self.list.blockSignals(False)
        items = [self.list.item(i) for i in range(self.list.count()) if self.list.item(i).data(Qt.UserRole)]
        target = next((it for it in items if it.data(Qt.UserRole) == select_id), items[0] if items else None)
        if target:
            self.list.setCurrentItem(target)

    def _fill_cameras(self, kind: str, selected: str):
        self._loading = True
        self.camera.clear()
        self.camera.addItem("(none / enter values yourself)", "")
        wanted = {"astro": cameras.ASTRO_GROUP, "dslr_scope": cameras.DSLR_GROUP, "dslr_lens": cameras.DSLR_GROUP,
                  "phone": cameras.PHONE_GROUP}.get(kind)
        for group, makers in cameras.grouped():
            if group != wanted:
                continue
            for maker, cams in makers:
                head = f"── {group}: {maker} ──" if maker else f"── {group} ──"
                self.camera.addItem(head, None)
                idx = self.camera.count() - 1
                self.camera.model().item(idx).setEnabled(False)
                for c in cams:
                    self.camera.addItem(("    " + c.model) if maker else ("    " + c.model), c.id)
        i = self.camera.findData(selected) if selected else 0
        self.camera.setCurrentIndex(max(i, 0))
        self._loading = False

    # ------------------------------------------------------------------ editing
    def _selected(self, item, _prev=None):
        if item is None or not item.data(Qt.UserRole):
            return
        p = self.manager.get(item.data(Qt.UserRole))
        if p is None:
            return
        self.current = copy.deepcopy(p)
        self._loading = True
        self.title.setText(p.name.replace("Template: ", "Template – "))
        self.note.setText(p.note or "")
        self.name.setText(p.name)
        self.kind.setCurrentIndex(max(self.kind.findData(p.kind), 0))
        self._fill_cameras(p.kind, p.camera)
        self._loading = True
        e = p.values.get("equipment", {})
        self.focal.setValue(float(e.get("focal_length") or 0))
        self.pixel.setValue(float(e.get("pixel_size") or 0))
        self.sw.setValue(int(e.get("sensor_width") or 0))
        self.sh.setValue(int(e.get("sensor_height") or 0))
        self.f35.setValue(float(e.get("lens_35mm") or 0))
        self.fov.setValue(float(p.values.get("solver.astap", {}).get("fov_override") or 0))
        self._loading = False
        editable = not p.builtin
        for w in (self.name, self.kind, self.camera, self.focal, self.pixel, self.sw, self.sh, self.f35, self.fov):
            w.setEnabled(editable)
        self.btn_copy.setVisible(p.builtin)
        for b in (self.btn_save, self.btn_from_current, self.btn_delete):
            b.setVisible(editable)
        self._loading = True            # show/hide rows only; keep the profile's camera selected
        self._kind_changed()
        self._loading = False
        self._update_summary()

    def _kind_changed(self):
        kind = self.kind.currentData()
        scope = kind in ("astro", "dslr_scope")
        for w in self.scope_rows:
            w.setVisible(scope)
            self.form.labelForField(w).setVisible(scope)
        screen = kind == "screen"
        self.f35.setVisible(not scope and not screen)
        self.form.labelForField(self.f35).setVisible(not scope and not screen)
        self.camera.setVisible(not screen)
        self.form.labelForField(self.camera).setVisible(not screen)
        self.fov.setVisible(screen)
        self.form.labelForField(self.fov).setVisible(screen)
        if not self._loading and self.current and not self.current.builtin:
            self._fill_cameras(kind, "")
        self._update_summary()

    def _camera_changed(self):
        if self._loading:
            return
        cam = cameras.get(self.camera.currentData() or "")
        if cam is None:
            return
        if cam.lens_35mm:
            self.f35.setValue(cam.lens_35mm)
        else:
            self.pixel.setValue(cam.pixel_um)
            self.sw.setValue(cam.width)
            self.sh.setValue(cam.height)
        self._update_summary()

    def _edited(self) -> Profile | None:
        if self.current is None:
            return None
        p = copy.deepcopy(self.current)
        p.name = self.name.text().strip() or p.name
        p.kind = self.kind.currentData()
        p.camera = self.camera.currentData() or ""
        e = p.values.setdefault("equipment", {})
        scope = p.kind in ("astro", "dslr_scope")
        e.update({"focal_length": self.focal.value() if scope else 0.0,
                  "pixel_size": self.pixel.value() if scope else 0.0,
                  "sensor_width": self.sw.value() if scope else 0, "sensor_height": self.sh.value() if scope else 0,
                  "detect_drizzle": scope, "lens_35mm": 0.0 if scope else self.f35.value()})
        e.setdefault("override_file", False)
        a = p.values.setdefault("solver.astap", {})
        a.setdefault("database", "")
        a.setdefault("fov_override", 0.0)
        a.setdefault("any_scale_retry", True)
        if p.kind == "screen":
            e["lens_35mm"] = 0.0
            p.camera = ""
            a["fov_override"] = self.fov.value()
        elif self.current.kind == "screen":
            a["fov_override"] = 0.0          # changed away from a screen profile: no fixed field any more
        return p

    def _update_summary(self):
        p = self._edited()
        if p is None:
            return
        cam = cameras.get(p.camera)
        text = p.summary()
        if cam:
            text = f"{cam.label}: {cam.describe()}\n{text}"
        self.summary.setText(text)
        import html
        rows = []
        for c in self.manager.checklist(p):
            mark, col = ("✓", OK) if c.ok else (("⚠", ERROR) if c.required else ("•", WARN))
            rows.append(f"<tr><td style='color:{col}; padding-right:8px; vertical-align:top'><b>{mark}</b></td>"
                        f"<td>{html.escape(c.text)}</td></tr>")
        self.checks.setText("<b>Before you solve with this profile</b><table style='margin-top:4px'>"
                            + "".join(rows) + "</table>")

    def _copy_template(self):
        t = self.current
        if t is None:
            return
        cam_kind_name = {"astro": "My astro camera", "dslr_scope": "My camera on the telescope",
                         "dslr_lens": "My camera with lens", "phone": "My phone",
                         "screen": "My photos of a screen"}.get(t.kind, "My profile")
        p = self.manager.new_profile(cam_kind_name, t.kind, "", 0.0, t.note)
        p.values = copy.deepcopy(t.values)
        p = self.manager.save(p)
        self._fill_list(p.id)
        self.name.setFocus()
        self.name.selectAll()

    def _save(self) -> Profile | None:
        p = self._edited()
        if p is None or p.builtin:
            return None
        if p.kind in ("astro", "dslr_scope") and not (p.values["equipment"]["focal_length"] and
                                                       p.values["equipment"]["pixel_size"]):
            QMessageBox.information(self, "Profiles", "Enter the telescope's focal length and choose a camera "
                                                      "(or enter the pixel size), so the image scale is known.")
        p = self.manager.save(p)
        if self.manager.active_id == p.id:
            self.manager.apply(p)       # keep the settings in step with the edited profile
        self._fill_list(p.id)
        return p

    def _from_current(self):
        if self.current is None or self.current.builtin:
            return
        vals = self.manager.current_values()
        self.current.values = vals
        e = vals["equipment"]
        self._loading = True
        self.focal.setValue(float(e.get("focal_length") or 0))
        self.pixel.setValue(float(e.get("pixel_size") or 0))
        self.sw.setValue(int(e.get("sensor_width") or 0))
        self.sh.setValue(int(e.get("sensor_height") or 0))
        self.f35.setValue(float(e.get("lens_35mm") or 0))
        self._loading = False
        self._update_summary()

    def _delete(self):
        if self.current is None or self.current.builtin:
            return
        if QMessageBox.question(self, "Delete profile", f"Delete the profile '{self.current.name}'?") \
                != QMessageBox.Yes:
            return
        self.manager.delete(self.current.id)
        self.current = None
        self._fill_list()

    def _use(self):
        if self.current is None:
            return
        p = self.current if self.current.builtin else (self._save() or self.current)
        self.manager.choose(p)
        self._fill_list(p.id)
        self.accept()
