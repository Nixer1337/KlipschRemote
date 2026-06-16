"""Pure view-state derivations for the desktop remote.

Each function here maps a piece of *device* state to what the *UI* should show:
the EQ-preset round-trip, the tri-state subwoofer-detection display, the dB
label, and the speaker-placement copy. They are deliberately free of Flet -- the
screen code in :mod:`klipsch_remote.app` calls them and applies the result to its
controls, but the decisions themselves are plain-data-in / plain-data-out.

Keeping them here (rather than tangled in ``app.py``'s 1400-line widget class)
means they're unit-testable without a window, a page, or even Flet installed --
i.e. in the dependency-light pytest CI that ``app.py``'s own widget tests (gated
on ``importorskip("flet")``) skip. That's also why the package ``__init__`` imports
Flet lazily: so this module can be imported on its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import NamedTuple

# (bass, mid, treble), the EQ band triple in the -10..+6 range.
EqBands = tuple[int, int, int]


def match_eq_preset(presets: Mapping[str, EqBands],
                    bass: int, mid: int, treble: int) -> str | None:
    """Return the preset name whose ``(bass, mid, treble)`` equals these three
    bands, or ``None`` when none match (the caller then shows its "Custom" label).

    ``presets`` is passed in -- the app hands over ``theme.EQ_PRESETS`` -- so this
    module stays independent of the Flet-laden ``theme``.
    """
    target = (bass, mid, treble)
    for name, vals in presets.items():
        if tuple(vals) == target:
            return name
    return None


class SubDisplay(NamedTuple):
    """How the subwoofer card should look for a given detection state."""

    present: bool   # is a sub actually connected?
    status: str     # label shown by the section title ("" when present)
    opacity: float  # 1.0 when live, 0.38 (Material disabled content) when greyed


def sub_detection(detected: bool | None) -> SubDisplay:
    """Map the tri-state detection flag to the card's display state.

    ``detected`` is ``True`` (sub present), ``False`` (confirmed absent) or
    ``None`` (the status read failed / was absent). The last two both mean "not
    detected", so the card greys out -- only an explicit ``True`` lights it up.
    """
    present = detected is True
    return SubDisplay(
        present=present,
        status="" if present else "Not detected",
        opacity=1.0 if present else 0.38,
    )


def format_db(db: int) -> str:
    """The standard ``"N dB"`` label shared by the sub-level row and its slider."""
    return f"{db} dB"


# Speaker-placement (boundary-gain) copy: the live description shown under the
# selector for the current choice. The byte each maps to is the bass gain the
# speaker adds -- most when free-standing, least in a corner (where the room
# already reinforces bass). See klipsch_ble.constants.Placement.
PLACEMENT_HINT: dict[str, str] = {
    "corner": "In a corner the room reinforces bass the most — the speaker adds "
              "the least.",
    "wall": "Against a wall the room adds some bass — the speaker adds a "
            "moderate amount.",
    "open": "Free-standing, away from walls — no room reinforcement, so the "
            "speaker adds the most bass.",
}


def placement_hint(name: str) -> str:
    """The copy for a placement choice, falling back to the ``"wall"`` default for
    an unknown name (matching the segmented button's default selection)."""
    return PLACEMENT_HINT.get(name, PLACEMENT_HINT["wall"])
