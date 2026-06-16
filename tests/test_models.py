"""Model identification: DIS number / hardware revision / name resolution."""

from __future__ import annotations

import pytest

from klipsch_ble import models as m
from klipsch_ble.models import KlipschModel as K


def test_reverse_lookups_derived_from_model_info():
    # The lookups must be built from MODEL_INFO (single source of truth), with
    # "first wins" so the shared Fives number resolves to the plain Fives.
    assert m.MODEL_BY_NUMBER == {
        "1067563": K.FIVES, "1067562": K.FIVES,
        "1071199": K.SEVENS, "1071202": K.SEVENS,
        "1071200": K.NINES, "1071201": K.NINES,
        "1071482": K.NINES_MCLAREN,
    }
    assert m.MODEL_BY_HW_REV == {
        1: K.FIVES, 2: K.FIVES, 3: K.FIVES_MCLAREN,
        4: K.SEVENS, 5: K.NINES, 8: K.NINES_MCLAREN,
    }


def test_shared_fives_number_refined_by_hw_revision():
    for fives_number in ("1067563", "1067562"):  # both Fives finish SKUs
        assert m.resolve_model(fives_number, "1") is K.FIVES
        assert m.resolve_model(fives_number, "2") is K.FIVES
        assert m.resolve_model(fives_number, "3") is K.FIVES_MCLAREN
        # Unknown/absent revision on a Fives number falls back to the plain Fives.
        assert m.resolve_model(fives_number, None) is K.FIVES
        assert m.resolve_model(fives_number, "99") is K.FIVES


@pytest.mark.parametrize("number, expected", [
    ("1071199", K.SEVENS), ("1071200", K.NINES), ("1071482", K.NINES_MCLAREN),
    # Alternate-finish SKUs that the app binds to the same template (per binary).
    ("1071202", K.SEVENS), ("1071201", K.NINES),
])
def test_distinct_model_numbers(number, expected):
    assert m.resolve_model(number) is expected


def test_hw_revision_alone_when_number_missing():
    assert m.resolve_model(None, "4") is K.SEVENS
    assert m.resolve_model(None, "5") is K.NINES


@pytest.mark.parametrize("name, expected", [
    ("The Fives", K.FIVES), ("the five", K.FIVES),
    ("Klipsch The Fives McLaren", K.FIVES_MCLAREN),
    ("The Sevens", K.SEVENS),
    ("The Nines", K.NINES), ("The Nines McLaren", K.NINES_MCLAREN),
    ("Living Room Speaker", K.UNKNOWN),
])
def test_name_fallback(name, expected):
    assert m.resolve_model(name=name) is expected


def test_everything_unknown_is_unknown():
    assert m.resolve_model() is K.UNKNOWN
    assert m.resolve_model("nonsense", "nonsense", "nonsense") is K.UNKNOWN


def test_model_metadata_coherent():
    # Every recognised model exposes the full feature set and the line inputs.
    for model in K:
        assert model.display_name
        assert isinstance(model.supports("volume"), bool)
    assert K.FIVES.supports("eqmode") and not K.FIVES.supports("nope")
