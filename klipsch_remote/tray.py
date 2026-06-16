"""System-tray icon: Windows close-to-tray with a right-click Quit.

Optional / Windows-only — the pystray + Pillow import is guarded so dev installs
on Linux/macOS without those packages still import and run (the tray is simply
skipped there). The icon image is injected by the caller (``icon_path``) so this
module stays decoupled from how the app locates its assets.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time

try:
    import pystray
    from PIL import Image
except ImportError:  # pragma: no cover - exercised only where pystray is absent
    pystray = None
    Image = None

# The close-to-tray feature only exists on Windows and only when pystray is
# installed — elsewhere the setting is hidden and the window's X always quits.
TRAY_SUPPORTED = sys.platform == "win32" and pystray is not None

# Default for a fresh install: close-to-tray on (preserves the original
# always-on-tray behaviour). Used when no close_to_tray choice is saved yet.
TRAY_DEFAULT = True

# How long to wait for the shell's notification area before giving up. A login
# autostart can start before the taskbar exists; Shell_NotifyIcon(NIM_ADD) then
# fails SILENTLY (pystray's wrapper has no errcheck — it just returns FALSE and
# pystray still flips visible=True), so the icon never appears and nothing
# raises. We therefore gate on the tray window actually existing first.
_READY_RETRIES = 120        # attempts
_READY_INTERVAL = 0.5       # seconds between attempts (~60s total)


def _shell_tray_ready() -> bool:
    """True once the shell's notification area (Shell_TrayWnd) exists."""
    try:
        return bool(ctypes.windll.user32.FindWindowW("Shell_TrayWnd", None))
    except Exception:  # treat any probe failure as "not ready"
        return False


def start_tray(*, icon_path: str | None, on_show, on_quit,
               on_ready=None, on_fail=None) -> pystray.Icon | None:
    """Create the system-tray icon (Windows close-to-tray + right-click Quit).

    Returns the running ``pystray.Icon`` (its event loop runs on a daemon
    thread) or ``None`` when tray support is unavailable. The menu callbacks
    run on pystray's own thread, so they hand control back to the Flet event
    loop via the thread-safe ``on_show`` / ``on_quit`` callables the caller
    wires up. ``Show`` is the default item, so a left-click / double-click on
    the tray icon also reveals the window.

    Robustness: pystray's *default* setup just does ``icon.visible = True`` once
    on a side thread and swallows any failure — so if the notification area
    isn't ready yet (typical for a login-time autostart) the icon silently never
    appears. We pass our own setup that *retries* the registration, then calls
    ``on_ready`` once the icon is actually shown (or ``on_fail`` if it never
    takes). The caller uses that signal to keep the window hidden only when the
    tray truly came up, and to fall back to showing the window otherwise — so
    the app is never left hidden AND icon-less (an unreachable process).
    """
    if pystray is None or Image is None:
        if on_fail:
            on_fail()
        return None
    if icon_path:
        image = Image.open(icon_path)
    else:  # fallback: a small solid-blue square so the tray still appears
        image = Image.new("RGBA", (64, 64), (138, 180, 248, 255))
    menu = pystray.Menu(
        pystray.MenuItem("Show Klipsch Remote", lambda _i, _it: on_show(),
                         default=True),
        pystray.MenuItem("Quit", lambda _i, _it: on_quit()),
    )
    icon = pystray.Icon("klipsch-remote", image, "Klipsch Remote", menu)

    def _setup(ic) -> None:
        # Runs on pystray's setup thread once the message loop is live. Wait for
        # the notification area to exist before adding the icon — adding it while
        # the shell is still coming up at login fails silently and the icon is
        # lost. (pystray's window, created by run(), already listens for the
        # shell's TaskbarCreated broadcast, so a taskbar that appears even later
        # still triggers a re-add as a backstop.)
        ready = False
        for _ in range(_READY_RETRIES):
            if _shell_tray_ready():
                ready = True
                break
            time.sleep(_READY_INTERVAL)
        if ready:
            try:
                ic.visible = True
            except Exception:  # leave visible False -> on_fail
                pass
        if ic.visible:
            if on_ready:
                on_ready()
        elif on_fail:
            on_fail()

    def _run() -> None:
        try:
            icon.run(setup=_setup)
        except Exception:  # whole tray loop failed to start
            if on_fail:
                on_fail()
    threading.Thread(target=_run, daemon=True).start()
    return icon
