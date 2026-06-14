<#
Builds the CANONICAL native KlipschRemote with `flet build windows`: a real
Flutter app whose executable IS the program. The window/taskbar icon is baked
into the binary and the app gets a stable Windows identity, so "Pin to taskbar"
works - and the same `flet build` path gives a proper .app on macOS and a bundle
on Linux.

By default this also compiles the Inno Setup installer, so one run yields a
ready-to-ship KlipschRemote-Setup.exe. Pass -NoInstaller to stop after the folder
bundle (dist_app\, the executable plus all its dependencies) and skip the .exe.

Prerequisites (one-time):
  - Visual Studio with the "Desktop development with C++" workload
  - Inno Setup 6 for the installer (skipped automatically if absent, or with
    -NoInstaller). Install with: winget install JRSoftware.InnoSetup
  (flet build downloads and manages its own pinned Flutter SDK automatically.)

Usage (from this folder):
  powershell -ExecutionPolicy Bypass -File build_app.ps1                 # bundle + installer
  powershell -ExecutionPolicy Bypass -File build_app.ps1 -NoInstaller    # folder bundle only
  powershell -ExecutionPolicy Bypass -File build_app.ps1 -Fast -NoInstaller  # fast incremental dev rebuild
Output:
  dist_app\                              (folder bundle - the exe + dependencies)
  dist_installer\KlipschRemote-Setup.exe (installer, unless -NoInstaller)
#>
param(
    # Skip the installer and produce only the dist_app\ folder bundle.
    [switch]$NoInstaller,
    # Incremental rebuild: reuse the existing .build_app\build cache instead of
    # wiping it, so only changed sources are rebuilt (the native C++ runner is
    # reused untouched for a Python-only change). Much faster for dev iteration;
    # use a plain (clean) run for a release build you intend to ship.
    [switch]$Fast
)

# Native build tools write progress to stderr; under $ErrorActionPreference="Stop"
# PowerShell 5.1 would turn that into a terminating error even on success. We
# check $LASTEXITCODE explicitly instead. NOTE: keep this file ASCII-only - PS 5.1
# reads .ps1 as ANSI, so a stray Unicode dash breaks the parser.
Set-Location -Path $PSScriptRoot

# flet build manages its OWN pinned Flutter SDK (it installs the exact version it
# needs under the flet cache). We deliberately do NOT put a system Flutter on PATH
# - a different version there only causes a version-mismatch reinstall prompt.

# flet build's progress UI (rich) writes Unicode glyphs; on a non-UTF-8 console
# code page (e.g. Russian cp1251) its legacy-Windows renderer dies with a charmap
# UnicodeEncodeError. Force Python into UTF-8 mode so stdout can encode them.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# Build the native C++ (runner + plugins) across all cores. `cmake --build`,
# which flutter invokes under the hood, honours this env var as its -j level.
$env:CMAKE_BUILD_PARALLEL_LEVEL = [Environment]::ProcessorCount

# --- Stage a clean flet-build project (source stays single-sourced) -----------
$Stage = Join-Path $PSScriptRoot ".build_app"
$Src   = Join-Path $Stage "src"
if ($Fast -and (Test-Path (Join-Path $Stage "build\flutter"))) {
    # Incremental: keep build\ (the Flutter/CMake/C++ cache) and refresh only the
    # staged sources, so a Python-only change skips the full native recompile.
    Write-Host "==> -Fast: reusing existing .build_app\build cache (incremental)."
    if (Test-Path $Src) { Remove-Item -Recurse -Force $Src }
} else {
    if ($Fast) { Write-Host "==> -Fast requested but no cache yet - doing a full first build." }
    if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
}
New-Item -ItemType Directory -Force -Path $Src | Out-Null

Write-Host "==> Staging sources..."
Copy-Item (Join-Path $PSScriptRoot "packaging\pyproject.toml") $Stage
Copy-Item (Join-Path $PSScriptRoot "packaging\main.py")        $Src
Copy-Item -Recurse (Join-Path $PSScriptRoot "klipsch_remote") $Src
Copy-Item -Recurse (Join-Path $PSScriptRoot "klipsch_ble") $Src
# Don't ship stale bytecode.
Get-ChildItem -Path $Src -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force

# flet generates the native window/taskbar icon from <app>/assets/icon.png.
$Assets = Join-Path $Src "assets"
New-Item -ItemType Directory -Force -Path $Assets | Out-Null
Copy-Item (Join-Path $PSScriptRoot "klipsch_remote\assets\icon.png") (Join-Path $Assets "icon.png")

# --- Build --------------------------------------------------------------------
Write-Host "==> Running flet build windows (first run compiles Flutter, a few minutes)..."
Push-Location $Stage
flet build windows `
    --artifact "KlipschRemote" `
    --product "Klipsch Remote" `
    --org "com.unofficial" `
    --company "Unofficial" `
    --copyright "Unofficial. Not affiliated with or endorsed by Klipsch." `
    --no-rich-output `
    --yes
$BuildExit = $LASTEXITCODE
Pop-Location
if ($BuildExit -ne 0) { Write-Error "flet build failed (exit $BuildExit)."; exit 1 }

# --- Patch the native runner: start the window HIDDEN at the right size --------
# The generated Flutter runner creates the window at a hardcoded 1280x720 and
# Show()s it on the first frame - BEFORE our Python main() can size/centre it, so
# a big empty window flashes. The runner already supports a hidden start, but only
# via the FLET_HIDE_WINDOW_ON_START env var, which would have to be present at
# process launch - we can't guarantee that for a double-clicked exe. So we force
# hidden-start in the runner C++ (false || -> true ||) and set a sane initial
# size, then recompile just the runner. main() reveals the window once it's built
# (window.visible=True after wait_until_ready_to_show + center) - no flash.
$Runner = Join-Path $Stage "build\flutter\windows\runner"
$fw = Join-Path $Runner "flutter_window.cpp"
$mc = Join-Path $Runner "main.cpp"
Write-Host "==> Patching runner for hidden, centred, correctly-sized window start..."

# C++ block injected into FlutterWindow::OnCreate (runs while the window is still
# hidden) that centres the window on its monitor's WORK area, using the real
# (DPI-scaled) window rect - so it is revealed already centred, no corner flash.
$centerBlock = @'
  {
    HWND _h = GetHandle();
    RECT _wr;
    GetWindowRect(_h, &_wr);
    HMONITOR _mon = MonitorFromWindow(_h, MONITOR_DEFAULTTONEAREST);
    MONITORINFO _mi;
    _mi.cbSize = sizeof(_mi);
    if (GetMonitorInfo(_mon, &_mi)) {
      int _x = _mi.rcWork.left + ((_mi.rcWork.right - _mi.rcWork.left) - (_wr.right - _wr.left)) / 2;
      int _y = _mi.rcWork.top + ((_mi.rcWork.bottom - _mi.rcWork.top) - (_wr.bottom - _wr.top)) / 2;
      SetWindowPos(_h, nullptr, _x, _y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE);
    }
  }
  RECT frame = GetClientArea();
'@
$anchor = 'RECT frame = GetClientArea();'

# Literal .Replace() (not regex -replace) so the parens/newlines pass through.
$fwTxt = [System.IO.File]::ReadAllText($fw)
$fwTxt = $fwTxt.Replace('false ||', 'true ||')   # force hidden start
$fwTxt = $fwTxt.Replace($anchor, $centerBlock)    # centre while hidden
[System.IO.File]::WriteAllText($fw, $fwTxt)
$mcTxt = [System.IO.File]::ReadAllText($mc).Replace('Size size(1280, 720)', 'Size size(460, 860)')
[System.IO.File]::WriteAllText($mc, $mcTxt)

# The runner C++ Show() is only half the story: flet's own Dart bootstrap
# (setupDesktop) calls windowManager.show()+focus() during the ~2s Python boot
# UNLESS hide_window_on_start is true OR FLET_HIDE_WINDOW_ON_START is set in the
# environment. `flet build` does NOT expose this (it hard-codes the template
# value to "None" -> false), and the env var is only set by the desktop client
# we don't use here, so the window would pop up EMPTY while Python loads, then
# our reveal would hide+show it (an empty window that flashes and re-opens under
# other windows). We patch the generated lib/main.dart so window_manager keeps
# the window hidden until our main() shows it - centred, focused, on top.
$md = Join-Path $Stage "build\flutter\lib\main.dart"
$mdTxt = [System.IO.File]::ReadAllText($md).Replace(
    'final hideWindowOnStart = bool.tryParse("None".toLowerCase()) ?? false;',
    'final hideWindowOnStart = true;')
[System.IO.File]::WriteAllText($md, $mdTxt)

# Recompile only the runner with flet's OWN pinned Flutter (resolve flutter.bat
# just for this step; don't pollute PATH). The Python bundle staged by the first
# pass is reused, so this is an incremental C++ rebuild.
$FlutterBat = Get-ChildItem (Join-Path $env:USERPROFILE "flutter") -Recurse -Filter "flutter.bat" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $FlutterBat) { Write-Error "flet's managed Flutter (flutter.bat) not found under $env:USERPROFILE\flutter."; exit 1 }
Write-Host "==> Recompiling runner ($($FlutterBat.FullName))..."
Push-Location (Join-Path $Stage "build\flutter")
& $FlutterBat.FullName build windows --release
$ReExit = $LASTEXITCODE
Pop-Location
if ($ReExit -ne 0) { Write-Error "runner recompile failed (exit $ReExit)."; exit 1 }

# --- Collect the bundle -------------------------------------------------------
$BuildOut = Join-Path $Stage "build\flutter\build\windows\x64\runner\Release"
if (-not (Test-Path $BuildOut)) { Write-Error "Build reported success but $BuildOut is missing."; exit 1 }

$DistApp = Join-Path $PSScriptRoot "dist_app"
New-Item -ItemType Directory -Force -Path $DistApp | Out-Null
# Clear the CONTENTS (not the folder itself): Explorer / the search indexer often
# hold a handle on the dist_app directory, which makes `Remove-Item $DistApp` fail
# with "being used by another process" - but removing its children still works.
# A running KlipschRemote.exe from a previous launch WOULD lock the exe; stop it.
Get-Process -Name KlipschRemote -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $DistApp -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Copy-Item -Recurse (Join-Path $BuildOut "*") $DistApp
# Verify the freshly built exe actually landed (catches a stale lock on the exe).
$built = Join-Path $BuildOut "KlipschRemote.exe"
$dest  = Join-Path $DistApp "KlipschRemote.exe"
if ((Test-Path $dest) -and ((Get-Item $dest).Length -ne (Get-Item $built).Length)) {
    Write-Error "dist_app\KlipschRemote.exe wasn't replaced (locked?). Close any running copy and rebuild."
    exit 1
}

$Exe = Get-ChildItem -Path $DistApp -Recurse -Filter "KlipschRemote.exe" | Select-Object -First 1
Write-Host ""
if ($Exe) {
    $mb = [math]::Round((Get-ChildItem -Path $DistApp -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB, 1)
    Write-Host "==> Done. $($Exe.FullName)"
    Write-Host "    Native Flutter bundle (~$mb MB) - pinnable to the taskbar, own icon and identity."
    Write-Host "    Ship the whole dist_app\ folder together (the exe + its dependencies)."
} else {
    Write-Warning "Build finished but KlipschRemote.exe was not found under $DistApp."
}

# --- Compile the installer from the freshly built bundle ----------------------
# By default one run yields BOTH a current bundle and a current installer:
# building dist_app\ without recompiling installer.iss is exactly how
# dist_installer\KlipschRemote-Setup.exe goes stale (the bundle updates but the
# Setup.exe people install keeps the old code). -NoInstaller stops here, leaving
# just the folder bundle. (installer.iss packages dist_app\*, so the installer
# always matches what we just built.)
if ($NoInstaller) {
    Write-Host ""
    Write-Host "==> -NoInstaller: skipped installer. Folder bundle is in dist_app\."
} else {
    # Check the usual install roots: machine-wide (Program Files), and the per-user
    # location winget uses for JRSoftware.InnoSetup (%LOCALAPPDATA%\Programs).
    $IsccCandidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles        "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:LOCALAPPDATA        "Programs\Inno Setup 6\ISCC.exe")
    )
    $Iscc = $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $Iscc) {
        $cmd = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
        if ($cmd) { $Iscc = $cmd.Source }
    }
    if ($Iscc -and (Test-Path $Iscc)) {
        Write-Host ""
        Write-Host "==> Compiling installer (installer.iss)..."
        & $Iscc (Join-Path $PSScriptRoot "installer.iss")
        if ($LASTEXITCODE -ne 0) { Write-Error "ISCC failed (exit $LASTEXITCODE)."; exit 1 }
        $Setup = Join-Path $PSScriptRoot "dist_installer\KlipschRemote-Setup.exe"
        if (Test-Path $Setup) {
            $smb = [math]::Round((Get-Item $Setup).Length / 1MB, 1)
            Write-Host "==> Installer: $Setup (~$smb MB) - reinstall with this to update the installed app."
        }
    } else {
        Write-Warning "Inno Setup (ISCC.exe) not found - skipped installer build. The dist_app\ bundle is current; install Inno Setup 6 (winget install JRSoftware.InnoSetup), or pass -NoInstaller to silence this."
    }
}
