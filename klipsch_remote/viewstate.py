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

from collections.abc import Container, Mapping
from typing import NamedTuple, Protocol

from klipsch_ble import KlipschAccessError, KlipschNotFoundError

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


# ---- connect screen ---------------------------------------------------------
def connect_error_message(exc: BaseException) -> str:
    """Map a failed-connection exception to the line shown on the connect screen.

    An access error is the actionable one (the speaker is paired as LE / "Other"
    instead of as an audio device), so it gets the fix-it guidance; a not-found
    error already carries a useful message; anything else is shown verbatim,
    prefixed with its type so an unexpected failure is still legible.
    """
    if isinstance(exc, KlipschAccessError):
        return ("No control access — pair the speaker as an AUDIO device "
                "(not 'Other'/LE). Never unpair.")
    if isinstance(exc, KlipschNotFoundError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


class PickResult(NamedTuple):
    """What the connect screen should preselect after enumerating paired devices.

    Each field is an address to apply, or ``None`` to leave that control as-is."""

    select: str | None    # address to select in the paired dropdown
    autofill: str | None  # address to write into the address field


def pick_paired_device(addresses: list[str], typed: str) -> PickResult:
    """Decide which paired device the connect screen should preselect.

    If the typed / saved address is among the paired devices, select it (matched
    case-insensitively) and leave the field alone -- so after auto-connect the
    right device shows selected. Otherwise, if exactly one device is paired,
    select *and* autofill it. With zero, or several unmatched, devices: change
    nothing.
    """
    typed_u = typed.strip().upper()
    if typed_u:
        for addr in addresses:
            if addr.upper() == typed_u:
                return PickResult(select=addr, autofill=None)
    if len(addresses) == 1:
        return PickResult(select=addresses[0], autofill=addresses[0])
    return PickResult(select=None, autofill=None)


# ---- remote screen ----------------------------------------------------------
class Status(Protocol):
    """The subset of ``klipsch_ble.KlipschStatus`` that :func:`reconcile` reads."""

    input: str
    volume_raw: int
    mute: bool | None
    bass: int | None
    mid: int | None
    treble: int | None
    night: bool | None
    dynamic_bass: bool | None
    sub_detected: bool | None
    sub_level_db: int | None
    sub_invert: bool | None
    sub_mute: bool | None


class RemoteView(NamedTuple):
    """The control values the remote screen should apply for one speaker status.

    ``input`` and ``eq`` are ``None`` when the status doesn't pin them down -- the
    caller then leaves that control as-is rather than blanking it. ``sub_detected``
    and ``sub_level_db`` carry their tri-state / optional straight through to the
    sub reflectors, which interpret them; the remaining flags are plain bools.
    """

    name: str
    volume_raw: int
    mute: bool
    input: str | None
    eq: tuple[int, int, int] | None
    night: bool
    dynamic_bass: bool
    sub_detected: bool | None
    sub_level_db: int | None
    sub_invert: bool
    sub_mute: bool


def reconcile(status: Status, raw_name: str | None, model_name: str,
              valid_inputs: Container[str]) -> RemoteView:
    """Turn a freshly-read speaker status into the values the remote should show.

    The decisions this pins down (which used to live inline in
    ``app._load_state``): the name falls back to the model's display name when the
    speaker reports none; an unrecognised ``input`` is dropped (leave the tile
    highlight alone) instead of highlighting nothing; the EQ is applied only when
    all three bands were read -- a half-read EQ is discarded, not shown as zeros;
    the mode / sub flags are coerced from the wire's ``bool | None`` to bools.
    """
    b, m, t = status.bass, status.mid, status.treble
    eq = (b, m, t) if b is not None and m is not None and t is not None else None
    return RemoteView(
        name=raw_name or model_name,
        volume_raw=status.volume_raw,
        mute=bool(status.mute),
        input=status.input if status.input in valid_inputs else None,
        eq=eq,
        night=bool(status.night),
        dynamic_bass=bool(status.dynamic_bass),
        sub_detected=status.sub_detected,
        sub_level_db=status.sub_level_db,
        sub_invert=bool(status.sub_invert),
        sub_mute=bool(status.sub_mute),
    )
