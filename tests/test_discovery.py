"""Tests for the BLE discovery layer (klipsch_ble/discovery.py).

``discovery.discover`` is the package's only public function that drives a live
``BleakScanner`` scan, so it's normally awkward to test. But it imports bleak
lazily (``from bleak import BleakScanner`` inside the function), so a fake bleak
module injected into ``sys.modules`` lets the whole match/dedup/name-resolution
path run with canned advertisements -- no radio, no bleak, any platform, in the
dependency-light pytest CI.

The matching rule under test: a device counts as Klipsch if its advertised name
contains the needle *or* it advertises one of the vendor's custom GATT services
(matched by UUID suffix), so a renamed speaker is still found.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import ClassVar

import pytest

from klipsch_ble import discovery
from klipsch_ble.models import KlipschModel

# A full vendor service UUID -- shares the suffix discovery keys off.
_KLIPSCH_UUID = "da6d0f01-0d18-442c-babe-f85b5baa6f11"
_OTHER_UUID = "0000180f-0000-1000-8000-00805f9b34fb"  # battery service


class _Dev:
    def __init__(self, address, name=None):
        self.address = address
        self.name = name


class _Adv:
    def __init__(self, local_name=None, service_uuids=None):
        self.local_name = local_name
        self.service_uuids = service_uuids


@pytest.fixture
def fake_bleak(monkeypatch):
    """Inject a fake ``bleak`` whose ``BleakScanner.discover`` replays whatever
    is assigned to ``BleakScanner.result`` ({address: (dev, adv)})."""
    mod = types.ModuleType("bleak")

    class BleakScanner:
        result: ClassVar[dict] = {}

        @classmethod
        async def discover(cls, timeout=0, return_adv=False):
            assert return_adv, "discovery must request advertisements"
            return cls.result

    mod.BleakScanner = BleakScanner
    monkeypatch.setitem(sys.modules, "bleak", mod)
    return BleakScanner


def _scan(fake_bleak, devices, **kw):
    fake_bleak.result = {d.address: (d, a) for d, a in devices}
    return asyncio.run(discovery.discover(**kw))


class TestDiscover:
    def test_matches_by_name(self, fake_bleak):
        hits = _scan(fake_bleak, [(_Dev("54:B7:..", "Klipsch The Fives"), _Adv())])
        assert len(hits) == 1
        assert hits[0].address == "54:B7:.."
        assert hits[0].model == KlipschModel.FIVES

    def test_name_match_is_case_insensitive(self, fake_bleak):
        hits = _scan(fake_bleak, [(_Dev("a"), _Adv(local_name="KLIPSCH the nines"))])
        assert len(hits) == 1

    def test_matches_renamed_speaker_by_service_uuid(self, fake_bleak):
        # No "klipsch" anywhere in the name -- only the vendor service UUID gives
        # it away. Model is UNKNOWN because the name carries no product token.
        hits = _scan(fake_bleak, [
            (_Dev("a", "Living Room"), _Adv(local_name="Living Room",
                                            service_uuids=[_KLIPSCH_UUID])),
        ])
        assert len(hits) == 1
        assert hits[0].name == "Living Room"
        assert hits[0].model == KlipschModel.UNKNOWN

    def test_service_uuid_match_is_case_insensitive(self, fake_bleak):
        hits = _scan(fake_bleak, [
            (_Dev("a"), _Adv(local_name="x", service_uuids=[_KLIPSCH_UUID.upper()])),
        ])
        assert len(hits) == 1

    def test_non_klipsch_is_excluded(self, fake_bleak):
        hits = _scan(fake_bleak, [
            (_Dev("a"), _Adv(local_name="Sony WH-1000", service_uuids=[_OTHER_UUID])),
        ])
        assert hits == []

    def test_local_name_preferred_over_dev_name(self, fake_bleak):
        hits = _scan(fake_bleak, [
            (_Dev("a", "stale cached name"), _Adv(local_name="Klipsch The Sevens")),
        ])
        assert hits[0].name == "Klipsch The Sevens"

    def test_falls_back_to_dev_name(self, fake_bleak):
        hits = _scan(fake_bleak, [(_Dev("a", "Klipsch The Nines"), _Adv(local_name=None))])
        assert hits[0].name == "Klipsch The Nines"

    def test_no_name_and_no_uuid_is_excluded(self, fake_bleak):
        hits = _scan(fake_bleak, [(_Dev("a"), _Adv(local_name=None, service_uuids=None))])
        assert hits == []

    def test_dedup_by_address(self, fake_bleak):
        # Two advertisements resolving to the same device address collapse to one.
        d1, d2 = _Dev("SAME", "Klipsch The Fives"), _Dev("SAME", "Klipsch The Fives")
        fake_bleak.result = {"k1": (d1, _Adv()), "k2": (d2, _Adv())}
        hits = asyncio.run(discovery.discover())
        assert len(hits) == 1

    def test_custom_needle(self, fake_bleak):
        hits = _scan(fake_bleak, [(_Dev("a", "The Sevens"), _Adv())], name_contains="sevens")
        assert len(hits) == 1

    def test_empty_scan(self, fake_bleak):
        assert _scan(fake_bleak, []) == []


class TestFindAddress:
    def test_returns_first_hit(self, fake_bleak):
        fake_bleak.result = {"54:B7:..": (_Dev("54:B7:..", "Klipsch The Fives"), _Adv())}
        assert asyncio.run(discovery.find_address()) == "54:B7:.."

    def test_none_when_nothing_found(self, fake_bleak):
        fake_bleak.result = {}
        assert asyncio.run(discovery.find_address()) is None
