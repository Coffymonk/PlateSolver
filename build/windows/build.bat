@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
rem Copyright (C) 2026 Miklos Elmberg
rem Builds the Windows app:  build\dist\windows\PlateSolver\PlateSolver.exe
rem plus a .zip of that folder, and an installer if Inno Setup 6 is installed.
rem
rem   Double-click this file, or run it from a command prompt.  ("build.bat ci" = no pause at the end)
rem
rem Uses the Python environment that run.bat created (.venv); otherwise makes build\.venv-windows.
setlocal EnableExtensions
cd /d "%~dp0..\.."
set "ROOT=%CD%"
set "OUT=%ROOT%\build\dist\windows"

set "VENV=%ROOT%\.venv"
if not exist "%VENV%\Scripts\python.exe" set "VENV=%ROOT%\build\.venv-windows"
if not exist "%VENV%\Scripts\python.exe" (
    echo Creating a Python environment for building...
    where py >nul 2>nul && (py -3 -m venv "%VENV%") || (python -m venv "%VENV%")
    if not exist "%VENV%\Scripts\python.exe" goto nopython
)
set "PY=%VENV%\Scripts\python.exe"

echo [1/5] Installing libraries and PyInstaller...
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -r requirements.txt pyinstaller || goto failed

for /f "delims=" %%v in ('call "%PY%" -c "import platesolver; print(platesolver.__version__)"') do set "VERSION=%%v"
echo        PlateSolver %VERSION%

echo [2/5] Drawing the app icon and fetching the newest OpenNGC catalogue...
"%PY%" build\make_icons.py || goto failed
"%PY%" tools\update_openngc.py || echo        Keeping the OpenNGC catalogue already in the program.

echo [3/5] Building the app (takes a few minutes)...
"%PY%" -m PyInstaller build\platesolver.spec --noconfirm --clean --distpath "%OUT%" --workpath "%ROOT%\build\work\windows" || goto failed
copy /y "%ROOT%\LICENSE" "%OUT%\PlateSolver\LICENSE.txt" >nul
copy /y "%ROOT%\THIRD-PARTY-NOTICES.md" "%OUT%\PlateSolver\THIRD-PARTY-NOTICES.txt" >nul

echo [4/5] Self-test of the built app...
del "%OUT%\selftest.txt" 2>nul
start "" /wait "%OUT%\PlateSolver\PlateSolver.exe" --selftest "%OUT%\selftest.txt"
if not exist "%OUT%\selftest.txt" goto testfailed
type "%OUT%\selftest.txt"
findstr /c:"all checks passed" "%OUT%\selftest.txt" >nul || goto testfailed

echo [5/5] Packaging...
del "%OUT%\PlateSolver-*-windows.zip" 2>nul
powershell -NoProfile -Command "Compress-Archive -Path '%OUT%\PlateSolver' -DestinationPath '%OUT%\PlateSolver-%VERSION%-windows.zip' -Force" || goto failed
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
    "%ISCC%" /Q /DMyAppVersion=%VERSION% "build\windows\installer.iss" || goto failed
) else (
    echo        Inno Setup 6 not found - skipping the installer. Get it free from jrsoftware.org to make one.
)

echo.
echo Done. Results in %OUT%:
echo   PlateSolver\PlateSolver.exe          - the app (run it from this folder)
echo   PlateSolver-%VERSION%-windows.zip     - the same folder, zipped for sharing
if exist "%OUT%\PlateSolver-%VERSION%-setup.exe" echo   PlateSolver-%VERSION%-setup.exe       - installer with Start-menu entry
goto end

:nopython
echo Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH") and run this file again.
goto fail_end
:testfailed
echo.
echo The self-test of the built app failed - see the report above (%OUT%\selftest.txt).
goto fail_end
:failed
echo.
echo The build failed - see the messages above.
:fail_end
if /i not "%~1"=="ci" pause
exit /b 1
:end
if /i not "%~1"=="ci" pause
exit /b 0
