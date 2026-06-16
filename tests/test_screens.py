"""Smoke + structural tests for the screen builders (klipsch_remote/screens.py).

screens.py is ~490 lines of Flet view assembly: five ``*_controls(r)`` builders
that wire the remote's pre-built controls into screen layouts. There's no pure
logic to isolate, so the meaningful coverage is a smoke test -- construct a real
``KlipschRemote`` (its ``__init__`` only builds controls; it never touches the
page) and assert every screen assembles into a non-empty control tree without
raising. That exercises all five builders end to end and catches a broken control
reference, a renamed handler, or a structural slip.

Gated on Flet like ``test_gui_logic`` (it builds real Flet controls), so it runs
in the GUI test job, not the lean core one.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

pytest.importorskip("flet")

from klipsch_ble import DeviceInfo
from klipsch_remote import screens
from klipsch_remote.app import KlipschRemote


class _DummyPage:
    """``KlipschRemote.__init__`` only stores the page and builds controls -- it
    never calls a page method -- so a bare object stands in for the live page."""


@pytest.fixture
def remote():
    return KlipschRemote(_DummyPage())


_BUILDERS = [
    screens.connect_controls,
    screens.connecting_controls,
    screens.remote_controls,
    screens.settings_controls,
    screens.about_controls,
]


class TestScreenBuilders:
    @pytest.mark.parametrize("builder", _BUILDERS, ids=lambda b: b.__name__)
    def test_builds_non_empty_control_list(self, remote, builder):
        controls = builder(remote)
        assert isinstance(controls, list)
        assert controls and all(c is not None for c in controls)

    def test_settings_builds_and_wires_the_sub_card(self, remote):
        # sub_card doesn't exist until settings_controls builds it (the reflectors
        # guard with getattr); building applies the cached detection state to it.
        assert not hasattr(remote, "sub_card")
        screens.settings_controls(remote)
        assert remote.sub_card is not None

    def test_remote_screen_is_header_plus_scroller(self, remote):
        # The remote returns a fixed header bar + a scroll viewport (two roots).
        assert len(screens.remote_controls(remote)) == 2


class TestAboutFields:
    def test_every_entry_is_an_icon_label_attr_triple(self):
        assert all(len(f) == 3 for f in screens.ABOUT_FIELDS)

    def test_labels_are_unique(self):
        labels = [label for _icon, label, _attr in screens.ABOUT_FIELDS]
        assert len(labels) == len(set(labels))

    def test_attrs_are_all_device_info_fields(self):
        # _apply_device_info reads each attr off DeviceInfo (name/model come from a
        # live override, but they're DeviceInfo fields too). A typo here would
        # render a permanently blank About row -- pin the contract.
        di_fields = {f.name for f in fields(DeviceInfo)}
        attrs = {attr for _icon, _label, attr in screens.ABOUT_FIELDS}
        assert attrs <= di_fields

    def test_repo_url_points_at_github(self):
        assert screens.REPO_URL.startswith("https://github.com/")
