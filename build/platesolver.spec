# -*- mode: python ; coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Miklos Elmberg
"""PyInstaller recipe for PlateSolver, shared by the Windows, macOS and Linux builds.

Don't run this directly; use build/windows/build.bat, build/macos/build.sh or build/linux/build.sh,
which set up the Python environment and put the result in build/dist/<system>/.

The result is a folder ("one-dir") build: it starts quickly and the user-modules folder keeps
working. On macOS it is wrapped as PlateSolver.app.
"""
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent          # the PlateSolver folder
ICONS = ROOT / "build" / "icons"
sys.path.insert(0, str(ROOT))
from platesolver import __version__             # noqa: E402

# Plugins are found at start-up by scanning the platesolver.plugins package, so every module
# must be bundled explicitly (PyInstaller can't see imports that happen by scanning).
hidden = collect_submodules("platesolver")
hidden += ["pillow_heif", "lz4.block", "zstandard", "tifffile", "imagecodecs", "openpyxl"]

datas = [
    (str(ROOT / "platesolver" / "data"), "platesolver/data"),
    (str(ROOT / "platesolver" / "help"), "platesolver/help"),
    (str(ROOT / "platesolver" / "legal"), "platesolver/legal"),     # GPL-3.0, LGPL-3.0, third-party notices
]
# the licence texts of every bundled library (their licences ask for this), in licenses/<library>/
sys.path.insert(0, str(ROOT / "build"))
from licenses import license_files             # noqa: E402
datas += license_files()
datas += collect_data_files("astropy", includes=["**/*.dat", "**/*.txt", "**/*.json", "**/*.csv",
                                                  "**/*.cfg", "**/*.ecsv"])

# Only QtCore, QtGui and QtWidgets are used; leaving the rest out saves well over 100 MB.
qt_unused = ["QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebSockets",
             "Qt3DCore", "Qt3DRender", "Qt3DInput", "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras",
             "QtQuick", "QtQuick3D", "QtQuickWidgets", "QtQml", "QtMultimedia", "QtMultimediaWidgets",
             "QtCharts", "QtDataVisualization", "QtGraphs", "QtLocation", "QtPositioning", "QtBluetooth",
             "QtNfc", "QtSensors", "QtSerialPort", "QtSql", "QtTest", "QtPdf", "QtPdfWidgets",
             "QtDesigner", "QtHelp", "QtOpenGL", "QtOpenGLWidgets", "QtRemoteObjects", "QtScxml",
             "QtSpatialAudio", "QtStateMachine", "QtSvgWidgets", "QtTextToSpeech", "QtUiTools",
             "QtHttpServer", "QtNetworkAuth", "QtAxContainer"]
excludes = ["tkinter", "matplotlib", "IPython", "pytest", "scipy", "pandas", "pyarrow", "dask", "h5py",
            "bottleneck", "sympy", "numba", "jinja2", "pycparser.lextab", "pycparser.yacctab"] + \
           [f"PySide6.{m}" for m in qt_unused]

a = Analysis(
    [str(ROOT / "build" / "launcher.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hidden,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "win32":
    icon = str(ICONS / "platesolver.ico")
elif sys.platform == "darwin":
    icon = str(ICONS / "platesolver.icns")
else:
    icon = None

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PlateSolver",
    console=False,          # a normal windowed app (the self-test writes its report to a file)
    icon=icon,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PlateSolver", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="PlateSolver.app",
        icon=icon,
        bundle_identifier="org.platesolver.PlateSolver",
        version=__version__,
        info_plist={
            "CFBundleName": "PlateSolver",
            "CFBundleDisplayName": "PlateSolver",
            "CFBundleShortVersionString": __version__,
            "CFBundleVersion": __version__,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Astronomical image",
                "CFBundleTypeRole": "Viewer",
                "CFBundleTypeExtensions": ["fits", "fit", "fts", "xisf", "tif", "tiff", "jpg", "jpeg",
                                           "png", "heic"],
            }],
        },
    )
