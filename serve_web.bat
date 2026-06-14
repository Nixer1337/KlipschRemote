@echo off
setlocal EnableExtensions

REM ---------------------------------------------------------------------------
REM  Run the web/ remote on a local test server.
REM
REM  Web Bluetooth treats http://localhost as a secure context, so it works here
REM  without HTTPS. Open the page in Chrome, Edge or Opera. Press Ctrl+C to stop.
REM
REM  Usage:  serve_web.bat            (port 8000)
REM          serve_web.bat 9000       (custom port)
REM ---------------------------------------------------------------------------

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8000"
set "ROOT=%~dp0web"

REM Find Python 3: prefer the 'py' launcher, then 'python' on PATH.
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
  python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3 was not found on PATH.
  echo Install it from https://www.python.org/downloads/ ^(tick "Add to PATH"^).
  echo.
  pause
  exit /b 1
)

echo.
echo   KlipschRemote web - local test server
echo   Serving : %ROOT%
echo   URL     : http://localhost:%PORT%/
echo   Stop    : Ctrl+C
echo.

REM Open the browser (server binds far faster than the browser launches).
start "" "http://localhost:%PORT%/"

REM Serve the web/ folder (blocking; Ctrl+C stops it).
%PY% -m http.server %PORT% --directory "%ROOT%"

endlocal
