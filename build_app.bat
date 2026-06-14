@echo off
REM Native build (flet build windows) -> dist_app\ [+ dist_installer\KlipschRemote-Setup.exe]
REM
REM Defaults to FAST incremental: reuses the .build_app build cache and compiles
REM the native C++ across all CPU cores, so a Python-only change rebuilds in
REM seconds instead of a full from-scratch compile. Extra args are forwarded.
REM   build_app.bat                 fast build: bundle + installer (default)
REM   build_app.bat -NoInstaller    fast build: folder bundle only
REM
REM For a full clean build (what you ship - no stale-cache risk), run the script
REM directly without -Fast:
REM   powershell -ExecutionPolicy Bypass -File build_app.ps1
powershell -ExecutionPolicy Bypass -File "%~dp0build_app.ps1" -Fast %*
