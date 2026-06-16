"""Tests for the CLI's address-resolution layer (klipsch_ble/cli.py).

This is the impure half of the CLI: paired-device enumeration (parsing PowerShell
/ bluetoothctl output), the interactive picker, config persistence, and the
``resolve_address`` orchestration that ties them together. None of it needs a real
OS, a real speaker or bleak -- ``subprocess.run`` is faked to replay canned tool
output, ``sys.stdin`` is faked to script the picker, and ``CONFIG_PATH`` is
redirected to a tmp file. So this runs in the dependency-light pytest CI on any
platform, Windows enumerator included.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from klipsch_ble import cli


# ---- fake subprocess --------------------------------------------------------
class _FakeRun:
    """Replays canned ``CompletedProcess`` results keyed by argv, and records
    every call so a test can assert a fallback form was (or wasn't) reached."""

    def __init__(self, table, missing=False):
        # table: {tuple(argv): (stdout, returncode)}; unknown argv -> rc 1.
        self.table = table
        self.missing = missing
        self.calls: list[list[str]] = []

    def __call__(self, argv, *a, **k):
        self.calls.append(list(argv))
        if self.missing:
            raise FileNotFoundError(argv[0])
        out, rc = self.table.get(tuple(argv), ("", 1))
        return subprocess.CompletedProcess(argv, rc, out, "")


def _patch_run(monkeypatch, fake):
    monkeypatch.setattr(cli.subprocess, "run", fake)
    return fake


# ---- list_bluetooth_linux ---------------------------------------------------
_PAIRED = ("bluetoothctl", "devices", "Paired")
_BARE = ("bluetoothctl", "devices")


class TestListBluetoothLinux:
    def test_parses_modern_paired_output(self, monkeypatch):
        out = ("Device 54:B7:E5:8D:8F:0B The Fives\n"
               "Device AA:BB:CC:DD:EE:FF Living Room\n")
        fake = _patch_run(monkeypatch, _FakeRun({_PAIRED: (out, 0)}))
        devs = cli.list_bluetooth_linux()
        assert [(d.address, d.name) for d in devs] == [
            ("54:B7:E5:8D:8F:0B", "The Fives"),
            ("AA:BB:CC:DD:EE:FF", "Living Room"),
        ]
        # The precise form succeeded -- the bare fallback must not be reached.
        assert fake.calls == [list(_PAIRED)]

    def test_lowercase_mac_is_uppercased(self, monkeypatch):
        _patch_run(monkeypatch, _FakeRun({_PAIRED: ("Device 54:b7:e5:8d:8f:0b x\n", 0)}))
        assert cli.list_bluetooth_linux()[0].address == "54:B7:E5:8D:8F:0B"

    def test_ignores_non_device_lines(self, monkeypatch):
        out = ("Agent registered\n"
               "Device 54:B7:E5:8D:8F:0B The Fives\n"
               "[bluetooth]# \n")
        _patch_run(monkeypatch, _FakeRun({_PAIRED: (out, 0)}))
        assert len(cli.list_bluetooth_linux()) == 1

    def test_falls_back_when_paired_filter_unsupported(self, monkeypatch):
        # Old build rejects `Paired` with a non-zero exit (empty stdout). The
        # fallback to bare `devices` must fire -- this is the regression the
        # exit-code check fixes (the old code returned [] here).
        fake = _patch_run(monkeypatch, _FakeRun({
            _PAIRED: ("", 1),
            _BARE: ("Device 54:B7:E5:8D:8F:0B The Fives\n", 0),
        }))
        devs = cli.list_bluetooth_linux()
        assert [d.name for d in devs] == ["The Fives"]
        assert fake.calls == [list(_PAIRED), list(_BARE)]  # both forms tried

    def test_zero_exit_empty_is_trusted_not_widened(self, monkeypatch):
        # Modern host, nothing paired: `Paired` exits 0 with no devices. We trust
        # that and must NOT fall back to bare `devices` (which lists known-but-
        # unpaired devices too).
        fake = _patch_run(monkeypatch, _FakeRun({_PAIRED: ("", 0), _BARE: ("Device 11:22:33:44:55:66 Ghost\n", 0)}))
        assert cli.list_bluetooth_linux() == []
        assert fake.calls == [list(_PAIRED)]

    def test_missing_binary_returns_empty(self, monkeypatch):
        _patch_run(monkeypatch, _FakeRun({}, missing=True))
        assert cli.list_bluetooth_linux() == []


# ---- list_bluetooth_windows -------------------------------------------------
class TestListBluetoothWindows:
    def test_parses_name_and_mac(self, monkeypatch):
        out = r"The Fives|BTHENUM\DEV_54B7E58D8F0B\7&abc"
        monkeypatch.setattr(cli.subprocess, "run",
                            lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
        devs = cli.list_bluetooth_windows()
        assert (devs[0].address, devs[0].name) == ("54:B7:E5:8D:8F:0B", "The Fives")

    def test_dedup_upgrades_placeholder_name(self, monkeypatch):
        # Same MAC across two nodes: an empty-named node first (stored as the MAC
        # placeholder), then a real-named node -- the real name must win.
        out = ("|BTHENUM\\DEV_AABBCCDDEEFF\\x\n"
               "Bedroom Speaker|BTHLE\\DEV_AABBCCDDEEFF\\y\n")
        monkeypatch.setattr(cli.subprocess, "run",
                            lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
        devs = cli.list_bluetooth_windows()
        assert len(devs) == 1
        assert (devs[0].address, devs[0].name) == ("AA:BB:CC:DD:EE:FF", "Bedroom Speaker")

    def test_lines_without_mac_are_skipped(self, monkeypatch):
        out = "Some Mouse|USB\\VID_1234&PID_5678\nThe Nines|BTHLE\\DEV_112233445566\\z\n"
        monkeypatch.setattr(cli.subprocess, "run",
                            lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
        devs = cli.list_bluetooth_windows()
        assert [d.address for d in devs] == ["11:22:33:44:55:66"]

    def test_subprocess_failure_returns_empty(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("powershell missing")
        monkeypatch.setattr(cli.subprocess, "run", boom)
        assert cli.list_bluetooth_windows() == []


# ---- _choose_paired ---------------------------------------------------------
class _FakeStdin:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


def _candidates():
    return [cli.Paired("54:B7:E5:8D:8F:0B", "The Fives"),
            cli.Paired("AA:BB:CC:DD:EE:FF", "The Nines")]


class TestChoosePaired:
    def test_non_interactive_uses_saved_default(self, monkeypatch):
        monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=False))
        # input() must never be called on the non-interactive path.
        monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("prompted"))
        chosen = cli._choose_paired(_candidates(), "AA:BB:CC:DD:EE:FF")
        assert chosen == "AA:BB:CC:DD:EE:FF"

    def test_non_interactive_without_default_raises(self, monkeypatch):
        monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=False))
        with pytest.raises(SystemExit):
            cli._choose_paired(_candidates(), default_address=None)

    def test_interactive_picks_by_number(self, monkeypatch):
        monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
        monkeypatch.setattr("builtins.input", lambda *a: "2")
        assert cli._choose_paired(_candidates(), None) == "AA:BB:CC:DD:EE:FF"

    def test_interactive_empty_input_takes_default(self, monkeypatch):
        monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
        monkeypatch.setattr("builtins.input", lambda *a: "")
        # Saved default is candidate #1 -> empty input selects it.
        assert cli._choose_paired(_candidates(), "54:B7:E5:8D:8F:0B") == "54:B7:E5:8D:8F:0B"

    def test_interactive_reprompts_on_garbage(self, monkeypatch):
        monkeypatch.setattr(cli.sys, "stdin", _FakeStdin(tty=True))
        answers = iter(["nope", "9", "1"])  # bad word, out-of-range, then valid
        monkeypatch.setattr("builtins.input", lambda *a: next(answers))
        assert cli._choose_paired(_candidates(), None) == "54:B7:E5:8D:8F:0B"


# ---- config persistence -----------------------------------------------------
class TestConfig:
    def test_load_absent_is_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / ".klipsch.json")
        assert cli.load_config() == {}

    def test_save_then_load_round_trips(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / ".klipsch.json")
        cli.save_config({"address": "54:B7:E5:8D:8F:0B", "auto_connect": True})
        assert cli.load_config() == {"address": "54:B7:E5:8D:8F:0B", "auto_connect": True}

    def test_save_address_merges_other_keys(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / ".klipsch.json")
        cli.save_config({"auto_connect": True})
        cli.save_address("AA:BB:CC:DD:EE:FF")
        assert cli.load_config() == {"auto_connect": True, "address": "AA:BB:CC:DD:EE:FF"}

    def test_malformed_json_is_ignored(self, monkeypatch, tmp_path):
        path = tmp_path / ".klipsch.json"
        path.write_text("{not valid json")
        monkeypatch.setattr(cli, "CONFIG_PATH", path)
        assert cli.load_config() == {}

    def test_non_dict_json_is_ignored(self, monkeypatch, tmp_path):
        path = tmp_path / ".klipsch.json"
        path.write_text("[1, 2, 3]")
        monkeypatch.setattr(cli, "CONFIG_PATH", path)
        assert cli.load_config() == {}


# ---- resolve_address --------------------------------------------------------
class TestResolveAddress:
    def test_explicit_address_wins(self):
        # An explicit --address short-circuits all enumeration and isn't cached.
        assert asyncio.run(cli.resolve_address("54:B7:E5:8D:8F:0B")) == "54:B7:E5:8D:8F:0B"

    def test_single_paired_auto_picks_and_caches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / ".klipsch.json")
        monkeypatch.setattr(cli, "list_paired_klipsch",
                            lambda: [cli.Paired("54:B7:E5:8D:8F:0B", "The Fives")])
        addr = asyncio.run(cli.resolve_address(None))
        assert addr == "54:B7:E5:8D:8F:0B"
        assert cli.load_config()["address"] == "54:B7:E5:8D:8F:0B"  # cached for next run

    def test_none_paired_falls_back_to_saved(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli, "CONFIG_PATH", tmp_path / ".klipsch.json")
        cli.save_address("AA:BB:CC:DD:EE:FF")
        monkeypatch.setattr(cli, "list_paired_klipsch", lambda: [])
        addr = asyncio.run(cli.resolve_address(None))
        assert addr == "AA:BB:CC:DD:EE:FF"
