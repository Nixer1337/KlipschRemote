"""klipsch-remote — a cross-platform Flet desktop remote for Klipsch powered speakers.

A thin graphical front-end over :mod:`klipsch_ble`. Run it with
``python -m klipsch_remote``.
"""

from __future__ import annotations

from .app import KlipschRemote, main, run

__version__ = "0.1.0"

__all__ = ["KlipschRemote", "main", "run", "__version__"]
