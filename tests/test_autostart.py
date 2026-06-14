"""Pure-logic tests for the autostart helper (no OS side effects).

``autostart.py`` is stdlib-only, so we load it straight from its file — bypassing
the ``klipsch_remote`` package ``__init__`` (which imports flet) — to keep this
suite dependency-free, matching the rest of tests/.
"""

from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

_MOD_PATH = Path(__file__).resolve().parent.parent / "klipsch_remote" / "autostart.py"
_spec = importlib.util.spec_from_file_location("klipsch_autostart", _MOD_PATH)
autostart = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(autostart)

_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_xml_escape_escapes_markup():
    assert autostart._xml_escape("a & b < c > d") == "a &amp; b &lt; c &gt; d"


def test_quote_only_quotes_elements_with_spaces():
    assert autostart._quote(["a", "b c", "d"]) == 'a "b c" d'


def test_launch_args_nonempty_and_starts_with_an_executable():
    args = autostart._launch_args()
    assert args and isinstance(args, list)
    assert args[0]  # always an executable path / interpreter


def test_win_task_xml_is_wellformed_with_a_single_command(monkeypatch):
    # A path with a space and an XML-hostile char proves escaping is applied
    # (ET.fromstring raises on malformed markup) and survives round-trip.
    monkeypatch.setattr(autostart, "_host_executable",
                        lambda: r"C:\Program Files\A&B\App.exe")
    root = ET.fromstring(autostart._win_task_xml())
    commands = root.findall(".//t:Actions/t:Exec/t:Command", _NS)
    assert len(commands) == 1
    assert commands[0].text == r"C:\Program Files\A&B\App.exe"


def test_win_task_xml_carries_the_logon_delay(monkeypatch):
    monkeypatch.setattr(autostart, "_host_executable", lambda: r"C:\App\App.exe")
    root = ET.fromstring(autostart._win_task_xml())
    delay = root.find(".//t:Triggers/t:LogonTrigger/t:Delay", _NS)
    assert delay is not None and delay.text == autostart._WIN_LOGON_DELAY


def test_win_task_xml_omits_empty_arguments(monkeypatch):
    # A native single-exe relaunch has no extra argv: there must be NO
    # <Arguments> element (an empty one would pass a stray empty arg to the exe,
    # which the native flet launcher chokes on).
    monkeypatch.setattr(autostart, "_launch_args", lambda: [r"C:\App\App.exe"])
    assert "<Arguments>" not in autostart._win_task_xml()


def test_win_task_xml_keeps_nonempty_arguments(monkeypatch):
    # A dev `python -m klipsch_remote` relaunch does carry args -> emitted.
    monkeypatch.setattr(autostart, "_launch_args",
                        lambda: ["py.exe", "-m", "klipsch_remote"])
    assert "<Arguments>-m klipsch_remote</Arguments>" in autostart._win_task_xml()
