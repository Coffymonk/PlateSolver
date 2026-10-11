# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""Toolbar and menus: Settings and Help only in the menus, the profile list also under Tools › Profile."""


def test_toolbar_and_profile_menu(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("PLATESOLVER_HOME", str(tmp_path / "home"))
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QApplication, QToolButton
    app = QApplication.instance() or QApplication([])
    from platesolver.core import paths
    from platesolver.core.registry import PluginRegistry
    from platesolver.core.settings import SettingsStore
    from platesolver.ui import main_window as mw
    from platesolver.ui.main_window import MainWindow
    monkeypatch.setattr(mw.QMessageBox, "information", lambda *a, **k: None)   # template note, no click needed
    store = SettingsStore(paths.settings_file())
    win = MainWindow(store, PluginRegistry(store).discover())
    tb = win.toolbar
    assert win.act_settings not in tb.actions()
    assert not [b for b in tb.findChildren(QToolButton) if b.text() == "Help"]
    assert tb.iconSize().width() == 16
    assert win.act_settings.menuRole() == QAction.PreferencesRole
    assert not win.act_settings.shortcut().isEmpty()
    # Tools › Profile lists the same profiles as the toolbar and follows the choice both ways
    items = [a for a in win.profile_menu.actions() if a.isCheckable() and a.data()]
    combo_ids = [win.profile_combo.itemData(i) for i in range(win.profile_combo.count())]
    assert items and all(a.data() in combo_ids for a in items)
    pid = items[-1].data()
    items[-1].trigger()
    app.processEvents()
    assert win.pipeline.profiles.active_id == pid
    assert win.profile_combo.currentData() == pid
    checked = [a for a in win.profile_menu.actions() if a.isChecked()]
    assert len(checked) == 1 and checked[0].data() == pid
    other = next(i for i in range(win.profile_combo.count())
                 if win.profile_combo.itemData(i) not in ("", "__manage__", pid, None))
    win._profile_picked(other)
    assert [a.data() for a in win.profile_menu.actions() if a.isChecked()] == [win.profile_combo.itemData(other)]
    win.close()
    app.processEvents()


def test_opens_column(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from platesolver.core.models import Link, SkyObject
    from platesolver.ui.sidebar import ObjectsPanel, link_source
    a = SkyObject("M 31", 10.68, 41.27, "Galaxy")
    a.links = [Link("Wikipedia (en): Andromeda Galaxy", "https://en.wikipedia.org/wiki/Andromeda_Galaxy", "Wikipedia"),
               Link("SIMBAD: M 31", "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=M31", "SIMBAD")]
    b = SkyObject("HD 1", 1.0, 2.0, "Star")
    b.links = [Link("SIMBAD: HD 1", "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=HD1", "SIMBAD")]
    c = SkyObject("X 1", 1.0, 2.0, "Object")
    p = ObjectsPanel()
    p.set_objects([a, b, c], 1)
    col = p.OPENS
    assert p.tree.headerItem().text(col) == "Opens"
    texts = [p.tree.topLevelItem(i).text(col) for i in range(3)]
    assert texts == ["Wikipedia", "SIMBAD", "–"]
    assert "Andromeda" in p.tree.topLevelItem(0).toolTip(col)
    assert link_source(Link("x", "https://ned.ipac.caltech.edu/byname?objname=M31")) == "NED"
    assert link_source(Link("x", "https://www.example.org/page")) == "Example"
    p.set_objects([a], 1, opens_column=False)
    assert p.tree.isColumnHidden(col)
