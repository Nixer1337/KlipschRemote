"""Behavioural tests for the CLI's pure helper functions (klipsch_ble/cli.py).

The CLI is the project's least-covered layer (and is kept out of the mypy gate as
Windows/ctypes-flavoured glue), yet its argument parsers are pure and easy to pin
down. Importing ``klipsch_ble.cli`` pulls in no bleak — it's imported lazily — so
this runs in the dependency-light pytest CI.
"""

from __future__ import annotations

import pytest

from klipsch_ble.cli import (
    _mac_from_dev_id,
    input_arg_to_byte,
    parse_delta,
    volume_bar,
)
from klipsch_ble.constants import MAX_VOLUME_RAW


class TestParseDelta:
    def test_absolute(self):
        assert parse_delta("5", 10, 0, MAX_VOLUME_RAW) == 5

    def test_relative_up(self):
        assert parse_delta("+3", 10, 0, MAX_VOLUME_RAW) == 13

    def test_relative_down(self):
        assert parse_delta("-2", 10, 0, MAX_VOLUME_RAW) == 8

    def test_clamps_high(self):
        assert parse_delta("+100", 10, 0, MAX_VOLUME_RAW) == MAX_VOLUME_RAW

    def test_clamps_low(self):
        assert parse_delta("-100", 10, 0, MAX_VOLUME_RAW) == 0

    def test_absolute_clamped(self):
        assert parse_delta("999", 0, 0, MAX_VOLUME_RAW) == MAX_VOLUME_RAW

    def test_strips_whitespace(self):
        assert parse_delta("  7 ", 0, 0, MAX_VOLUME_RAW) == 7


class TestInputArgToByte:
    @pytest.mark.parametrize(
        "arg, expected",
        [
            ("tv", 1), ("bluetooth", 2), ("bt", 2), ("optical", 3), ("opt", 3),
            ("aux", 4), ("usb", 5), ("phono", 6), ("3", 3),
        ],
    )
    def test_known(self, arg, expected):
        assert input_arg_to_byte(arg) == expected

    def test_prefix_match(self):
        # 'blue' is not an alias but uniquely prefixes 'bluetooth'.
        assert input_arg_to_byte("blue") == 2

    @pytest.mark.parametrize("arg", ["off", "0", "xyz"])
    def test_off_or_unknown_is_none(self, arg):
        assert input_arg_to_byte(arg) is None


class TestVolumeBar:
    def test_empty(self):
        assert volume_bar(0) == "." * 20

    def test_full(self):
        assert volume_bar(MAX_VOLUME_RAW) == "#" * 20

    def test_width_and_charset_preserved(self):
        for step in range(MAX_VOLUME_RAW + 1):
            bar = volume_bar(step)
            assert len(bar) == 20
            assert set(bar) <= {"#", "."}

    def test_custom_width(self):
        assert len(volume_bar(10, width=8)) == 8


class TestMacFromDevId:
    def test_classic_node(self):
        assert _mac_from_dev_id(r"BTHENUM\DEV_54B7E58D8F0B\7&abc") == "54:B7:E5:8D:8F:0B"

    def test_lowercase_is_uppercased(self):
        assert _mac_from_dev_id(r"BTHLE\DEV_54b7e58d8f0b\x") == "54:B7:E5:8D:8F:0B"

    def test_no_match(self):
        assert _mac_from_dev_id(r"USB\VID_1234&PID_5678") is None
