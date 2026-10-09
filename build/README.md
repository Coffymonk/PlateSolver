# Building PlateSolver apps

This folder makes stand-alone apps that run without installing Python. Each app has to be built on
its own system: the Windows app on Windows, the Mac app on a Mac, the Linux app on Linux.
Results go to `build/dist/<system>/`. Each build first fetches the newest [OpenNGC](https://github.com/mattiaverga/OpenNGC) release (`tools/update_openngc.py`; without internet the copy already in the program is kept). Every build ends with a self-test of the finished app
(`selftest.txt`), so a broken build is noticed straight away.

| System | How | Result |
|---|---|---|
| **Windows** | Double-click `build\windows\build.bat` | `PlateSolver\PlateSolver.exe`, a `.zip` of it, and an installer if [Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed |
| **macOS** | In Terminal: `bash build/macos/build.sh` | `PlateSolver.app` and a `.dmg` |
| **Linux** | `bash build/linux/build.sh` | `PlateSolver/` folder and a `.tar.gz` |

A build takes a few minutes the first time (libraries are downloaded), less after that.

## Windows

* Uses the Python environment that `run.bat` already created (`.venv`), so run PlateSolver once
  with `run.bat` first; otherwise the script makes its own environment.
* For a proper installer with a Start-menu entry and uninstaller, install Inno Setup 6 (free) and
  run `build.bat` again: it finds Inno Setup by itself.
* The app is not code-signed. The first time, Windows may say "Windows protected your PC":
  click *More info* › *Run anyway*.

## macOS

* Needs Python 3.10 or newer: the installer from [python.org](https://www.python.org/downloads/)
  or Homebrew (`brew install python`).
* The app is built for the Mac's own processor: Apple Silicon (M1 and later) or Intel.
* It is not signed by Apple. The first time, right-click the app › *Open*, or System Settings ›
  Privacy & Security › *Open Anyway*.

## Linux

* Needs Python 3.10+ with venv (Debian/Ubuntu: `sudo apt install python3-venv`).
* If the app doesn't start, install Qt's window-system helper:
  `sudo apt install libxcb-cursor0` (Fedora: `sudo dnf install xcb-util-cursor`).
* `PlateSolver/install.sh` adds the app to the applications menu.

## Publishing a release on GitHub

1. Set the version in `platesolver/__init__.py` and add it to *What's new* in the manual.
2. Build the app on each system (above) and check its `selftest.txt`.
3. On GitHub: *Releases* › *Draft a new release*, create the tag `v<version>` (for example `v0.9.7`),
   and attach the files from `build/dist/<system>/`: `PlateSolver-<version>-windows.zip` (and the
   installer, if made), the macOS `.dmg`, the Linux `.tar.gz`, and the installation guide PDFs.

The `Samples` folder and the build outputs are not uploaded (see `.gitignore`).

## Files

| File | Purpose |
|---|---|
| `platesolver.spec` | PyInstaller recipe shared by all systems: which files and modules are bundled |
| `launcher.py` | Entry point of the packaged app |
| `make_icons.py` | Draws the app icon (`icons/`: .png, .ico, .icns) |
| `../tools/update_openngc.py` | Refreshes the bundled OpenNGC catalogue from its newest release (also runnable on its own) |
| `windows/` | `build.bat`, `installer.iss` (Inno Setup) |
| `macos/` | `build.sh` |
| `linux/` | `build.sh`, `platesolver.desktop`, `install.sh` |

ASTAP is not bundled: install it separately and set its location in Settings.
Settings, logs and your own modules stay in the same per-user folder as when running from source.
