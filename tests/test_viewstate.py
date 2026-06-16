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

from types import SimpleNamespace

import pytest

from klipsch_ble import KlipschAccessError, KlipschNotFoundError
from klipsch_remote import viewstate

_PRESETS = {"Flat": (0, 0, 0), "Bass": (6, 0, 0), "Rock": (3, -1, 3)}
_ADDRS = ["54:B7:E5:8D:8F:0B", "AA:BB:CC:DD:EE:FF"]
_INPUT_KEYS = {"tv", "bluetooth", "optical", "usb", "aux", "phono"}


def _status(**kw):
    """A KlipschStatus-shaped (duck-typed) object with sane defaults to override."""
    base = dict(input="tv", volume_raw=18, mute=False, bass=0, mid=0, treble=0,
                night=False, dynamic_bass=False, sub_detected=True,
                sub_level_db=0, sub_invert=False, sub_mute=False)
    return SimpleNamespace(**{**base, **kw})


def _reconcile(status, raw_name="Living Room", model="The Fives"):
    return viewstate.reconcile(status, raw_name, model, _INPUT_KEYS)


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


class TestConnectErrorMessage:
    def test_access_error_gets_pairing_guidance(self):
        # The raw message is replaced by the actionable "pair as AUDIO" guidance.
        msg = viewstate.connect_error_message(KlipschAccessError("0x80…"))
        assert "AUDIO device" in msg
        assert "0x80" not in msg

    def test_not_found_error_is_passed_through(self):
        msg = viewstate.connect_error_message(KlipschNotFoundError("no speaker at 54:.."))
        assert msg == "no speaker at 54:.."

    def test_other_error_is_prefixed_with_its_type(self):
        assert viewstate.connect_error_message(OSError("link down")) == "OSError: link down"

    def test_sibling_errors_do_not_cross(self):
        # NotFound is not an AccessError (siblings under KlipschError), so it must
        # not pick up the access guidance.
        assert "AUDIO device" not in viewstate.connect_error_message(
            KlipschNotFoundError("gone"))


class TestPickPairedDevice:
    def test_typed_match_selects_only(self):
        r = viewstate.pick_paired_device(_ADDRS, "aa:bb:cc:dd:ee:ff")  # case-insensitive
        assert r.select == "AA:BB:CC:DD:EE:FF"
        assert r.autofill is None  # don't overwrite what the user typed

    def test_lone_device_selects_and_autofills(self):
        assert viewstate.pick_paired_device(["54:B7:E5:8D:8F:0B"], "") == (
            "54:B7:E5:8D:8F:0B", "54:B7:E5:8D:8F:0B")

    def test_typed_match_beats_lone_autofill(self):
        # A single device that matches the typed address: select, but don't autofill.
        r = viewstate.pick_paired_device(["54:B7:E5:8D:8F:0B"], "54:b7:e5:8d:8f:0b")
        assert (r.select, r.autofill) == ("54:B7:E5:8D:8F:0B", None)

    def test_multiple_unmatched_changes_nothing(self):
        assert viewstate.pick_paired_device(_ADDRS, "99:99:99:99:99:99") == (None, None)

    def test_empty_typed_with_multiple_changes_nothing(self):
        assert viewstate.pick_paired_device(_ADDRS, "") == (None, None)

    def test_no_devices_changes_nothing(self):
        assert viewstate.pick_paired_device([], "54:B7:E5:8D:8F:0B") == (None, None)


class TestReconcile:
    def test_passes_through_basics(self):
        v = _reconcile(_status(volume_raw=24, input="optical"), raw_name="Living Room")
        assert (v.name, v.volume_raw, v.input) == ("Living Room", 24, "optical")

    def test_name_falls_back_to_model_when_absent(self):
        # Empty or missing speaker name -> the model's display name.
        assert _reconcile(_status(), raw_name=None, model="The Fives").name == "The Fives"
        assert _reconcile(_status(), raw_name="", model="The Fives").name == "The Fives"

    def test_unknown_input_is_dropped(self):
        # A bogus/unsupported input -> None: leave the tile highlight as it is.
        assert _reconcile(_status(input="hdmi")).input is None

    def test_eq_kept_only_when_all_three_bands_present(self):
        assert _reconcile(_status(bass=3, mid=-1, treble=2)).eq == (3, -1, 2)
        # A half-read EQ is discarded, not shown as a partial/zeroed curve.
        assert _reconcile(_status(bass=3, mid=None, treble=2)).eq is None
        assert _reconcile(_status(bass=None, mid=None, treble=None)).eq is None

    @pytest.mark.parametrize("field", ["mute", "night", "dynamic_bass", "sub_invert", "sub_mute"])
    def test_flags_coerced_to_bool(self, field):
        assert getattr(_reconcile(_status(**{field: 1})), field) is True
        assert getattr(_reconcile(_status(**{field: None})), field) is False

    @pytest.mark.parametrize("detected", [True, False, None])
    def test_sub_detected_passes_through_tristate(self, detected):
        # The tri-state must survive intact for viewstate.sub_detection downstream.
        assert _reconcile(_status(sub_detected=detected)).sub_detected is detected

    def test_sub_level_passes_through_including_none(self):
        assert _reconcile(_status(sub_level_db=-12)).sub_level_db == -12
        assert _reconcile(_status(sub_level_db=None)).sub_level_db is None
