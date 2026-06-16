"""Tests for the GUI's pure view-state derivations (klipsch_remote/viewstate.py).

This is the first slice of logic pulled out of the 1400-line Flet widget class in
``app.py`` so it can be tested on its own. The module is deliberately Flet-free,
and the package ``__init__`` imports Flet lazily, so -- unlike
``tests/test_gui_logic.py`` (gated on ``importorskip("flet")``) -- this runs in the
dependency-light pytest CI with nothing but pytest installed.

The real-EQ-preset round-trip stays in test_gui_logic (it goes through
``KlipschRemote._match_preset`` with ``theme.EQ_PRESETS``, which needs Flet); here
the matcher is exercised on synthetic presets to pin the pure logic.
"""

from __future__ import annotations

import pytest

from klipsch_remote import viewstate

_PRESETS = {"Flat": (0, 0, 0), "Bass": (6, 0, 0), "Rock": (3, -1, 3)}


class TestMatchEqPreset:
    def test_exact_match_returns_name(self):
        assert viewstate.match_eq_preset(_PRESETS, 6, 0, 0) == "Bass"

    def test_no_match_returns_none(self):
        # None (not a label) is the contract: app.py substitutes its CUSTOM text.
        assert viewstate.match_eq_preset(_PRESETS, 1, 2, 3) is None

    def test_first_matching_name_wins(self):
        presets = {"A": (1, 1, 1), "B": (1, 1, 1)}
        assert viewstate.match_eq_preset(presets, 1, 1, 1) == "A"

    def test_list_values_match_too(self):
        # Values are coerced with tuple(), so a list preset still round-trips.
        assert viewstate.match_eq_preset({"X": [2, 2, 2]}, 2, 2, 2) == "X"

    def test_empty_presets_returns_none(self):
        assert viewstate.match_eq_preset({}, 0, 0, 0) is None


class TestSubDetection:
    def test_present(self):
        d = viewstate.sub_detection(True)
        assert (d.present, d.status, d.opacity) == (True, "", 1.0)

    @pytest.mark.parametrize("detected", [False, None])
    def test_absent_or_unknown_greys_out(self, detected):
        # Only an explicit True lights the card; False and None both grey it.
        d = viewstate.sub_detection(detected)
        assert d.present is False
        assert d.status == "Not detected"
        assert d.opacity == 0.38

    def test_is_named_tuple(self):
        # app.py unpacks by attribute, so the field names are part of the contract.
        assert viewstate.sub_detection(True)._fields == ("present", "status", "opacity")


class TestFormatDb:
    @pytest.mark.parametrize("db, text", [(0, "0 dB"), (12, "12 dB"), (-40, "-40 dB")])
    def test_formats(self, db, text):
        assert viewstate.format_db(db) == text


class TestPlacementHint:
    @pytest.mark.parametrize("name", ["corner", "wall", "open"])
    def test_known_placements_have_distinct_copy(self, name):
        hint = viewstate.placement_hint(name)
        assert hint == viewstate.PLACEMENT_HINT[name]
        assert hint  # non-empty

    def test_all_three_placements_present(self):
        assert set(viewstate.PLACEMENT_HINT) == {"corner", "wall", "open"}

    def test_unknown_falls_back_to_wall(self):
        assert viewstate.placement_hint("ceiling") == viewstate.PLACEMENT_HINT["wall"]

    def test_known_placements_are_unique(self):
        assert len(set(viewstate.PLACEMENT_HINT.values())) == 3
