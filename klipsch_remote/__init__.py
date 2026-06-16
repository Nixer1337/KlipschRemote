"""klipsch-remote — a cross-platform Flet desktop remote for Klipsch powered speakers.

A thin graphical front-end over :mod:`klipsch_ble`. Run it with
``python -m klipsch_remote``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "0.1.0"

__all__ = ["KlipschRemote", "__version__", "main", "run"]

# ``app`` imports Flet (a heavy GUI dependency). Import it lazily so Flet-free
# submodules — e.g. ``klipsch_remote.viewstate`` — can be imported and unit-tested
# without Flet installed (the lean pytest CI has none). Accessing KlipschRemote /
# main / run still works and pulls Flet in on first use.
_LAZY = frozenset({"KlipschRemote", "main", "run"})


def __getattr__(name: str) -> object:
    if name in _LAZY:
        from . import app
        return getattr(app, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return [*globals(), *_LAZY]


if TYPE_CHECKING:  # let type-checkers and IDEs still see the lazily-exported names
    from .app import KlipschRemote, main, run
