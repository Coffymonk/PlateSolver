@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
rem Copyright (C) 2026 Miklos Elmberg
rem Starts PlateSolver. The first start creates a private Python environment
rem in the .venv folder and installs the libraries (takes a few minutes).
rem   run.bat            normal start
rem   run.bat debug      keep a console window open to see messages
setlocal
cd /d "%~dp0"
set "VENV=%~dp0.venv"

if not exist "%VENV%\Scripts\python.exe" (
    echo Creating the Python environment - first start only...
    where py >nul 2>nul && (py -3 -m venv "%VENV%") || (python -m venv "%VENV%")
    if not exist "%VENV%\Scripts\python.exe" goto nopython
)

fc /b requirements.txt "%VENV%\installed-requirements.txt" >nul 2>nul
if errorlevel 1 (
    echo Installing libraries...
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip
    "%VENV%\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto pipfailed
    copy /y requirements.txt "%VENV%\installed-requirements.txt" >nul
)

if /i "%~1"=="debug" (
    "%VENV%\Scripts\python.exe" -m platesolver %2 %3
    pause
    exit /b
)
start "" "%VENV%\Scripts\pythonw.exe" -m platesolver %*
exit /b 0

:nopython
echo.
echo Python 3.10 or newer was not found.
echo Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH")
echo and run this file again.
pause
exit /b 1

:pipfailed
echo.
echo Installing the libraries failed - see the messages above.
pause
exit /b 1
