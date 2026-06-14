"""Flet build entry point — launches the Klipsch Remote desktop app.

`flet build` runs this module as the program (top-level `run()` -> `ft.run(...)`).
The real app lives in the sibling `klipsch_remote` package; this only bootstraps it,
so the GUI source stays single-sourced with the `python -m klipsch_remote` dev path.
"""

from __future__ import annotations

from klipsch_remote.app import run

run()
