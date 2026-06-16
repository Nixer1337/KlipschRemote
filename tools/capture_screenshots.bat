@echo off
REM Capture README screenshots of Klipsch Remote into docs\screenshots\.
REM Runs the app in offline demo mode (no speaker needed) and grabs each screen.
REM Usage:  tools\capture_screenshots.bat            (all screens)
REM         tools\capture_screenshots.bat -Screens equalizer,about
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0capture_screenshots.ps1" %*
exit /b %ERRORLEVEL%
