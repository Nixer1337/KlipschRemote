"""Single-instance guard + window-raise helper.

A fixed loopback port doubles as a cross-platform instance lock and a
signalling channel: the first instance binds it and listens; a later launch
fails to bind, pings the running instance to raise its window, and exits.
"""

from __future__ import annotations

import asyncio
import socket
import threading

import flet as ft

# A fixed loopback port is our cross-platform instance lock + signalling channel.
_INSTANCE_PORT = 49219          # arbitrary high 127.0.0.1 port, stable across runs
_INSTANCE_MAGIC = b"klipsch-remote/1"   # handshake token (don't false-trip on a
#                                         foreign app that happens to hold the port)


class SingleInstance:
    """Cross-platform single-instance guard over a loopback TCP socket.

    The first instance binds ``127.0.0.1:_INSTANCE_PORT`` and listens; a later
    instance fails to bind, connects to that port and (after a magic-token
    handshake) asks the running instance to raise its window, then exits. Works
    identically on Windows/macOS/Linux, and — unlike a PID/lock file — it can
    *signal* the running instance and auto-releases when the process dies (the
    OS closes the socket), so there's no stale lock to clean up.
    """

    def __init__(self) -> None:
        self._srv: socket.socket | None = None
        self._on_raise = None

    def acquire(self) -> bool:
        """Try to become the primary instance. True if we bound the port."""
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Deliberately NO SO_REUSEADDR: we WANT bind() to fail if another
        # instance is already listening (on every platform).
        try:
            srv.bind(("127.0.0.1", _INSTANCE_PORT))
        except OSError:
            srv.close()
            return False
        srv.listen(8)
        self._srv = srv
        threading.Thread(target=self._serve, daemon=True).start()
        return True

    def set_raise_handler(self, callback) -> None:
        """Register a thread-safe callback fired when a 2nd instance pings us."""
        self._on_raise = callback

    def signal_raise(self) -> bool:
        """Ask the already-running instance to come forward. True only if we
        actually reached *our* app (verified by the handshake reply)."""
        try:
            with socket.create_connection(
                    ("127.0.0.1", _INSTANCE_PORT), timeout=1.0) as c:
                c.settimeout(1.0)
                c.sendall(_INSTANCE_MAGIC + b" raise")
                reply = c.recv(64)
            return reply.startswith(_INSTANCE_MAGIC)
        except OSError:
            return False

    def release(self) -> None:
        if self._srv is not None:
            try:
                self._srv.close()  # unblocks accept() -> _serve() returns
            except OSError:
                pass
            self._srv = None

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()  # type: ignore[union-attr]
            except OSError:
                return  # socket closed by release() / shutdown
            fire = False
            try:
                conn.settimeout(1.0)
                if conn.recv(64).startswith(_INSTANCE_MAGIC):
                    conn.sendall(_INSTANCE_MAGIC + b" ok")
                    fire = True
            except OSError:
                pass
            finally:
                conn.close()
            if fire and self._on_raise is not None:
                try:
                    self._on_raise()
                except Exception:  # never kill the listener
                    pass


async def bring_to_front(page: ft.Page) -> None:
    """Un-minimise, reveal and focus the window (a 2nd launch was attempted)."""
    w = page.window
    w.minimized = False
    w.visible = True
    page.update()
    try:
        await w.to_front()
    except Exception:  # best-effort across platforms
        pass
    w.focused = True
    # Briefly pin on top to force the OS to actually raise + focus the window,
    # then release so it behaves like a normal window again.
    w.always_on_top = True
    page.update()
    await asyncio.sleep(0.25)
    w.always_on_top = False
    page.update()
