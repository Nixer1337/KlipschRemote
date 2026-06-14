; Inno Setup script for Klipsch Remote (unofficial).
;
; Packages the native `flet build` bundle in dist_app\ into a single installer,
; KlipschRemote-Setup.exe. The app is a real native Flutter executable, so once
; installed it has its own icon/identity and pins to the taskbar normally.
;
; Build it after build_app.ps1 has produced dist_app\:
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
; or just `iscc installer.iss` if Inno Setup is on PATH (it is on the GitHub
; windows-latest runner). Output lands in dist_installer\.

#define MyAppName "Klipsch Remote"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Unofficial"
#define MyAppExeName "KlipschRemote.exe"

[Setup]
; A stable AppId keeps upgrades/uninstall clean across versions - never change it.
AppId={{8F3E1C2A-5B6D-4E7F-9A0B-1C2D3E4F5A6B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Per-user install: no admin/UAC prompt, installs under %LocalAppData%\Programs.
; Pinning to the taskbar works the same as a machine-wide install.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; The bundle is 64-bit only.
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
; The setup .exe's own icon (ship our OWN art, not Klipsch's trademarked icon).
SetupIconFile=klipsch_remote\assets\icon.ico
WizardStyle=modern
Compression=zip
SolidCompression=no
OutputDir=dist_installer
OutputBaseFilename=KlipschRemote-Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Registry]
; Legacy autostart cleanup: older builds registered "Launch on startup" as this
; HKCU Run value (now superseded by a Scheduled Task — see autostart.py). The
; installer never creates it (ValueType: none); it only declares it for removal
; so uninstall doesn't leave a stale entry pointing at the deleted exe. Value
; name must match _WIN_RUN_VALUE in autostart.py.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: none; ValueName: "KlipschRemote"; Flags: uninsdeletevalue

[UninstallRun]
; Remove the launch-at-logon Scheduled Task the app creates (autostart.py,
; _WIN_TASK_NAME) so uninstall leaves nothing pointing at the deleted exe.
; runhidden + no-window flags keep it silent; failures are ignored (the task may
; never have been created if the user never enabled startup).
Filename: "{cmd}"; Parameters: "/c schtasks /Delete /TN ""KlipschRemote"" /F"; Flags: runhidden; RunOnceId: "DelKlipschTask"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; The whole native bundle - keep it together (exe + DLLs + bundled Python +
; data\). recursesubdirs/createallsubdirs pull in data\, DLLs\, Lib\, etc.
Source: "dist_app\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
