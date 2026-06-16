"""Window + system-tray lifecycle for the desktop remote.

Split out of ``KlipschRemote``: the tray icon, its off-thread menu callbacks, and
the window-close policy (hide-to-tray vs real quit) are one self-contained
concern. The owner constructs a :class:`TrayLifecycle` with the Flet page, an
async ``cleanup`` to run before the process exits (tear the BLE link down
cleanly), and the tray-icon path; everything tray/window-related then lives here
behind a small interface (``ensure`` / ``remove`` / ``quit`` / ``on_window_event``
/ ``active``).

``tray.py`` stays the low-level pystray wrapper; this is the orchestration on top.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import flet as ft

from .single_instance import bring_to_front
from .tray import TRAY_SUPPORTED, start_tray


class TrayLifecycle:
    """Owns the system-tray icon and the window close-to-tray behaviour."""

    def __init__(self, page: ft.Page, *,
                 cleanup: Callable[[], Awaitable[None]],
                 icon_path: str | None) -> None:
        self.page = page
        self._cleanup = cleanup        # run before the window is destroyed
        self._icon_path = icon_path
        self._tray = None              # the running pystray.Icon, or None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def active(self) -> bool:
        """True while the tray icon runs (so the window's X hides, not quits)."""
        return self._tray is not None

    def ensure(self, *, on_ready=None, on_fail=None) -> None:
        """Start the tray icon if it isn't already running (no-op otherwise).

        ``on_ready`` / ``on_fail`` (fired from the tray's worker thread) report
        whether the icon actually registered, so a silent autostart can fall back
        to showing the window instead of staying hidden with no icon.
        """
        if self._tray is not None or not TRAY_SUPPORTED:
            if on_fail:
                on_fail()
            return
        # Captured here (on the Flet event loop) so the tray's off-thread menu
        # callbacks can marshal back onto it.
        self._loop = asyncio.get_running_loop()
        self._tray = start_tray(
            icon_path=self._icon_path,
            on_show=lambda: self._loop.call_soon_threadsafe(
                self.page.run_task, bring_to_front, self.page),
            on_quit=lambda: self._loop.call_soon_threadsafe(
                self.page.run_task, self.quit),
            on_ready=on_ready,
            on_fail=on_fail,
        )

    def remove(self) -> None:
        """Stop and clear the tray icon (with it gone, the X quits the app)."""
        if self._tray is not None:
            self._tray.stop()
            self._tray = None

    async def quit(self) -> None:
        """The single real-exit path (window X without a tray, or tray Quit).

        Runs the owner's ``cleanup`` (tear the BLE link down cleanly) first —
        without it the process just dies and Windows takes a couple of seconds to
        release the GATT link, so a quick relaunch hits a speaker still
        "connected" to the dead session and the reconnect fails.
        """
        await self._cleanup()
        self.remove()
        await self.page.window.destroy()

    async def on_window_event(self, e: ft.WindowEvent) -> None:
        """Window-close policy: with a tray, X hides; without one, X quits."""
        if e.type != ft.WindowEventType.CLOSE:
            return
        if self._tray is not None:
            self.page.window.visible = False
            self.page.update()
            return
        await self.quit()
