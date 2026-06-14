# klipsch-remote

A cross-platform **desktop remote** for Klipsch powered speakers — The Fives,
The Sevens and The Nines (incl. McLaren) — built with [Flet](https://flet.dev)
on top of the [`klipsch_ble`](../klipsch_ble/README.md) library. Same async
`KlipschClient` that drives the CLI, here behind a GUI.

It runs on Windows, Linux and macOS from one code path: Flet renders a native
desktop window and `klipsch_ble` talks BLE through bleak (WinRT / BlueZ /
CoreBluetooth), with the Windows WinRT fast-path backend for quick connects.

## Screens

- **Connect** — pick a paired Klipsch (enumerated off the OS), type an address,
  or scan the air. The chosen address is remembered in `~/.klipsch.json` (shared
  with the CLI).
- **Remote** — model + status header, volume slider + mute, input dropdown
  (TV/ARC · Bluetooth · Optical · Aux · USB · Phono), 3-band EQ (−10…+6), sound
  modes (night, dynamic bass, sub mute, sub invert), transport
  (prev / play-pause / next) and rename. The remote opens only once the full
  state has been read, so it never shows empty or stale controls.

## Requirements

- Python 3.9+
- `pip install flet bleak` (plus `winrt-Windows.Devices.Bluetooth` on Windows
  for the fast-path backend)
- The `klipsch_ble` package importable (it sits next to this one — run from the
  parent directory).
- The speaker paired with the OS as a Bluetooth **audio** device. Never pair or
  unpair it as an LE-only device — that breaks control. See the library README.

## Run

```sh
python -m klipsch_remote          # from the directory that contains klipsch_ble/ and klipsch_remote/
```

or, for Flet's hot-reload during development:

```sh
flet run klipsch_remote/app.py
```

## Build the app (Windows)

### Canonical native build — `flet build` (recommended)

From the parent directory (the one holding `klipsch_ble/` and `klipsch_remote/`):

```powershell
powershell -ExecutionPolicy Bypass -File build_app.ps1   # or double-click build_app.bat
```

This compiles a **real Flutter app** whose executable *is* the program, producing
the folder bundle `dist_app\` (KlipschRemote.exe + DLLs + bundled Python +
site-packages, ~76 MB — ship the **whole folder** together). Because the window
is owned by `KlipschRemote.exe` itself, the app has its own icon and a stable
Windows identity, so it **pins to the taskbar** correctly. The same `flet build`
path produces a proper `.app` on macOS and a bundle on Linux.

By default the build also compiles the installer (see below); pass `-NoInstaller`
(`build_app.bat -NoInstaller`) to stop after the folder bundle.

The script stages a clean Flet project under `.build_app\` (sources stay
single-sourced — your working layout is untouched), generates the native icon
from `klipsch_remote/assets/icon.png`, and copies the result to `dist_app\`.

**Prerequisites (one-time):** Visual Studio with the *Desktop development with
C++* workload, and **Windows Developer Mode** enabled (Flutter needs it to create
plugin symlinks: Settings → Privacy & security → For developers → Developer Mode).
`flet build` downloads and manages its own pinned Flutter SDK automatically. On a
fresh machine, install the toolchain plus the pinned deps with
`python -m pip install -r requirements.txt` from the repo root.

### Installer — Inno Setup (one `Setup.exe`)

The native build is a *folder* bundle. To hand someone a single file, `build_app`
wraps it in an installer **automatically** — it compiles `installer.iss` right
after `dist_app\` is built (skipped if Inno Setup isn't installed, or with
`-NoInstaller`). To build the installer on its own from an existing `dist_app\`:

```powershell
iscc installer.iss        # or: "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

Either way you get `dist_installer\KlipschRemote-Setup.exe` (~22 MB). It's a
per-user install (no admin/UAC prompt, lands under `%LocalAppData%\Programs`),
adds Start Menu (and optional desktop) shortcuts, and registers a clean
uninstall. Because the installed program is the native `KlipschRemote.exe`, it
pins to the taskbar. Install Inno Setup with `winget install JRSoftware.InnoSetup`.

### CI — GitHub Actions

`.github/workflows/build.yml` runs the whole pipeline on a `windows-latest`
runner (it already has the VS C++ workload and Inno Setup): it runs
`build_app.ps1`, compiles `installer.iss`, and uploads
`KlipschRemote-Setup.exe` as a build artifact. Push a `v*` tag and it also
attaches the installer to a GitHub Release. The installer is unsigned, so
SmartScreen shows an "unknown publisher" warning until it's code-signed.

To brand the build, drop your own art at `klipsch_remote/assets/icon.png` (and
`icon.ico`, used as the installer's icon) — ship your **own** icon, not Klipsch's
(trademarked).

## How async fits in

Flet runs its own asyncio loop, so the BLE coroutines are simply `await`-ed
inside the control handlers. Every speaker call goes through a single
`asyncio.Lock`, so concurrent GATT reads/writes never overlap on the one client.
Slow operations (paired-device enumeration, scanning, connecting, the initial
state read) run as `page.run_task(...)` with a status line / spinner so the UI
stays responsive. The volume and EQ sliders update their label live while
dragging and write to the speaker only on change-end, to avoid flooding the link.

## Design notes

- The UI never imports bleak directly — it only ever talks to `KlipschClient`
  and reuses the CLI's paired-device enumeration (`list_paired_bluetooth`, which
  lists every paired BT device so a renamed speaker still appears) and
  saved-address helpers, so behaviour matches the command line.
- Flet 0.85 wants list-valued selections (`SegmentedButton.selected: list[str]`)
  and `Dropdown(on_select=…)`; both are used here.

## License

Apache License 2.0. Unofficial project, not affiliated with Klipsch Group, Inc.;
trademarks belong to their owners. See [`LICENSE`](../LICENSE), [`NOTICE`](../NOTICE)
and the [project README](../README.md#-license--legal).
