# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Main window: image view on the left, sidebar on the right."""
from __future__ import annotations

import logging
import math
from pathlib import Path

from PySide6.QtCore import QByteArray, QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCursor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QLabel, QMainWindow, QMenu, QMessageBox,
                               QSizePolicy, QSplitter, QStyle, QToolBar, QToolButton, QVBoxLayout, QWidget)

from platesolver import APP_NAME, COPYRIGHT, __version__
from platesolver.core import paths
from platesolver.core.batch import BatchSettings
from platesolver.core.equipment import EquipmentSettings
from platesolver.core.export import (ExportSettings, write_objects_csv, write_solution_into_fits,
                                     write_wcs_file)
from platesolver.core.formatting import format_dec, format_ly, format_ra
from platesolver.core.general import GeneralSettings
from platesolver.core.imaging import to_display_rgb8
from platesolver.core.interfaces import CatalogProvider
from platesolver.core.models import ImageData, SkyObject, SolveResult
from platesolver.core.pipeline import Pipeline
from platesolver.core.registry import PluginRegistry
from platesolver.core.settings import SettingsStore
from platesolver.ui.image_view import ImageView, QtOverlayPainter, rgb8_to_qimage
from platesolver.ui.confirm import ConfirmBridge
from platesolver.ui.crop_bar import CropBar, crop_icon
from platesolver.ui.object_info import HoverCard, card_html
from platesolver.ui.settings_dialog import SettingsDialog
from platesolver.ui.sidebar import Sidebar, object_menu, open_first_link
from platesolver.ui.workers import TaskWorker

log = logging.getLogger(__name__)


def hamburger_icon(color: str = "#d6dbe3"):
    """Three-line 'menu' icon, drawn so it is crisp at any screen scaling."""
    from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
    icon = QIcon()
    for size in (16, 20, 24, 32, 48):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color), max(1.5, size / 9))
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        for frac in (0.28, 0.5, 0.72):
            y = size * frac
            p.drawLine(int(size * 0.18), int(y), int(size * 0.82), int(y))
        p.end()
        icon.addPixmap(pm)
    return icon


def play_icon(color: str = "#3ecf6a", disabled: str = "#4a505b"):
    """Green 'play' triangle for Solve, with a grey version while it can't be used."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
    icon = QIcon()
    for mode, col in ((QIcon.Normal, color), (QIcon.Disabled, disabled)):
        for size in (16, 20, 24, 32, 48):
            pm = QPixmap(size, size)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath(QPointF(size * 0.26, size * 0.16))
            path.lineTo(QPointF(size * 0.84, size * 0.5))
            path.lineTo(QPointF(size * 0.26, size * 0.84))
            path.closeSubpath()
            pen = QPen(QColor(col), max(1.0, size / 12))
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(QColor(col))
            p.drawPath(path)
            p.end()
            icon.addPixmap(pm, mode)
    return icon


class MainWindow(QMainWindow):
    def __init__(self, store: SettingsStore, registry: PluginRegistry):
        super().__init__()
        self.store = store
        self.registry = registry
        self.general_section = GeneralSettings()
        GeneralSettings.migrate(store)
        self.general = store.section(self.general_section)
        self.equipment_section = EquipmentSettings()
        EquipmentSettings.migrate(store)
        self.equipment = store.section(self.equipment_section)
        self.export_section = ExportSettings()
        self.export = store.section(self.export_section)
        self.batch_section = BatchSettings()
        from platesolver.core.stars import StarCheckSettings
        from platesolver.core.preprocess import SolvePrepSettings
        self.star_section = StarCheckSettings()
        self.prep_section = SolvePrepSettings()
        from platesolver.core.phoneaids import PhoneAidSettings
        self.phone_section = PhoneAidSettings()
        from platesolver.core.distortion import DistortionSettings
        self.distortion_section = DistortionSettings()
        from platesolver.core.visibility import VisibilitySettings
        self.visibility_section = VisibilitySettings()
        from platesolver.core.quiz import QuizSettings
        self.quiz_section = QuizSettings()
        from platesolver.core.objecthint import HintSettings
        self.hint_section = HintSettings()
        from platesolver.core.profiles import ProfileSettings
        self.profile_section = ProfileSettings()
        self.pipeline = Pipeline(registry, self.general, self.equipment)

        self.image: ImageData | None = None
        self.original_image: ImageData | None = None   # as opened; self.image differs after Crop and rotate
        self._crop_q, self._crop_angle, self._crop_base_rgb = 0, 0.0, None
        self.result: SolveResult | None = None
        self.objects: list[SkyObject] = []
        self.worker: TaskWorker | None = None
        self.overlay_visible: dict[str, bool] = {}
        self.selected: SkyObject | None = None
        self._hovered: SkyObject | None = None
        self._help = None
        self.confirm_bridge = ConfirmBridge(self)   # lets background tasks ask yes/no questions

        self.setWindowTitle(APP_NAME)
        self.resize(1400, 880)
        self.setAcceptDrops(True)

        self.view = ImageView()
        self.hover_card = HoverCard(self.view.viewport())
        self.sidebar = Sidebar()
        self.crop_bar = CropBar()
        self.crop_bar.hide()
        image_area = QWidget()
        lay = QVBoxLayout(image_area)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.crop_bar)
        lay.addWidget(self.view, 1)
        split = QSplitter()
        split.addWidget(image_area)
        split.addWidget(self.sidebar)
        split.setStretchFactor(0, 1)
        split.setSizes([1000, 400])
        split.setChildrenCollapsible(False)
        self.splitter = split
        self.setCentralWidget(split)

        self._build_actions()
        self._build_statusbar()

        # labels depend on the zoom level (overlap avoidance), so redraw shortly after zooming stops
        self._zoom_timer = QTimer(self)
        self._zoom_timer.setSingleShot(True)
        self._zoom_timer.setInterval(250)
        self._zoom_timer.timeout.connect(self.render_overlays)

        self.view.cursor_moved.connect(self._on_cursor)
        self.view.cursor_left.connect(self._cursor_left)
        self.view.zoom_changed.connect(self._on_zoom)
        self.view.file_dropped.connect(lambda p: self.open_file(Path(p)))
        self.view.clicked.connect(self._on_image_click)
        self.view.double_clicked.connect(self._on_image_double_click)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._image_context_menu)
        self.view.crop_changed.connect(self._crop_selection_changed)
        self.crop_bar.rotate_requested.connect(self._crop_rotate)
        self.crop_bar.angle_changed.connect(self._crop_straighten)
        self.crop_bar.reset_requested.connect(self._crop_reset)
        self.crop_bar.cancel_requested.connect(self.cancel_crop)
        self.crop_bar.apply_requested.connect(self.apply_crop)
        self._crop_keys = []
        for key, slot in (("Esc", self.cancel_crop), ("Return", lambda: self.apply_crop(True)),
                          ("Enter", lambda: self.apply_crop(True)), ("[", lambda: self._crop_rotate(-1)),
                          ("]", lambda: self._crop_rotate(1))):
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(slot)
            sc.setEnabled(False)
            self._crop_keys.append(sc)
        self.sidebar.objects.object_selected.connect(self._on_object_selected)
        self.sidebar.objects.filter_changed.connect(self._on_filter_changed)
        from platesolver.core.objectfilter import ObjectFilter
        self.sidebar.objects.set_filter(ObjectFilter.from_dict(self.store.get("ui", "object_filter")))
        self.sidebar.objects.filter_saved.connect(lambda d: self.store.set("ui", "object_filter", d))

        self.sidebar.solution.display(None, None)
        self._apply_general()
        self._restore_window()
        self._first_shown = False
        self._update_actions()
        self._refresh_profiles()
        self.log(f"{APP_NAME} {__version__} – {len(registry.plugins)} modules loaded")
        if self.pipeline.profiles.active():
            self.log(f"Profile: {self.pipeline.profiles.active().name}")
        for mod, err in registry.errors:
            self.log(f"Module problem in {mod}: {err}")

    # ------------------------------------------------------------------ UI setup
    def _build_actions(self):
        st = self.style()

        def act(text, slot, shortcut=None, icon=None, tip=None):
            a = QAction(text, self)
            if icon is not None:
                a.setIcon(st.standardIcon(icon))
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if tip:
                a.setToolTip(tip)
            a.triggered.connect(slot)
            return a

        self.act_open = act("Open…", self.choose_file, QKeySequence.Open, QStyle.SP_DialogOpenButton,
                            "Open a FITS, XISF, TIFF, JPG or HEIC image (Ctrl+O)")
        self.act_solve = act("Solve", self.solve, "F5", QStyle.SP_MediaPlay, "Plate solve the image (F5)")
        self.act_solve.setIcon(play_icon())
        self.act_solve_hint = act("Solve with object hint…", self.solve_with_hint, "Shift+F5", None,
                                  "Tell PlateSolver what the image shows, e.g. M101 (Shift+F5)")
        self.act_cancel = act("Cancel", self.cancel, "Esc", QStyle.SP_BrowserStop, "Stop the running task")
        self.act_settings = act("Settings", lambda: self.open_settings(), "Ctrl+,")
        self.act_settings.setIcon(hamburger_icon())
        self.act_settings.setToolTip("Settings (Ctrl+,)")
        self.act_export_img = act("Export annotated image…", self.export_image, "Ctrl+E",
                                  QStyle.SP_DialogSaveButton, "Save the image with its labels (Ctrl+E)")
        self.act_export_csv = act("Export object list (CSV)…", self.export_csv, "Ctrl+Shift+E")
        self.act_save_wcs = act("Save plate solution (.wcs file)…", self.save_wcs)
        self.act_write_fits = act("Write plate solution into this FITS file", self.write_fits)
        self.act_batch = act("Batch solve folder…", self.open_batch, "Ctrl+B")
        self.act_help = act("Manual", lambda: self.open_help(), "F1")
        self.act_fit = act("Fit to window", self.view.fit, "F")
        self.act_100 = act("Actual size (100 %)", lambda: self.view.set_zoom(1.0), "1")
        self.act_zoom_in = act("Zoom in", lambda: self.view.zoom_by(1.25), QKeySequence.ZoomIn)
        self.act_zoom_out = act("Zoom out", lambda: self.view.zoom_by(0.8), QKeySequence.ZoomOut)
        self.act_search = act("Search objects", self._focus_search, "Ctrl+F")
        self.act_crop = act("Crop and rotate", self.toggle_crop, "C", None,
                            "Crop and rotate the image shown, e.g. to solve only the stars of a photo of a "
                            "screen (C). The file itself is not changed.")
        self.act_crop.setIcon(crop_icon())
        self.act_crop.setCheckable(True)
        self.act_original = act("Back to the original image", self.restore_original, "Shift+C", None,
                                "Show the image as it was opened, without cropping or rotation (Shift+C)")
        self.act_original_tb = act("Original image", self.restore_original, None, None,
                                   "Show the image as it was opened, without cropping or rotation (Shift+C)")
        self.act_quit = act("Exit", self.close, QKeySequence.Quit)

        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        for a in (self.act_open, self.act_solve, self.act_cancel):
            tb.addAction(a)
        tb.addSeparator()
        from PySide6.QtWidgets import QComboBox
        prof_label = QLabel(" Profile: ")
        tb.addWidget(prof_label)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumContentsLength(26)
        self.profile_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.profile_combo.setToolTip("Equipment profile: sets focal length, camera and solver options in one go")
        self.profile_combo.activated.connect(self._profile_picked)
        tb.addWidget(self.profile_combo)
        tb.addSeparator()
        tb.addAction(self.act_fit)
        tb.addAction(self.act_100)
        tb.addAction(self.act_crop)
        tb.addAction(self.act_original_tb)
        tb.addSeparator()
        tb.addAction(self.act_export_img)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)            # pushes the rest to the right edge
        self.help_menu = QMenu("Help", self)
        help_btn = QToolButton()
        help_btn.setText("Help")
        help_btn.setToolTip("Manual, shortcuts and troubleshooting (F1 opens the manual)")
        help_btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        help_btn.setPopupMode(QToolButton.InstantPopup)
        help_btn.setMenu(self.help_menu)
        help_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0; }")
        tb.addWidget(help_btn)
        tb.addAction(self.act_settings)  # rightmost
        self.addToolBar(tb)

        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addAction(self.act_open)
        m.addSeparator()
        m.addAction(self.act_export_img)
        m.addAction(self.act_export_csv)
        m.addSeparator()
        m.addAction(self.act_save_wcs)
        m.addAction(self.act_write_fits)
        m.addSeparator()
        m.addAction(self.act_quit)
        m = mb.addMenu("&Image")
        m.addAction(self.act_solve)
        m.addAction(self.act_solve_hint)
        m.addAction(self.act_cancel)
        m.addSeparator()
        m.addAction(self.act_crop)
        m.addAction(self.act_original)
        m.addSeparator()
        m.addAction(self.act_search)
        m = mb.addMenu("&View")
        for a in (self.act_fit, self.act_100, self.act_zoom_in, self.act_zoom_out):
            m.addAction(a)
        m.addSeparator()
        self.overlay_menu = QMenu("Overlays", self)
        m.addMenu(self.overlay_menu)
        self._build_overlay_menu()
        m = mb.addMenu("&Tools")
        m.addAction(self.act_batch)
        m.addSeparator()
        m.addAction(act("Profiles…", lambda: self.open_profiles(), "Ctrl+P"))
        m.addAction(act("Install ASTAP and star databases…", lambda: self.open_astap_setup()))
        m.addAction(self.act_settings)
        m.addAction(act("Clear cached online lookups", self._clear_cache))
        m.addSeparator()
        m.addAction(act("Open settings folder", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(paths.app_data_dir())))))
        m.addAction(act("Open user modules folder", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(paths.user_plugin_dir())))))
        m = mb.addMenu("&Help")
        help_items = [self.act_help,
                      act("Quick start", lambda: self.open_help("quickstart")),
                      act("Keyboard shortcuts", lambda: self.open_help("shortcuts")),
                      act("Troubleshooting", lambda: self.open_help("troubleshooting")),
                      None,
                      act("Astronomy quiz…", self.open_quiz),
                      None,
                      act("Licence and credits…", self.open_licence),
                      act("About", self._about)]
        for menu in (m, self.help_menu):
            for a in help_items:
                menu.addSeparator() if a is None else menu.addAction(a)

    def _build_overlay_menu(self):
        self.overlay_menu.clear()
        overlays = self.pipeline.overlays()
        if not overlays:
            a = self.overlay_menu.addAction("(none enabled)")
            a.setEnabled(False)
        for i, ov in enumerate(overlays, start=1):
            a = self.overlay_menu.addAction(ov.name)
            a.setCheckable(True)
            a.setChecked(self._overlay_on(ov))
            if i <= 9:
                a.setShortcut(QKeySequence(f"Ctrl+{i}"))
            a.toggled.connect(lambda on, sid=ov.section_id: self._toggle_overlay(sid, on))

    def _build_statusbar(self):
        sb = self.statusBar()
        self.status_label = QLabel("Ready")
        self.pos_label = QLabel("")
        self.zoom_label = QLabel("")
        sb.addWidget(self.status_label, 1)
        sb.addPermanentWidget(self.pos_label)
        sb.addPermanentWidget(self.zoom_label)

    def _update_actions(self):
        cropping = self.view.crop_mode()
        busy = self.worker is not None
        free = not busy and not cropping
        solved = self.result is not None and self.result.success
        self.act_open.setEnabled(free)
        self.act_solve.setEnabled(free and self.image is not None)
        self.act_solve_hint.setEnabled(free and self.image is not None)
        self.act_cancel.setEnabled(busy)
        self.act_settings.setEnabled(free)
        self.act_batch.setEnabled(free)
        self.act_export_img.setEnabled(free and self.image is not None)
        self.act_export_csv.setEnabled(free and bool(self.objects))
        self.act_save_wcs.setEnabled(free and solved)
        self.act_write_fits.setEnabled(free and solved and self.image is not None and self.image.format == "FITS"
                                       and not self.edited())
        self.act_write_fits.setToolTip("Not available for a cropped or rotated view: the solution describes "
                                       "the edited pixels, not the file" if self.edited() else "")
        self.act_crop.setEnabled((not busy and self.image is not None) or cropping)
        self.act_crop.setChecked(cropping)
        self.act_original.setEnabled(free and self.edited())
        self.act_original_tb.setVisible(self.edited())
        self.act_original_tb.setEnabled(free)
        for sc in self._crop_keys:
            sc.setEnabled(cropping and not busy)

    def _apply_general(self):
        self.sidebar.solution.min_stars = int(self.store.get("star_check", "min_stars", 15))
        self.sidebar.objects.card_options = {"show_size": bool(self.general.get("hover_size")),
                                             "show_light": bool(self.general.get("hover_light"))}

    def log(self, text: str):
        self.sidebar.log.add(text)
        log.info(text)

    def set_status(self, text: str):
        self.status_label.setText(text)

    # ------------------------------------------------------------------ tasks
    def _run(self, fn, on_success, busy_text: str):
        if self.worker is not None:
            return
        self.worker = TaskWorker(fn, self, self.confirm_bridge.confirm)
        self.worker.message.connect(self.log)
        self.worker.message.connect(self.set_status)
        self.worker.succeeded.connect(on_success)
        self.worker.failed.connect(self._task_failed)
        self.worker.cancelled.connect(lambda: (self.log("Cancelled"), self.set_status("Cancelled")))
        self.worker.finished.connect(self._task_finished)
        self.set_status(busy_text)
        QApplication.setOverrideCursor(Qt.BusyCursor)
        self._update_actions()
        self.worker.start()

    def _task_finished(self):
        QApplication.restoreOverrideCursor()
        self.worker.deleteLater()
        self.worker = None
        self._update_actions()

    def _task_failed(self, message: str):
        self.log(f"Error: {message}")
        self.set_status("Failed")
        QMessageBox.warning(self, APP_NAME, message)

    def cancel(self):
        if self.worker is not None:
            self.set_status("Cancelling…")
            self.worker.cancel()

    # ------------------------------------------------------------------ open
    def choose_file(self):
        start = self.general.get("last_dir") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Open image", start,
                                              ";;".join(self.pipeline.file_filters()))
        if path:
            self.open_file(Path(path))

    def _display_rgb(self, image: ImageData):
        return to_display_rgb8(image.data, image.is_linear, bool(self.general.get("auto_stretch")),
                               float(self.general.get("stretch_background")))

    def open_file(self, path: Path):
        if self.worker is not None:
            return
        path = Path(path)
        if not self.pipeline.can_load(path):
            QMessageBox.information(self, APP_NAME, f"'{path.suffix}' files can't be opened. "
                                    "Supported: FITS, XISF, TIFF, JPG, PNG and HEIC.")
            return
        self.general.set("last_dir", str(path.parent))
        self.log(f"Opening {path}")

        def work(ctx):
            image = self.pipeline.load(path)
            return image, self._display_rgb(image)
        self._run(work, self._opened, f"Opening {path.name}…")

    def _opened(self, payload):
        image, rgb = payload
        if self.view.crop_mode():
            self._end_crop_mode()
        self.image, self.result, self.objects, self.selected = image, None, [], None
        self.original_image = image
        self._rgb = rgb
        self.view.clear_layers()
        self.view.set_image(rgb8_to_qimage(rgb))
        self.setWindowTitle(f"{image.path.name} – {APP_NAME}")
        self.sidebar.solution.display(image, None)
        self.sidebar.objects.set_message("Solve the image to list the objects in it.")
        self.set_status(f"Opened {image.path.name} ({image.width} × {image.height})")
        self.log(f"Opened {image.format} {image.bit_depth}, {image.width} × {image.height}")
        if image.profile_note:
            self.log(image.profile_note)
            self._log_missing(self.pipeline.profiles.active())
            self._refresh_profiles()
        self._update_actions()
        if self.general.get("auto_solve"):
            QTimer.singleShot(0, self.solve)

    # ------------------------------------------------------------------ solve
    def solve(self):
        if self.image is None or self.worker is not None:
            return
        image = self.image
        self.sidebar.solution.display(image, None, "Solving…")
        self.sidebar.objects.set_message("Solving…")

        def work(ctx):
            result = self.pipeline.solve(image, ctx)
            objects = self.pipeline.find_objects(image, result, ctx) if result.success else []
            return result, objects
        self._run(work, self._solved, "Solving…")

    # ------------------------------------------------------------------ profiles
    def _refresh_profiles(self):
        combo, mgr = self.profile_combo, self.pipeline.profiles
        combo.blockSignals(True)
        combo.clear()
        users = mgr.user_profiles()
        for p in users:
            combo.addItem(p.name, p.id)
        if users:
            combo.insertSeparator(combo.count())
        for p in mgr.profiles():
            if p.builtin:
                combo.addItem(p.name.replace("Template: ", "Template – "), p.id)
        combo.insertSeparator(combo.count())
        combo.addItem("Manage profiles…", "__manage__")
        i = combo.findData(mgr.active_id)
        if i < 0:
            combo.insertItem(0, "(no profile – own settings)", "")
            i = 0
        combo.setCurrentIndex(i)
        active = mgr.active()
        tip = f"{active.name}\n{active.summary()}" if active else "No profile chosen"
        missing = mgr.missing(active) if active else []
        if missing:
            tip += "\n\nStill needed:\n• " + "\n• ".join(missing)
        combo.setToolTip(tip + "\n\nTools › Profiles… to create or edit profiles")
        combo.blockSignals(False)

    def _profile_picked(self, index: int):
        pid = self.profile_combo.itemData(index)
        if pid == "__manage__":
            self.open_profiles()
            return
        prof = self.pipeline.profiles.get(pid) if pid else None
        if prof is None:
            return
        self.pipeline.profiles.choose(prof)
        self.log(f"Profile: {prof.name} – {prof.summary()}")
        self._log_missing(prof)
        self.reload_hints()
        self._refresh_profiles()
        if prof.builtin and prof.kind in ("astro", "dslr_scope"):
            QMessageBox.information(self, "Profile", "This template has no camera or focal length yet. In "
                                    "Tools › Profiles…, click 'Make my own profile from this template' and add "
                                    "them; the window lists everything else that is still needed.")

    def _log_missing(self, prof):
        for m in (self.pipeline.profiles.missing(prof) if prof else []):
            self.log(f"Profile needs: {m}")

    def reload_hints(self):
        """After a profile change: reload the open image's scale hints from file + new settings."""
        if self.image is None:
            return
        try:
            fresh = self.pipeline.load(self.image.path, auto_profile=False)
        except Exception:
            return
        old = self.image.hints
        self.image.hints, self.image.notes = fresh.hints, fresh.notes
        if old.position_hint:           # keep an object hint the user gave
            h = self.image.hints
            h.ra_deg, h.dec_deg, h.position_hint = old.ra_deg, old.dec_deg, old.position_hint
            h.source["ra"], h.source["dec"] = old.source.get("ra", ""), old.source.get("dec", "")
        self.sidebar.solution.display(self.image, self.result)

    # ------------------------------------------------------------------ ASTAP install helper
    def _astap_state(self):
        """(ASTAP plugin, has program, has a database) – plugin None when ASTAP is switched off."""
        astap = self.registry.get("solver.astap")
        if astap is None or not astap.enabled:
            return None, True, True
        return astap, astap.executable() is not None, bool(astap.installed_databases())

    def open_astap_setup(self):
        from platesolver.ui.astap_setup_dialog import AstapSetupDialog
        dlg = AstapSetupDialog(self.store, self.pipeline.profiles.active(), self)
        dlg.exec()
        astap, has_exe, has_db = self._astap_state()
        if astap is not None:
            self.log("ASTAP: " + (str(astap.executable()) if has_exe else "not found") + " – star databases: " +
                     (", ".join(d.upper() for d in astap.installed_databases()) if has_db else "none yet"))
        self._refresh_profiles()

    @staticmethod
    def _can_prompt() -> bool:
        """False when nobody can answer a dialog (tests, headless runs)."""
        import os
        from PySide6.QtGui import QGuiApplication
        if "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("PLATESOLVER_NO_PROMPTS"):
            return False
        return QGuiApplication.platformName() not in ("offscreen", "minimal")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._first_shown:
            self._first_shown = True
            QTimer.singleShot(800, self._first_run_astap_check)

    def _first_run_astap_check(self):
        """Offer the install helper once, when ASTAP isn't installed at all."""
        if not self._can_prompt() or not self.isVisible():
            return
        astap, has_exe, _ = self._astap_state()
        if astap is None or has_exe or self.store.get("ui", "astap_setup_offered"):
            return
        self.store.set("ui", "astap_setup_offered", True)
        box = QMessageBox(self)
        box.setWindowTitle("ASTAP isn't installed")
        box.setIcon(QMessageBox.Information)
        box.setText("PlateSolver uses ASTAP, a free plate solver, to solve images on this computer. "
                    "It wasn't found.")
        box.setInformativeText("PlateSolver can help you download and install it, and later a star database. "
                               "You can also do this any time from Tools › Install ASTAP and star databases.")
        go = box.addButton("Install ASTAP…", QMessageBox.AcceptRole)
        box.addButton("Not now", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is go:
            self.open_astap_setup()

    def _offer_astap_setup(self) -> bool:
        """After a failed solve: offer the helper (once per session) if ASTAP or its database is missing."""
        if not self._can_prompt():
            return False
        astap, has_exe, has_db = self._astap_state()
        if astap is None or (has_exe and has_db) or getattr(self, "_astap_offered", False):
            return False
        self._astap_offered = True
        box = QMessageBox(self)
        box.setWindowTitle("Not solved")
        box.setIcon(QMessageBox.Question)
        box.setText("ASTAP isn't installed, so it couldn't try to solve this image." if not has_exe else
                    "ASTAP has no star database yet, so it couldn't solve this image.")
        box.setInformativeText("ASTAP needs " + ("the program and " if not has_exe else "") +
                               "a star database. Do you want to install " + ("them" if not has_exe else "one") +
                               " now?")
        go = box.addButton("Install…", QMessageBox.AcceptRole)
        box.addButton("Not now", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is go:
            self.open_astap_setup()
        return True

    def open_profiles(self):
        from platesolver.ui.profiles_dialog import ProfilesDialog
        before = self.pipeline.profiles.active_id
        ProfilesDialog(self.pipeline.profiles, self).exec()
        if self.pipeline.profiles.active_id != before:
            p = self.pipeline.profiles.active()
            if p:
                self.log(f"Profile: {p.name} – {p.summary()}")
                self._log_missing(p)
            self.reload_hints()
        self._refresh_profiles()

    def solve_with_hint(self, prefill: str = ""):
        """Ask what the image shows (object name or coordinates), then solve near it."""
        if self.image is None or self.worker is not None:
            return
        from PySide6.QtWidgets import QInputDialog
        from platesolver.core.objecthint import guess_from_filename
        image = self.image
        text, ok = QInputDialog.getText(
            self, "Solve with object hint",
            "What does the image show?\n\nAn object name (M101, NGC 7380, Sh2-155, Pleiades, Wizard Nebula) "
            "or coordinates (14 03 12.6 +54 20 56, or 210.80 54.35 in degrees).\n"
            "The object doesn't have to be in the centre, and a cropped image is fine.",
            text=prefill or image.hints.position_hint or guess_from_filename(image.path) or "")
        if not ok or not text.strip():
            return
        self.sidebar.solution.display(image, None, "Solving…")
        self.sidebar.objects.set_message("Solving…")

        def work(ctx):
            try:
                self.pipeline.set_object_hint(image, text, ctx)
            except LookupError as exc:
                failed = SolveResult.failed(f"Object hint not used: {exc}")
                failed.attempts = [f"Object hint: {exc}"]
                ctx.log(f"Object hint: {exc}")
                return failed, []
            result = self.pipeline.solve(image, ctx)
            objects = self.pipeline.find_objects(image, result, ctx) if result.success else []
            return result, objects
        self._run(work, self._solved, "Solving…")

    def _solved(self, payload):
        result, objects = payload
        self.result, self.objects, self.selected = result, objects, None
        self.sidebar.solution.display(self.image, result)
        if not result.success:
            self.set_status("Not solved")
            self.sidebar.objects.set_message("The image could not be solved. See the Solution tab.")
            self.sidebar.setCurrentWidget(self.sidebar.solution)
            self.view.clear_layers()
            self._update_actions()
            if self._offer_astap_setup():
                return
            skipped = any(a.startswith("Star check") for a in (result.attempts or []))
            if (self.pipeline.hint_settings.get("ask_on_failure") and not skipped and self.image is not None
                    and getattr(self, "_hint_offered_for", None) is not self.image):
                self._hint_offered_for = self.image        # offered once per image; Image › Solve with object hint stays
                QTimer.singleShot(0, self._offer_hint)
            return
        self.set_status(f"Solved: centre {format_ra(result.center_ra_deg)} {format_dec(result.center_dec_deg)}, "
                        f"{result.pixel_scale_arcsec:.2f}″/px")
        self._show_objects()
        self.render_overlays()
        self._update_actions()

    def _offer_hint(self):
        hinted = self.image.hints.position_hint
        box = QMessageBox(self)
        box.setWindowTitle("Not solved")
        box.setIcon(QMessageBox.Question)
        box.setText(f"The image could not be solved near {hinted}. Is it a different object?" if hinted else
                    "The image could not be solved. Do you know what it shows?")
        box.setInformativeText("With an object name or coordinates, the solver only needs to search near it, "
                               "which usually works even for cropped or resized JPGs.")
        give = box.addButton("Enter object…", QMessageBox.AcceptRole)
        box.addButton("Not now", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is give:
            self.solve_with_hint()

    def _show_objects(self):
        errors = list(getattr(self.pipeline, "object_errors", []))
        note = ""
        if errors:
            note = "Some lookups failed: " + "; ".join(errors[:2])
            for e in errors:
                self.log(f"Problem: {e}")
        decimals = int(self.general.get("ly_decimals"))
        if self.objects:
            self.sidebar.objects.set_objects(self.objects, decimals, note, bool(self.general.get("size_column")))
            self.sidebar.setCurrentWidget(self.sidebar.objects)
        elif not self.registry.of_kind(CatalogProvider):
            self.sidebar.objects.set_message(
                "Solved. No object catalogue module is enabled, so no objects are listed. "
                "Enable one in Settings › Object catalogues.")
            self.sidebar.setCurrentWidget(self.sidebar.solution)
        elif errors:
            self.sidebar.objects.set_message(
                "The objects could not be looked up.\n\n" + "\n".join(errors[:3]) +
                "\n\nCheck the internet connection and solve again (F5).")
            self.sidebar.setCurrentWidget(self.sidebar.objects)
        else:
            self.sidebar.objects.set_message("No catalogued objects found in this field. "
                                             "Lower the limits in Settings › Object catalogues to see more.")

    # ------------------------------------------------------------------ overlays
    def labelled_objects(self) -> list[SkyObject]:
        if self.general.get("overlay_follows_filter"):
            return self.sidebar.objects.visible_objects()
        return self.objects

    def render_overlays(self):
        self.view.clear_layers()
        self._hovered = None
        self.hover_card.hide()
        if self.image is None or self.result is None or not self.result.success:
            return
        font_scale = float(self.general.get("overlay_font_size")) / 11.0
        screen_px = 1.0 / max(self.view.zoom(), 1e-6)
        objects = self.labelled_objects()
        for z, ov in enumerate(self.pipeline.overlays(), start=1):
            group = self.view.new_layer(ov.section_id, z)
            painter = QtOverlayPainter(group, self.view.scene(), font_scale, screen_px=screen_px)
            painter.ly_decimals = int(self.general.get("ly_decimals"))
            try:
                ov.render(painter, self.image, self.result, objects)
            except Exception as exc:
                log.exception("Overlay %s failed", ov.name)
                self.log(f"Overlay '{ov.name}' failed: {exc}")
            group.setVisible(self._overlay_on(ov))
        if self.selected is not None:
            self._draw_selection(self.selected)

    def _on_zoom(self, z: float):
        self.zoom_label.setText(f"{z * 100:.0f} %")
        if self.objects:
            self._zoom_timer.start()

    def _on_filter_changed(self):
        if self.objects and self.general.get("overlay_follows_filter"):
            self.render_overlays()

    def _overlay_on(self, ov) -> bool:
        if ov.section_id not in self.overlay_visible:
            saved = self.store.get("ui", "overlays_visible") or {}
            self.overlay_visible[ov.section_id] = saved.get(ov.section_id, ov.visible_by_default)
        return self.overlay_visible[ov.section_id]

    def _toggle_overlay(self, section_id: str, on: bool):
        self.overlay_visible[section_id] = on
        self.store.set("ui", "overlays_visible", dict(self.overlay_visible))
        self.view.set_layer_visible(section_id, on)

    # ------------------------------------------------------------------ objects on the image
    def object_at(self, x: float, y: float) -> SkyObject | None:
        """The object under the mouse: the smallest marker that contains the point."""
        if not self.objects or self.result is None:
            return None
        tol = 10.0 / max(self.view.zoom(), 1e-6)
        scale = self.result.pixel_scale_arcsec or 1.0
        best, best_r = None, math.inf
        for o in self.labelled_objects():
            if o.x is None:
                continue
            r = tol
            if o.size_arcmin and o.category != "star":
                r = max(tol, o.size_arcmin * 30.0 / scale)
            d = math.hypot(x - o.x, y - o.y)
            if d <= r and r < best_r:
                best, best_r = o, r
        return best

    def _on_cursor(self, x: float, y: float):
        text = f"x {x:.0f}  y {y:.0f}"
        if self.view.crop_mode():
            self.pos_label.setText(text)
            return
        if self.result is not None and self.result.success:
            ra, dec = self.result.pixel_to_radec(x, y)
            text += f"    RA {format_ra(ra)}   Dec {format_dec(dec)}"
        self.pos_label.setText(text)
        obj = self.object_at(x, y)
        pointer = self.view.viewport().mapFromGlobal(QCursor.pos())
        if obj is not self._hovered:
            self._hovered = obj
            self.view.viewport().setCursor(Qt.PointingHandCursor if obj else Qt.OpenHandCursor)
            if obj is None or not self.general.get("hover_card"):
                self.hover_card.hide()
            else:
                hint = "Click to select · double-click to open information" if obj.links else "Click to select"
                html = card_html(obj, int(self.general.get("ly_decimals")), bool(self.general.get("hover_size")),
                                 bool(self.general.get("hover_light")), hint)
                self.hover_card.show(html, pointer)
        elif obj is not None and self.hover_card.is_visible():
            self.hover_card.move_near(pointer)   # follow the pointer while it stays on the object

    def _cursor_left(self):
        self.pos_label.setText("")
        self._hovered = None
        self.hover_card.hide()

    def _on_image_click(self, x: float, y: float):
        obj = self.object_at(x, y)
        if obj is not None:
            self._select(obj, recentre=False)
            self.sidebar.objects.select_object(obj)

    def _on_image_double_click(self, x: float, y: float):
        obj = self.object_at(x, y)
        if obj is not None and self.general.get("click_opens"):
            if not open_first_link(obj):
                self.set_status(f"No information link for {obj.display_name}")
            return
        self.view.fit()

    def _image_context_menu(self, pos):
        p = self.view.mapToScene(pos)
        obj = self.object_at(p.x(), p.y())
        if obj is not None:
            object_menu(self, obj, int(self.general.get("ly_decimals"))).exec(self.view.viewport().mapToGlobal(pos))

    def _on_object_selected(self, obj: SkyObject):
        self._select(obj, recentre=True)

    def _select(self, obj: SkyObject, recentre: bool):
        if obj is None or obj.x is None or self.result is None or not self.result.success:
            return
        self.selected = obj
        r = self._draw_selection(obj)
        if recentre:
            on_screen = r * self.view.zoom()
            if on_screen < 25:
                self.view.set_zoom(min(2.0, 60.0 / r))
            self.view.center_on_pixel(obj.x, obj.y)
        dist = ""
        if obj.distance:
            dist = " – " + format_ly(obj.distance.light_years, int(self.general.get("ly_decimals")))
        self.set_status(f"{obj.display_name}: {obj.object_type}{dist}")

    def _draw_selection(self, obj: SkyObject) -> float:
        layer = self.view.new_layer("selection", 100)
        painter = QtOverlayPainter(layer, self.view.scene())
        base = max(12.0, min(self.image.width, self.image.height) * 0.02)
        r = base
        if obj.size_arcmin and self.result.pixel_scale_arcsec and obj.category != "star":
            r = max(base, obj.size_arcmin * 60.0 / 2.0 / self.result.pixel_scale_arcsec * 1.15)
        painter.circle(obj.x, obj.y, r, "#5aa9ff", 3.0)
        painter.circle(obj.x, obj.y, r * 1.12, "#ffffff", 1.0)
        return r

    def _focus_search(self):
        self.sidebar.setCurrentWidget(self.sidebar.objects)
        self.sidebar.objects.search.setFocus()
        self.sidebar.objects.search.selectAll()

    # ------------------------------------------------------------------ crop and rotate
    def edited(self) -> bool:
        return self.image is not None and self.original_image is not None and self.image is not self.original_image

    def toggle_crop(self):
        if self.view.crop_mode():
            self.cancel_crop()
        else:
            self.start_crop()

    def start_crop(self):
        if self.image is None or self.worker is not None or self.view.crop_mode():
            self._update_actions()
            return
        self._crop_q, self._crop_angle = 0, 0.0
        self._crop_base_rgb = self._rgb
        self.hover_card.hide()
        self._hovered = None
        self.view.clear_layers()
        self.view.set_crop_mode(True)
        self.crop_bar.reset_angle()
        self.crop_bar.set_selection(None)
        self.crop_bar.show()
        self.view.setFocus()
        self.set_status("Crop and rotate: drag a rectangle on the image, then Apply and solve (Enter)")
        self._update_actions()

    def _crop_preview(self):
        from platesolver.core.imageedit import rotate_array
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            rgb = rotate_array(self._crop_base_rgb, self._crop_q, self._crop_angle)
        finally:
            QApplication.restoreOverrideCursor()
        self._crop_preview_rgb = rgb
        self.view.set_crop_rect(None)
        self.view.set_image(rgb8_to_qimage(rgb))

    def _crop_rotate(self, step: int):
        if not self.view.crop_mode() or self.worker is not None:
            return
        self._crop_q = (self._crop_q + step) % 4
        self._crop_preview()

    def _crop_straighten(self, angle: float):
        if not self.view.crop_mode() or abs(angle - self._crop_angle) < 1e-9:
            return
        self._crop_angle = angle
        self._crop_preview()

    def _crop_reset(self):
        self.crop_bar.reset_angle()
        changed = self._crop_q or self._crop_angle
        self._crop_q, self._crop_angle = 0, 0.0
        if changed:
            self._crop_preview()
        else:
            self.view.set_crop_rect(None)

    def _crop_selection_changed(self):
        sel = self.view.crop_rect()
        rect = self.view.sceneRect()
        whole = (int(round(rect.width())), int(round(rect.height())))
        self.crop_bar.set_selection(None if sel is None else (sel[2], sel[3]), whole)

    def _end_crop_mode(self):
        self.view.set_crop_mode(False)
        self.crop_bar.hide()
        self._crop_base_rgb = None
        self._update_actions()

    def cancel_crop(self):
        if not self.view.crop_mode() or self.worker is not None:
            return
        changed = self._crop_q or self._crop_angle
        self._end_crop_mode()
        if changed:
            self.view.set_image(rgb8_to_qimage(self._rgb))
        self.set_status("Crop and rotate cancelled")
        self.render_overlays()

    def apply_crop(self, solve: bool = True):
        if not self.view.crop_mode() or self.worker is not None or self.image is None:
            return
        from platesolver.core.imageedit import Edit, clamp_crop, describe, edited_image
        sel = self.view.crop_rect()
        rect = self.view.sceneRect()
        try:
            crop = clamp_crop(sel, int(round(rect.width())), int(round(rect.height())))
        except ValueError as exc:
            QMessageBox.information(self, APP_NAME, str(exc))
            return
        edit = Edit(self._crop_q, self._crop_angle, crop)
        if edit.is_identity():
            self.cancel_crop()
            if solve and not (self.result and self.result.success):
                self.solve()
            return
        base = self.image
        self._end_crop_mode()

        def work(ctx):
            new = edited_image(base, edit)
            return new, self._display_rgb(new), describe(edit), solve
        self._run(work, self._crop_applied, "Cropping…")

    def _crop_applied(self, payload):
        new, rgb, text, solve = payload
        self.image, self.result, self.objects, self.selected = new, None, [], None
        self._rgb = rgb
        self.view.clear_layers()
        self.view.set_image(rgb8_to_qimage(rgb))
        self.setWindowTitle(f"{new.path.name} (edited) – {APP_NAME}")
        self.sidebar.solution.display(new, None)
        self.sidebar.objects.set_message("Solve the edited image to list the objects in it.")
        self.log(f"Edited view: {text} – now {new.width} × {new.height}. The file itself is unchanged; "
                 "Image › Back to the original image (Shift+C) shows it again.")
        self.set_status(f"Edited view {new.width} × {new.height}")
        self._update_actions()
        if solve:
            QTimer.singleShot(0, self.solve)

    def restore_original(self):
        if not self.edited() or self.worker is not None or self.view.crop_mode():
            return
        orig = self.original_image
        orig.hints = self.image.hints        # profile changes and object hints made meanwhile still apply
        image = orig

        def work(ctx):
            return image, self._display_rgb(image)

        def done(payload):
            img, rgb = payload
            self.image, self.result, self.objects, self.selected = img, None, [], None
            self._rgb = rgb
            self.view.clear_layers()
            self.view.set_image(rgb8_to_qimage(rgb))
            self.setWindowTitle(f"{img.path.name} – {APP_NAME}")
            self.sidebar.solution.display(img, None)
            self.sidebar.objects.set_message("Solve the image to list the objects in it.")
            self.log(f"Back to the original image ({img.width} × {img.height})")
            self.set_status(f"Original image {img.width} × {img.height}")
            self._update_actions()
        self._run(work, done, "Restoring the original image…")

    # ------------------------------------------------------------------ export
    def _default_path(self, suffix: str, tag: str = "") -> str:
        folder = Path(self.store.get("export", "last_dir") or (self.image.path.parent if self.image else Path.home()))
        stem = self.image.path.stem if self.image else "platesolver"
        return str(folder / f"{stem}{tag}{suffix}")

    def _remember_dir(self, path: str):
        self.export.set("last_dir", str(Path(path).parent))

    def export_image(self):
        if self.image is None:
            return
        fmt = self.export.get("image_format")
        ext = ".jpg" if fmt == "jpg" else ".png"
        tag = "_edited_annotated" if self.edited() else "_annotated"
        path, _ = QFileDialog.getSaveFileName(self, "Export annotated image", self._default_path(ext, tag),
                                              "PNG image (*.png);;JPEG image (*.jpg *.jpeg)" if ext == ".png"
                                              else "JPEG image (*.jpg *.jpeg);;PNG image (*.png)")
        if not path:
            return
        if Path(path).suffix.lower() not in (".png", ".jpg", ".jpeg"):
            path += ext
        self._remember_dir(path)
        from platesolver.ui.export_image import render_annotated, save_image
        size = self.export.get("image_size")
        max_w = None if size == "full" else int(size)
        overlays = [ov for ov in self.pipeline.overlays() if self._overlay_on(ov)] if self.result and self.result.success else []
        objects = self.labelled_objects() if self.export.get("only_filtered") else self.objects
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            img = render_annotated(self._rgb, self.image, self.result, objects, overlays, max_w,
                                   float(self.export.get("label_scale")) / 100.0,
                                   float(self.general.get("overlay_font_size")), int(self.general.get("ly_decimals")),
                                   bool(self.export.get("caption")) and bool(self.result and self.result.success))
            ok = save_image(img, Path(path), int(self.export.get("jpeg_quality")))
        except Exception as exc:
            log.exception("Export failed")
            ok = False
            self.log(f"Export failed: {exc}")
        finally:
            QApplication.restoreOverrideCursor()
        if ok:
            self.log(f"Saved {path} ({img.width()} × {img.height()})")
            self.set_status(f"Saved {Path(path).name}")
        else:
            QMessageBox.warning(self, APP_NAME, f"Could not save {path}")

    def export_csv(self):
        if not self.objects:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export object list", self._default_path(".csv", "_objects"),
                                              "CSV (*.csv)")
        if not path:
            return
        self._remember_dir(path)
        objects = self.labelled_objects() if self.export.get("only_filtered") else self.objects
        write_objects_csv(Path(path), objects, int(self.general.get("ly_decimals")),
                          str(self.export.get("csv_separator")))
        self.log(f"Saved {len(objects)} objects to {path}")
        self.set_status(f"Saved {Path(path).name}")

    def save_wcs(self):
        if not (self.result and self.result.success):
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save plate solution",
                                              self._default_path(".wcs", "_edited" if self.edited() else ""),
                                              "WCS header (*.wcs)")
        if path:
            self._remember_dir(path)
            write_wcs_file(Path(path), self.result, self.image)
            self.log(f"Saved plate solution to {path}")
            self.set_status(f"Saved {Path(path).name}")

    def write_fits(self):
        if not (self.result and self.result.success and self.image and self.image.format == "FITS"):
            return
        answer = QMessageBox.question(
            self, APP_NAME, f"Write the plate solution into the header of\n{self.image.path}?\n\n"
            "Only header keywords are changed; the image data is not touched. Any existing solution in the "
            "file is replaced.")
        if answer != QMessageBox.Yes:
            return
        try:
            write_solution_into_fits(self.image.path, self.result, self.image)
            self.log(f"Wrote the plate solution into {self.image.path.name}")
            self.set_status("Solution written into the FITS header")
        except Exception as exc:
            log.exception("Writing FITS failed")
            QMessageBox.warning(self, APP_NAME, f"Could not write the file:\n{exc}")

    # ------------------------------------------------------------------ batch, help, settings
    def open_batch(self):
        from platesolver.ui.batch_dialog import BatchDialog
        start = str(self.image.path.parent) if self.image else (self.general.get("last_dir") or "")
        dlg = BatchDialog(self.pipeline, self.store, self.general, start, self, self.confirm_bridge.confirm)
        dlg.open_requested.connect(lambda p: (dlg.close(), self.open_file(Path(p))))
        dlg.exec()

    def help_sections(self) -> list:
        from platesolver.core.interfaces import ALL_KINDS
        secs = [self.general_section, self.equipment_section, self.star_section, self.prep_section, self.phone_section, self.distortion_section, self.visibility_section,
                self.export_section, self.batch_section, self.hint_section, self.profile_section,
                self.quiz_section]
        for kind in ALL_KINDS:
            secs += self.registry.of_kind(kind, enabled_only=False)
        return secs

    def open_quiz(self):
        from platesolver.ui.quiz_dialog import QuizDialog
        try:
            dlg = QuizDialog(self.store.section(self.quiz_section), self)
        except Exception as exc:
            QMessageBox.warning(self, "Astronomy quiz", f"The quiz could not be started: {exc}")
            return
        dlg.exec()

    def open_help(self, anchor: str = ""):
        from platesolver.ui.help_window import HelpWindow
        if self._help is None:
            self._help = HelpWindow(self, anchor, self.help_sections())
        elif anchor:
            self._help.show_anchor(anchor)
        self._help.show()
        self._help.raise_()
        self._help.activateWindow()

    def open_settings(self, section: str | None = None):
        dlg = SettingsDialog(self.store, [self.general_section, self.equipment_section, self.star_section,
                                          self.prep_section, self.phone_section, self.distortion_section, self.visibility_section, self.export_section, self.batch_section,
                                          self.hint_section, self.profile_section, self.quiz_section],
                                self.registry, self, section)
        def help_for_page(anchor):
            # the Settings dialog is modal, so its help window must belong to it to be usable
            from platesolver.ui.help_window import HelpWindow
            hw = HelpWindow(dlg, anchor, self.help_sections())
            hw.setAttribute(Qt.WA_DeleteOnClose)
            hw.show()
        dlg.help_requested.connect(help_for_page)
        if dlg.exec():
            self.log("Settings saved")
            self._apply_general()
            if self.image is not None and self.result is not None and self.result.success:
                self.log("Solve again (F5) to apply changed catalogue, distance or link settings")
            self._build_overlay_menu()
            if self.image is not None:
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    self._rgb = self._display_rgb(self.image)
                    self.view.set_image(rgb8_to_qimage(self._rgb), keep_view=True)
                finally:
                    QApplication.restoreOverrideCursor()
                if self.objects:
                    self._show_objects()
                self.render_overlays()

    # ------------------------------------------------------------------ window
    def _clear_cache(self):
        from platesolver.core.cache import JsonCache
        n = sum(JsonCache(name).clear() for name in ("simbad", "wikipedia"))
        self.log(f"Cleared {n} cached lookups")
        self.set_status(f"Cleared {n} cached lookups")

    def open_licence(self):
        from platesolver.ui.licence_dialog import LicenceDialog
        LicenceDialog(self).exec()

    def _about(self):
        mods = "<br>".join(f"{p.kind_label}: {p.name}" for p in self.registry.plugins)
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME} {__version__}</h3>"
                          f"<p>Plate solves FITS, XISF, TIFF, JPG, PNG and HEIC images and identifies the objects "
                          f"in them.</p>"
                          f"<p>{COPYRIGHT}<br>Free software under the GNU General Public License, version 3 or "
                          f"later. It comes with ABSOLUTELY NO WARRANTY. See <i>Help › Licence and credits</i>.</p>"
                          f"<p><b>Data</b><br>SIMBAD (CDS, Strasbourg) · OpenNGC by Mattia Verga (CC BY-SA 4.0) · "
                          f"Hipparcos stars (XHIP) and constellations from d3-celestial by Olaf Frohn (BSD) · "
                          f"Wikipedia</p>"
                          f"<p><b>Built with</b><br>Python, Qt (PySide6, LGPL-3.0), Astropy, NumPy, Pillow, "
                          f"pillow-heif, tifffile, imagecodecs, lz4, zstandard, openpyxl</p>"
                          f"<p><b>Modules</b><br>{mods}</p>"
                          f"<p>Settings: {paths.app_data_dir()}</p>")

    def _restore_window(self):
        geo = self.store.get("ui", "geometry")
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        sp = self.store.get("ui", "splitter")
        if sp:
            self.splitter.restoreState(QByteArray.fromBase64(sp.encode()))

    def closeEvent(self, event):
        if self.worker is not None:
            self.worker.cancel()
            self.worker.wait(5000)
        self.store.set("ui", "geometry", bytes(self.saveGeometry().toBase64()).decode())
        self.store.set("ui", "splitter", bytes(self.splitter.saveState().toBase64()).decode())
        try:
            self.store.save()
        except OSError as exc:
            log.warning("Could not save settings: %s", exc)
        super().closeEvent(event)
