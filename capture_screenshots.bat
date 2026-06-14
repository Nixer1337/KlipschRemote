@echo off
REM Capture README screenshots of Klipsch Remote into docs\screenshots\.
REM Runs the app in offline demo mode (no speaker needed) and grabs each screen.
REM Usage:  capture_screenshots.bat            (all screens)
REM         capture_screenshots.bat -Screens equalizer,about
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\capture_screenshots.ps1" %*
exit /b %ERRORLEVEL%
