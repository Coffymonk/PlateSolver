; SPDX-License-Identifier: GPL-3.0-or-later
; Copyright (C) 2026 Miklos Elmberg
; Inno Setup script for the PlateSolver installer (used by build.bat when Inno Setup 6 is installed).
; Free download: https://jrsoftware.org/isinfo.php

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{6F0C2B7E-4E1B-4C55-9A83-3F2E9D1B7A10}
AppName=PlateSolver
AppVersion={#MyAppVersion}
AppPublisher=Miklos Elmberg
AppCopyright=Copyright (C) 2026 Miklos Elmberg. GPL-3.0-or-later.
AppPublisherURL=https://github.com/Coffymonk/PlateSolver
AppSupportURL=https://github.com/Coffymonk/PlateSolver/issues
AppUpdatesURL=https://github.com/Coffymonk/PlateSolver/releases
VersionInfoVersion={#MyAppVersion}
; The GPL is shown for information (it needs no acceptance), before the installation starts
InfoBeforeFile=..\..\LICENSE
DefaultDirName={autopf}\PlateSolver
DefaultGroupName=PlateSolver
DisableProgramGroupPage=yes
OutputDir=..\dist\windows
OutputBaseFilename=PlateSolver-{#MyAppVersion}-setup
SetupIconFile=..\icons\platesolver.ico
UninstallDisplayIcon={app}\PlateSolver.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\windows\PlateSolver\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PlateSolver"; Filename: "{app}\PlateSolver.exe"
Name: "{group}\PlateSolver manual"; Filename: "{app}\_internal\platesolver\help\manual.html"
Name: "{group}\Licence and third-party notices"; Filename: "{app}\THIRD-PARTY-NOTICES.txt"
Name: "{autodesktop}\PlateSolver"; Filename: "{app}\PlateSolver.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PlateSolver.exe"; Description: "{cm:LaunchProgram,PlateSolver}"; Flags: nowait postinstall skipifsilent
