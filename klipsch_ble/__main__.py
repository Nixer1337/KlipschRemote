"""``python -m klipsch_ble`` -> the optional interactive/one-shot CLI.

Equivalent to the ``klipsch`` / ``klipsch-ble`` console scripts. The library
itself (``klipsch_ble.KlipschClient``) has no CLI dependency; this is just a
convenience front-end.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    main()
