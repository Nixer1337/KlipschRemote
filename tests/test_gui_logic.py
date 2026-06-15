"""Behavioural tests for GUI logic — Flet widgets + EQ-preset matching.

Gated on Flet being importable: runs locally and for contributors with the full
app dependencies, and skips cleanly in the dependency-light test CI (which
installs only pytest). No window/page is created — the slider is exercised with
``update=False`` so it never calls ``.update()`` and needs no live render loop.
"""

from __future__ import annotations

import pytest

pytest.importorskip("flet")

from klipsch_remote.app import KlipschRemote
from klipsch_remote.theme import CUSTOM, EQ_PRESETS
from klipsch_remote.widgets import VSlider


def _slider(lo: int = -10, hi: int = 6) -> VSlider:
    return VSlider(lo=lo, hi=hi, on_commit=lambda _v: None)


class TestVSlider:
    def test_clamps_above_hi(self):
        s = _slider()
        s.set_value(100, update=False)
        assert s.value == 6

    def test_clamps_below_lo(self):
        s = _slider()
        s.set_value(-100, update=False)
        assert s.value == -10

    def test_rounds_to_int(self):
        s = _slider()
        s.set_value(2.4, update=False)
        assert s.value == 2
        s.set_value(2.6, update=False)
        assert s.value == 3

    def test_value_from_y_top_is_max(self):
        s = _slider()
        assert s._value_from_y(s.THUMB / 2) == s.hi

    def test_value_from_y_bottom_is_min(self):
        s = _slider()
        assert s._value_from_y(s.THUMB / 2 + s._travel) == s.lo


class TestMatchPreset:
    @pytest.mark.parametrize("name", list(EQ_PRESETS))
    def test_known_preset_round_trips(self, name):
        assert KlipschRemote._match_preset(*EQ_PRESETS[name]) == name

    def test_unmatched_is_custom(self):
        assert KlipschRemote._match_preset(1, 2, 3) == CUSTOM
