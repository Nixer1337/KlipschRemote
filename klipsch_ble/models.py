"""Klipsch powered-speaker model identification and per-model capabilities.

The whole "CinemaStream" powered line — The Fives, The Sevens, The Nines (incl.
McLaren variants) — speaks one identical BLE control protocol (see
:mod:`klipsch_ble.constants`). The only real per-model variation is cosmetic
(name) plus which optional features a given firmware exposes. This module gives
callers a friendly model name, the input list, and a capability check.

Detection is driven by the **standard BLE Device Information Service**: the Model
Number string (``0x2A24``) is a stable product id reported by the speaker (it is
*not* the user-settable Klipsch name), so it survives the user renaming the unit.
The Hardware Revision (``0x2A27``) further distinguishes the Fives sub-variants.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .constants import Input


class KlipschModel(Enum):
    """A recognised Klipsch powered speaker (or ``UNKNOWN``)."""

    FIVES = "fives"
    FIVES_MCLAREN = "fives_mclaren"
    SEVENS = "sevens"
    NINES = "nines"
    NINES_MCLAREN = "nines_mclaren"
    UNKNOWN = "unknown"

    @property
    def info(self) -> ModelInfo:
        return MODEL_INFO[self]

    @property
    def display_name(self) -> str:
        return MODEL_INFO[self].display_name

    @property
    def inputs(self) -> tuple[Input, ...]:
        return MODEL_INFO[self].inputs

    def supports(self, feature: str) -> bool:
        """True if this model exposes ``feature`` (see :data:`FEATURES`)."""
        return feature in MODEL_INFO[self].features


# The control surface this library implements. Fives/Sevens/Nines all expose the
# full set (shared feature set + shared GATT table).
FEATURES: frozenset[str] = frozenset({
    "volume", "mute", "input", "bass", "mid", "treble",
    "night", "vocal", "dynamic_bass", "submute", "subinvert", "sub_level",
    "eqmode", "name", "play_pause", "next", "prev", "func_sounds",
    "factory_reset", "placement",
})

# The physical inputs available on the Fives/Sevens/Nines (identity byte map).
_LINE_INPUTS: tuple[Input, ...] = (
    Input.TV, Input.BLUETOOTH, Input.OPTICAL, Input.AUX, Input.USB, Input.PHONO,
)


@dataclass(frozen=True)
class ModelInfo:
    """Static description of one model."""

    model: KlipschModel
    display_name: str
    model_numbers: tuple[str, ...]          # DIS 0x2A24 values
    hardware_revs: tuple[int, ...] = ()      # DIS 0x2A27 values (if distinctive)
    inputs: tuple[Input, ...] = _LINE_INPUTS
    features: frozenset[str] = field(default=FEATURES)


# DIS Model Number (0x2A24) literals. The first (primary) number per product is
# from CinemaStreamUtil._clinit_; the second number on Fives/Sevens/Nines is the
# alternate-finish SKU that the matching <Product>DeviceTemplateKt._clinit_ binds
# to the very same device template (so it speaks the identical BLE protocol).
# Note: "The Fives" and "The Fives McLaren" share model number 1067563 and are
# told apart only by the Hardware Revision (V1=1, V2=2, McLaren=3); the second
# Fives SKU (1067562) resolves to FIVES and is refined by HW rev the same way.
MODEL_INFO: dict[KlipschModel, ModelInfo] = {
    KlipschModel.FIVES: ModelInfo(
        KlipschModel.FIVES, "The Fives", ("1067563", "1067562"),
        hardware_revs=(1, 2)),
    KlipschModel.FIVES_MCLAREN: ModelInfo(
        KlipschModel.FIVES_MCLAREN, "The Fives McLaren", ("1067563",),
        hardware_revs=(3,)),
    KlipschModel.SEVENS: ModelInfo(
        KlipschModel.SEVENS, "The Sevens", ("1071199", "1071202"),
        hardware_revs=(4,)),
    KlipschModel.NINES: ModelInfo(
        KlipschModel.NINES, "The Nines", ("1071200", "1071201"),
        hardware_revs=(5,)),
    KlipschModel.NINES_MCLAREN: ModelInfo(
        KlipschModel.NINES_MCLAREN, "The Nines McLaren", ("1071482",),
        hardware_revs=(8,)),
    KlipschModel.UNKNOWN: ModelInfo(
        KlipschModel.UNKNOWN, "Klipsch speaker", ()),
}

# Reverse lookups (model number / hardware revision -> model), derived from
# MODEL_INFO so the model table above stays the single source of truth — no
# parallel hand-maintained dict to drift out of sync. "First definition wins",
# so the Fives McLaren (which shares the Fives model number 1067563) does NOT
# clobber the plain Fives: a bare number resolves to FIVES and the Hardware
# Revision refines that one case (see :func:`resolve_model`).
def _build_reverse_lookups() -> tuple[
    dict[str, KlipschModel], dict[int, KlipschModel]
]:
    by_number: dict[str, KlipschModel] = {}
    by_rev: dict[int, KlipschModel] = {}
    for model, info in MODEL_INFO.items():
        for number in info.model_numbers:
            by_number.setdefault(number, model)
        for rev in info.hardware_revs:
            by_rev.setdefault(rev, model)
    return by_number, by_rev


MODEL_BY_NUMBER, MODEL_BY_HW_REV = _build_reverse_lookups()


def model_from_number(model_number: str | None) -> KlipschModel:
    """Map a DIS Model Number string (``0x2A24``) to a :class:`KlipschModel`."""
    if not model_number:
        return KlipschModel.UNKNOWN
    return MODEL_BY_NUMBER.get(model_number.strip(), KlipschModel.UNKNOWN)


def model_from_hw_revision(hw_revision: int | str | None) -> KlipschModel:
    """Map a DIS Hardware Revision (``0x2A27``) to a model, ``UNKNOWN`` if odd."""
    if hw_revision is None:
        return KlipschModel.UNKNOWN
    try:
        rev = int(str(hw_revision).strip())
    except ValueError:
        return KlipschModel.UNKNOWN
    return MODEL_BY_HW_REV.get(rev, KlipschModel.UNKNOWN)


def model_from_name(name: str | None) -> KlipschModel:
    """Best-effort model guess from an advertised / device name.

    Only a fallback when the Model Number is unavailable; the name may have been
    customised by the user, in which case this returns ``UNKNOWN``.
    """
    if not name:
        return KlipschModel.UNKNOWN
    low = name.lower()
    if "nines" in low or "the nine" in low:
        return KlipschModel.NINES_MCLAREN if "mclaren" in low else KlipschModel.NINES
    if "sevens" in low or "the seven" in low:
        return KlipschModel.SEVENS
    if "fives" in low or "the five" in low:
        return KlipschModel.FIVES_MCLAREN if "mclaren" in low else KlipschModel.FIVES
    return KlipschModel.UNKNOWN


def resolve_model(
    model_number: str | None = None,
    hw_revision: int | str | None = None,
    name: str | None = None,
) -> KlipschModel:
    """Combine the available signals into a single best model guess.

    Priority: Model Number (refined by HW revision for the shared Fives id) ->
    HW revision alone -> name -> ``UNKNOWN``.
    """
    by_number = model_from_number(model_number)
    if by_number is KlipschModel.FIVES:
        # 1067563 covers Fives V1/V2/McLaren; refine with the hardware revision.
        refined = model_from_hw_revision(hw_revision)
        if refined in (KlipschModel.FIVES, KlipschModel.FIVES_MCLAREN):
            return refined
        return by_number
    if by_number is not KlipschModel.UNKNOWN:
        return by_number
    by_rev = model_from_hw_revision(hw_revision)
    if by_rev is not KlipschModel.UNKNOWN:
        return by_rev
    return model_from_name(name)
