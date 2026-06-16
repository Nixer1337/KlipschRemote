"""Pure value-conversion and input-coercion tests (no hardware)."""

from __future__ import annotations

import pytest

from klipsch_ble import constants as c
from klipsch_ble.constants import Input


# ---- volume ----------------------------------------------------------------
@pytest.mark.parametrize("percent, raw", [(0, 0), (50, 18), (100, c.MAX_VOLUME_RAW)])
def test_volume_percent_raw_roundtrip_anchors(percent, raw):
    assert c.volume_percent_to_raw(percent) == raw
    assert c.volume_raw_to_percent(raw) == percent


def test_volume_percent_to_raw_truncates_like_fives_api():
    # 49% of 36 steps = 17.64 -> 17 (truncating, not rounding).
    assert c.volume_percent_to_raw(49) == 17


@pytest.mark.parametrize("bad, expected", [(-1, 0), (101, c.MAX_VOLUME_RAW)])
def test_volume_percent_out_of_range_clamps(bad, expected):
    # Out-of-range percent is clamped, matching the EQ/sub conversions and klipsch.js.
    assert c.volume_percent_to_raw(bad) == expected


def test_volume_raw_out_of_range_clamps():
    assert c.volume_raw_to_percent(c.MAX_VOLUME_RAW + 1) == 100
    assert c.volume_raw_to_percent(-1) == 0


def test_volume_db_endpoints():
    assert c.volume_raw_to_db(0) == -80
    assert c.volume_raw_to_db(c.MAX_VOLUME_RAW) == 8


# ---- EQ --------------------------------------------------------------------
def test_eq_flat_is_offset_ten():
    assert c.eq_level_to_byte(0) == c.EQ_OFFSET == 10
    assert c.eq_byte_to_level(10) == 0


def test_eq_clamps_to_range():
    assert c.eq_level_to_byte(99) == c.EQ_MAX + c.EQ_OFFSET   # +6 -> 16
    assert c.eq_level_to_byte(-99) == c.EQ_MIN + c.EQ_OFFSET  # -10 -> 0
    assert c.eq_byte_to_level(0) == c.EQ_MIN
    assert c.eq_byte_to_level(255) == c.EQ_MAX


# ---- subwoofer -------------------------------------------------------------
@pytest.mark.parametrize("raw, db", [(0, -21), (21, 0), (31, 10)])
def test_sub_level_raw_db_roundtrip_anchors(raw, db):
    # 0 dB is the default and corresponds to raw step 21 (db = raw - 21).
    assert c.sub_raw_to_db(raw) == db
    assert c.sub_db_to_raw(db) == raw


def test_sub_level_clamps_to_range():
    assert c.sub_db_to_raw(99) == c.SUB_RAW_MAX        # +10 dB ceiling -> 31
    assert c.sub_db_to_raw(-99) == c.SUB_RAW_MIN       # -21 dB floor   -> 0
    assert c.sub_raw_to_db(99) == c.SUB_DB_MAX         # +10 dB
    assert c.sub_raw_to_db(-99) == c.SUB_DB_MIN        # -21 dB


@pytest.mark.parametrize("data, expected", [
    (bytes([1]), True), (bytes([0]), False),
    (bytes([0, 1]), True), (bytes([0, 0]), False),
    (None, None), (b"", None),
])
def test_sub_detected_from_bytes(data, expected):
    # checkSubConnectedOnOff: big-endian int value == 1 means a sub is connected.
    assert c.sub_detected_from_bytes(data) is expected


# ---- inputs ----------------------------------------------------------------
@pytest.mark.parametrize("value, expected", [
    ("optical", Input.OPTICAL), ("opt", Input.OPTICAL),
    ("bt", Input.BLUETOOTH), ("BLUETOOTH", Input.BLUETOOTH),
    ("analog", Input.AUX), ("rca", Input.PHONO),
    (3, Input.OPTICAL), ("3", Input.OPTICAL),
    (Input.TV, Input.TV),
])
def test_normalize_input_accepts_name_alias_number_enum(value, expected):
    assert c.normalize_input(value) is expected


def test_normalize_input_rejects_unknown():
    with pytest.raises(ValueError):
        c.normalize_input("hdmi2")
    with pytest.raises(ValueError):
        c.normalize_input(99)


def test_input_name_is_canonical_lowercase():
    assert c.input_name(Input.BLUETOOTH) == "bluetooth"
    assert c.input_name(2) == "bluetooth"


def test_clamp():
    assert c.clamp(5, 0, 10) == 5
    assert c.clamp(-1, 0, 10) == 0
    assert c.clamp(11, 0, 10) == 10


# ---- speaker placement / boundary gain -------------------------------------
@pytest.mark.parametrize("value, expected", [
    ("corner", c.Placement.CORNER), ("wall", c.Placement.WALL),
    ("open", c.Placement.OPEN), ("free", c.Placement.OPEN),
    ("on_wall", c.Placement.WALL), ("table", c.Placement.OPEN),
    (4, c.Placement.CORNER), ("7", c.Placement.WALL), (10, c.Placement.OPEN),
    (c.Placement.OPEN, c.Placement.OPEN),
])
def test_normalize_placement_accepts_name_alias_number_enum(value, expected):
    assert c.normalize_placement(value) is expected


def test_normalize_placement_rejects_unknown():
    with pytest.raises(ValueError):
        c.normalize_placement("ceiling")
    with pytest.raises(ValueError):
        c.normalize_placement(5)  # not one of the valid bytes 4/7/10


@pytest.mark.parametrize("byte, expected", [
    (4, c.Placement.CORNER), (7, c.Placement.WALL), (10, c.Placement.OPEN),
    # checkBoundryGainType: any other / absent value defaults to WALL.
    (0, c.Placement.WALL), (99, c.Placement.WALL), (None, c.Placement.WALL),
])
def test_placement_from_byte_defaults_to_wall(byte, expected):
    assert c.placement_from_byte(byte) is expected


def test_placement_name_is_canonical_lowercase():
    assert c.placement_name(c.Placement.OPEN) == "open"
    assert c.placement_name(7) == "wall"
