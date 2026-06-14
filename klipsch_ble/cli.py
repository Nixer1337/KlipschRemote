#!/usr/bin/env python3
"""Interactive / one-shot command-line front-end for Klipsch powered speakers.

Works with the whole protocol-identical line — The Fives, The Sevens, The Nines.
This is an *optional* convenience layer on top of the :mod:`klipsch_ble` library.
Run it with no arguments for a REPL, or pass a sub-command for one-shot use::

    klipsch                   # interactive REPL
    klipsch status            # print full status and exit
    klipsch set 50%           # set volume to 50%
    klipsch up 2              # raise volume by 2 steps
    klipsch in optical        # switch input

Address resolution: ``--address`` wins; otherwise the speaker is found among the
OS-paired devices — if exactly one Klipsch is paired it connects automatically,
if several are paired it lists them and asks which one; if none are paired it
falls back to a BLE name scan. The chosen address is cached in
``~/.klipsch.json``.

The transport and protocol live in the package (bleak: WinRT on Windows, BlueZ
on Linux, CoreBluetooth on macOS); this file is pure UX (REPL + argparse +
address discovery + a cosmetic Windows-only "also audio" badge).

IMPORTANT: the speaker must be paired with the OS as an AUDIO device
(Classic/CTKD) so the LE key is derived from the audio bond and GATT comes up.
Never pair()/unpair() it as an LE device -- that breaks control.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import (
    Input,
    KlipschAccessError,
    KlipschClient,
    KlipschNotFoundError,
    MAX_VOLUME_RAW,
    discover,
    find_address,
    input_name,
    normalize_input,
)
from .constants import (
    CH_DYNBASS,
    CH_FUNCSOUNDS,
    CH_NIGHT,
    CH_SUBINVERT,
    CH_SUBMUTE,
    EQ_MAX,
    EQ_MIN,
    INPUT_NAMES,
    SUB_DB_MAX,
    SUB_DB_MIN,
    clamp,
    volume_percent_to_raw,
    volume_raw_to_db,
    volume_raw_to_percent,
)
from .models import model_from_name

CONFIG_PATH = Path.home() / ".klipsch.json"
IS_WINDOWS = sys.platform == "win32"

# When we shell out (PowerShell on Windows) from a windowed app with no console
# of its own — e.g. the packaged GUI .exe — a console window flashes on screen
# unless we explicitly suppress it. CREATE_NO_WINDOW exists only on Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

VOL_MIN, VOL_MAX = 0, MAX_VOLUME_RAW


@dataclass(frozen=True)
class Paired:
    """A Klipsch speaker found among the OS-paired devices."""

    address: str
    name: str


# ---- display / input helpers ------------------------------------------------
def volume_bar(step, width=20):
    fill = round(step / VOL_MAX * width)
    return "#" * fill + "." * (width - fill)


def parse_delta(value, cur, lo, hi):
    """'+3'/'-2' -> relative to cur; '5' -> absolute. Returns a clamped value."""
    value = value.strip()
    if value and value[0] in "+-" and value[1:].lstrip("-").isdigit():
        return clamp(cur + int(value), lo, hi)
    return clamp(int(value), lo, hi)


def input_arg_to_byte(s):
    """Input name/alias/digit -> byte 1..6; unknown or 'off' -> None."""
    s = s.strip().lower()
    try:
        inp = normalize_input(s)
        return None if inp == Input.OFF else inp.value
    except ValueError:
        pass
    for inp, name in INPUT_NAMES.items():  # prefix match (blue -> bluetooth)
        if inp != Input.OFF and name.startswith(s):
            return inp.value
    return None


# ---- Windows-only: cosmetic "also an audio output" badge --------------------
if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    try:
        _BTH = ctypes.WinDLL("bthprops.cpl")
    except Exception:
        _BTH = None

    class _BTH_ADDR(ctypes.Union):
        _fields_ = [("ullLong", ctypes.c_ulonglong), ("rgBytes", ctypes.c_ubyte * 6)]

    class _SYSTEMTIME(ctypes.Structure):
        _fields_ = [(n, wintypes.WORD) for n in
                    ("y", "mo", "dow", "d", "h", "mi", "s", "ms")]

    class _BTH_DEVICE_INFO(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("Address", _BTH_ADDR),
            ("ulClassofDevice", wintypes.ULONG), ("fConnected", wintypes.BOOL),
            ("fRemembered", wintypes.BOOL), ("fAuthenticated", wintypes.BOOL),
            ("stLastSeen", _SYSTEMTIME), ("stLastUsed", _SYSTEMTIME),
            ("szName", ctypes.c_wchar * 248),
        ]

    if _BTH is not None:
        _BTH.BluetoothGetDeviceInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_BTH_DEVICE_INFO)]
        _BTH.BluetoothGetDeviceInfo.restype = wintypes.DWORD

    def _mac_to_int(mac):
        return int(mac.replace(":", "").replace("-", ""), 16)

    def bt_is_audio_connected(address):
        if _BTH is None:
            return False
        info = _BTH_DEVICE_INFO()
        info.dwSize = ctypes.sizeof(_BTH_DEVICE_INFO)
        try:
            info.Address.ullLong = _mac_to_int(address)
            if _BTH.BluetoothGetDeviceInfo(None, ctypes.byref(info)) == 0:
                return bool(info.fConnected)
        except Exception:
            pass
        return False
else:
    def bt_is_audio_connected(address):  # noqa: ARG001 - no cheap equivalent off Windows
        return False


# ---- saved config -----------------------------------------------------------
def load_config():
    """Return the whole ``~/.klipsch.json`` config as a dict (``{}`` if absent)."""
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):  # unreadable file / malformed JSON
            pass
    return {}


def save_config(data):
    """Write the config dict back, pretty-printed (best-effort)."""
    try:
        CONFIG_PATH.write_text(json.dumps(data, indent=2))
    except OSError:  # read-only home / disk full — config is non-essential
        pass


def load_saved_address():
    return load_config().get("address")


def save_address(address):
    # Merge so other keys (e.g. auto_connect) survive an address change.
    cfg = load_config()
    cfg["address"] = address
    save_config(cfg)


# ---- paired-device enumeration ----------------------------------------------
# Default Bluetooth names of the powered line. Used to pick out still-default-
# named Klipsch units from the full paired list; a *renamed* speaker won't match
# (that's fine — the auto-pick helpers only need to recognise default names, and
# the unfiltered enumerators used by the GUI list renamed devices regardless).
_KLIPSCH_NAME = re.compile(r"klipsch|the fives|the sevens|the nines", re.I)


def _mac_from_dev_id(instance_id):
    m = re.search(r"DEV_([0-9A-Fa-f]{12})", instance_id)
    if not m:
        return None
    h = m.group(1).upper()
    return ":".join(h[i:i + 2] for i in range(0, 12, 2))


def list_bluetooth_windows():
    """Every paired Bluetooth device Windows knows (name + MAC), unfiltered.

    A speaker that's been *renamed* still shows up (we don't filter by name). We
    keep only each device's primary node — Classic ``BTHENUM\\DEV_<MAC>...`` or
    LE ``BTHLE\\DEV_<MAC>...`` — and skip the per-service/profile child nodes
    (``BTHENUM\\{GUID}...``, ``BTHLEDEVICE\\...``, the AVRCP transports, etc).
    """
    # NB: emit the line with single-quoted PowerShell concatenation, NOT an
    # interpolated "$(...)" string. The InstanceId filters contain a backslash
    # (BTHENUM\DEV_), and a backslash in the argument throws off Windows'
    # C-runtime quote-escaping so embedded \" quotes get mangled into literal
    # backslashes — PowerShell then reads the format string as a bad command.
    ps = (r"Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
          r"Where-Object { $_.InstanceId -like 'BTHENUM\DEV_*' -or "
          r"$_.InstanceId -like 'BTHLE\DEV_*' } | "
          r"ForEach-Object { $_.FriendlyName + '|' + $_.InstanceId }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=20,
                             creationflags=_NO_WINDOW).stdout
    except Exception:
        return []
    by_mac: dict[str, str] = {}  # MAC -> best (non-empty) friendly name
    for line in out.splitlines():
        name, _, instance = line.partition("|")
        mac = _mac_from_dev_id(instance)
        name = name.strip()
        if not mac:
            continue
        # First sighting, or upgrade a placeholder once a real name turns up.
        if mac not in by_mac or (name and by_mac[mac] == mac):
            by_mac[mac] = name or mac
    return [Paired(address=mac, name=name) for mac, name in by_mac.items()]


def list_bluetooth_linux():
    """Every paired Bluetooth device known to BlueZ (name + MAC), unfiltered."""
    # Newer bluetoothctl wants the explicit `Paired` filter; older builds only
    # understand a bare `devices` (paired + known). Try the precise form first.
    for argv in (["bluetoothctl", "devices", "Paired"], ["bluetoothctl", "devices"]):
        try:
            out = subprocess.run(argv, capture_output=True, text=True,
                                 timeout=10).stdout
        except Exception:
            continue
        found: list[Paired] = []
        for line in out.splitlines():
            m = re.match(r"Device\s+([0-9A-Fa-f:]{17})\s+(.*)", line.strip())
            if m:
                found.append(Paired(address=m.group(1).upper(),
                                    name=m.group(2).strip()))
        return found
    return []


def list_paired_bluetooth():
    """All paired Bluetooth devices on this OS (name + MAC), unfiltered.

    Used by the GUI's device picker so renamed speakers — and any other paired
    device the user wants to point the address field at — are selectable.
    """
    if IS_WINDOWS:
        return list_bluetooth_windows()
    if sys.platform.startswith("linux"):
        return list_bluetooth_linux()
    return []  # macOS: no cheap paired enumeration; caller falls back to BLE scan


def list_paired_klipsch():
    """Still-default-named Klipsch speakers among the OS-paired devices.

    Filters :func:`list_paired_bluetooth` by the known product names — used by
    the CLI to auto-pick when exactly one Klipsch is paired. Empty on macOS (no
    cheap paired enumeration; the caller falls back to a BLE scan).
    """
    return [d for d in list_paired_bluetooth() if _KLIPSCH_NAME.search(d.name)]


def _choose_paired(candidates, default_address):
    """Prompt the user to pick one paired speaker; pre-select the saved one."""
    print("Multiple Klipsch speakers are paired:", file=sys.stderr)
    default_idx = 0
    for i, dev in enumerate(candidates, 1):
        is_default = dev.address == default_address
        if is_default:
            default_idx = i
        model = model_from_name(dev.name)
        label = model.display_name if model.value != "unknown" else dev.name
        mark = "  (last used)" if is_default else ""
        print(f"  {i}) {label}  [{dev.address}]{mark}", file=sys.stderr)
    if not sys.stdin or not sys.stdin.isatty():
        if default_address and any(c.address == default_address for c in candidates):
            print(f"Non-interactive; using last-used {default_address}.", file=sys.stderr)
            return default_address
        raise SystemExit(
            "Several Klipsch speakers are paired and there is no TTY to choose.\n"
            "Re-run with --address <MAC> to pick one.")
    prompt = f"Pick a speaker [1-{len(candidates)}] (default {default_idx}): "
    while True:
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit("\nNo selection made.")
        if not raw:
            return candidates[default_idx - 1].address
        if raw.isdigit() and 1 <= int(raw) <= len(candidates):
            return candidates[int(raw) - 1].address
        print(f"Enter a number 1-{len(candidates)}.", file=sys.stderr)


async def resolve_address(cli_address):
    if cli_address:
        return cli_address

    saved = load_saved_address()
    paired = list_paired_klipsch()

    if len(paired) == 1:
        addr = paired[0].address
        print(f"Using the paired {model_from_name(paired[0].name).display_name} "
              f"[{addr}].", file=sys.stderr)
        save_address(addr)
        return addr

    if len(paired) > 1:
        addr = _choose_paired(paired, saved)
        save_address(addr)
        return addr

    # Nothing enumerated. Fall back to a saved address, then a BLE air scan.
    if saved:
        print(f"No paired Klipsch enumerated; trying saved address {saved}.",
              file=sys.stderr)
        return saved
    print("Scanning the air (BLE) for Klipsch...", file=sys.stderr)
    found = await find_address()
    if found:
        print(f"Found: {found}", file=sys.stderr)
        save_address(found)
        return found
    raise SystemExit(
        "Speaker not found.\n"
        "  * Windows: pair it as an AUDIO device (Settings > Bluetooth >\n"
        "    Add device > 'Klipsch The Fives/Sevens/Nines'; the Audio section,\n"
        "    not 'Other').\n"
        "  * Linux/macOS: wake the speaker (it must advertise BLE) and retry,\n"
        "    or pass the address manually: --address 54:B7:...\n"
        "  * On macOS the address is a CoreBluetooth UUID, not a MAC.")


# ---- status -----------------------------------------------------------------
def _onoff(v):
    return {True: "on", False: "off", None: "?"}[v]


async def full_status(f: KlipschClient):
    st = await f.status()
    eq_line = (f"EQ      : bass {st.bass:+d}  mid {st.mid:+d}  treble {st.treble:+d}"
               f"   (-10..+6)"
               if None not in (st.bass, st.mid, st.treble) else "EQ      : ?")
    lines = [
        f"Model   : {f.model.display_name}",
        f"Volume  : [{volume_bar(st.volume_raw)}] {st.volume_raw:2d}/{VOL_MAX}  "
        f"{st.volume_percent:3d}%  {st.volume_db:+d} dB   mute:{_onoff(st.mute)}",
        f"Input   : {st.input}",
        eq_line,
        f"Modes   : night {_onoff(st.night)}   dynamic-bass {_onoff(st.dynamic_bass)}",
        f"Sub     : {'detected' if st.sub_detected else 'not detected' if st.sub_detected is not None else '?'}"
        f"   level {f'{st.sub_level_db:+d} dB' if st.sub_level_db is not None else '?'}"
        f"   invert {_onoff(st.sub_invert)}   mute {_onoff(st.sub_mute)}",
    ]
    return "\n".join(lines)


def short_volume(vol_raw, mute):
    return (f"[{volume_bar(vol_raw)}] {vol_raw:2d}/{VOL_MAX}  "
            f"{volume_raw_to_percent(vol_raw):3d}%  "
            f"{volume_raw_to_db(vol_raw):+d} dB   mute:{_onoff(mute)}")


# ---- interactive REPL -------------------------------------------------------
HELP = """\
VOLUME      N (0..36) | N% | + / - / +N / -N | m (mute)
INPUT       in <tv|bluetooth|optical|analog|usb|phono | 1..6>
EQ          bass <+-N|N> | mid <+-N|N> | treble <+-N|N>   (-10..+6)
SUBWOOFER   sublevel <+-N|N> (-21..+10 dB) | submute on|off | subinvert on|off
MODES       night on|off | dynbass on|off | vocal 0..3 | eq 0..5
TRANSPORT   play | next | prev
OTHER       name <new name> | info (firmware/serial) | s (full status) | r (volume) | ? | q (quit)
DANGER      factory-reset (wipes all settings; asks to confirm)"""


async def interactive(f: KlipschClient):
    audio = " (also audio output)" if bt_is_audio_connected(f.address) else ""
    print(f"\n=== {f.model.display_name}{audio} ===")
    print(await full_status(f))
    print("\n" + HELP)
    while True:
        try:
            raw = await asyncio.to_thread(input, "\n> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        cmd = raw.lstrip("﻿").strip()
        low = cmd.lower()
        if not cmd:
            continue
        try:
            if low in ("q", "quit", "exit"):
                break
            if low in ("?", "h", "help"):
                print(HELP)
                continue
            if low in ("s", "status"):
                print(await full_status(f))
                continue
            if low == "r":
                print(short_volume(await f.get_volume_raw(), await f.get_mute()))
                continue

            handled = await handle_command(f, low)
            if handled is None:
                print("didn't get that; '?' for help")
        except Exception as e:  # noqa: BLE001
            print(f"error: {type(e).__name__}: {e}")
    print("bye!")


async def handle_command(f: KlipschClient, low: str):
    """Run one command. Returns the printed result string, or None if unrecognized."""
    parts = low.split()
    head = parts[0]
    arg = parts[1] if len(parts) > 1 else ""
    rest = low[len(head):].strip()

    # volume
    if head == "m":
        await f.set_mute(not bool(await f.get_mute()))
        return _say(short_volume(await f.get_volume_raw(), await f.get_mute()))
    if head in ("+", "-") or re.fullmatch(r"[+-]\d+", head):
        delta = int(head) if len(head) > 1 else (1 if head == "+" else -1)
        await f.set_volume_raw(await f.get_volume_raw() + delta)
        return _say(short_volume(await f.get_volume_raw(), await f.get_mute()))
    if head.endswith("%") and head[:-1].isdigit():
        await f.set_volume_raw(volume_percent_to_raw(clamp(int(head[:-1]), 0, 100)))
        return _say(short_volume(await f.get_volume_raw(), await f.get_mute()))
    if head.isdigit():
        await f.set_volume_raw(clamp(int(head), VOL_MIN, VOL_MAX))
        return _say(short_volume(await f.get_volume_raw(), await f.get_mute()))

    # input
    if head in ("in", "input", "src", "source"):
        if not rest:
            return _say(f"input: {input_name(await f.get_input())}")
        b = input_arg_to_byte(rest)
        if b is None:
            return _say("unknown input (tv/bluetooth/optical/analog/usb/phono)")
        await f.set_input(b)
        return _say(f"input -> {input_name(b)}")

    # EQ
    if head in ("bass", "mid", "treble"):
        if not arg:
            return _say(f"{head}: {await f.get_eq(head):+d}")
        cur = await f.get_eq(head) or 0
        await f.set_eq(head, parse_delta(arg, cur, EQ_MIN, EQ_MAX))
        return _say(f"{head} -> {await f.get_eq(head):+d}")

    # subwoofer level (dB, -21..+10)
    if head in ("sublevel", "sub"):
        if not arg:
            db = await f.get_sub_level_db()
            return _say(f"sub level: {db:+d} dB" if db is not None
                        else "sub level: ? (no subwoofer detected)")
        cur = await f.get_sub_level_db() or 0
        target = parse_delta(arg, cur, SUB_DB_MIN, SUB_DB_MAX)
        await f.set_sub_level_db(target)
        return _say(f"sub level -> {target:+d} dB")

    # toggles
    toggles = {"night": CH_NIGHT, "dynbass": CH_DYNBASS, "submute": CH_SUBMUTE,
               "subinvert": CH_SUBINVERT, "funcsounds": CH_FUNCSOUNDS}
    if head in toggles:
        on = arg in ("on", "1", "true")
        await f.set_toggle(toggles[head], on)
        return _say(f"{head} -> {_onoff(on)}")

    if head == "vocal" and arg.lstrip("-").isdigit():
        await f.set_vocal(int(arg))
        return _say(f"vocal mode -> {clamp(int(arg), 0, 3)}")
    if head == "eq" and arg.lstrip("-").isdigit():
        await f.set_eqmode(int(arg))
        return _say(f"eq preset -> {clamp(int(arg), 0, 5)}")

    # transport
    if head in ("play", "pause", "playpause"):
        await f.play_pause()
        return _say("play/pause toggled")
    if head == "next":
        await f.next_track()
        return _say("next")
    if head == "prev":
        await f.prev_track()
        return _say("prev")

    # name
    if head == "name":
        if not rest:
            return _say(f"name: {await f.get_name()}")
        await f.set_name(rest)
        return _say(f"name -> {rest}")

    # device info (read-only DIS: firmware, serial, ...)
    if head in ("info", "i"):
        di = await f.device_info()
        return _say("\n".join([
            f"Model        : {f.model.display_name}",
            f"Name         : {di.name or '?'}",
            f"Manufacturer : {di.manufacturer or '?'}",
            f"Firmware rev : {di.firmware_revision or '?'}",
            f"MCU firmware : {di.software_revision or '?'}",
            f"Hardware rev : {di.hardware_revision or '?'}",
            f"Model number : {di.model_number or '?'}",
            f"Serial number: {di.serial_number or '?'}",
            f"MAC address  : {di.mac_address or '?'}",
            f"System ID    : {di.system_id or '?'}",
        ]))

    # factory reset (irreversible) — always asks for an explicit "Yes"
    if head in ("factory-reset", "factoryreset", "reset"):
        if not await _confirm(
            "Factory reset ERASES all speaker settings (name, EQ, modes, "
            "pairing) and restarts the speaker. This cannot be undone."
        ):
            return _say("factory reset cancelled")
        await f.factory_reset()
        return _say("factory reset sent — the speaker will restart")

    return None


def _say(s):
    print(s)
    return s


async def _confirm(warning: str) -> bool:
    """Print a warning and require the user to type 'Yes' (case-insensitive).

    Anything else — including a closed/non-interactive stdin (EOF) — cancels, so
    a piped or scripted ``factory-reset`` never wipes a speaker without a TTY.
    """
    print(warning, file=sys.stderr)
    try:
        ans = (await asyncio.to_thread(input, "Type 'Yes' to confirm [Yes/No]: ")).strip()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return False
    return ans.lower() == "yes"


# ---- discovery (no connection) ----------------------------------------------
async def run_discover():
    print("Scanning the air (BLE) for advertising Klipsch speakers...", file=sys.stderr)
    hits = await discover()
    if not hits:
        print("none found (a speaker connected as audio may not advertise)",
              file=sys.stderr)
        return 1
    for hit in hits:
        print(f"{hit.address}\t{hit.name}")
    return 0


# ---- one-shot commands ------------------------------------------------------
async def run_oneshot(f: KlipschClient, args):
    if args.cmd == "status":
        print(await full_status(f))
    else:
        line = args.cmd
        if getattr(args, "value", None):
            line += " " + " ".join(args.value)
        if await handle_command(f, line.strip().lower()) is None:
            print("unknown command")


async def amain(args):
    addr = await resolve_address(args.address)
    print(f"Connecting to the speaker for control ({addr})...", file=sys.stderr)
    f = KlipschClient(addr)
    try:
        await f.connect()
    except KlipschAccessError:
        raise SystemExit(
            "No control access: characteristics are encrypted but there is no\n"
            "working bond. The speaker must be added to the OS as an AUDIO device\n"
            "(not as an 'Other'/LE device). Do NOT unpair.")
    except KlipschNotFoundError as e:
        raise SystemExit(str(e))
    save_address(addr)
    print("Connected (control only).", file=sys.stderr)
    try:
        if args.cmd is None:
            await interactive(f)
        else:
            await run_oneshot(f, args)
    finally:
        print("Dropping the control link (audio untouched)...", file=sys.stderr)
        await f.disconnect()


def build_parser():
    p = argparse.ArgumentParser(
        prog="klipsch",
        description="Optional CLI for Klipsch powered speakers "
                    "(The Fives / Sevens / Nines, BLE via bleak)")
    p.add_argument("--address", help="speaker MAC (otherwise auto-discovered)")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("status", help="print full status and exit")
    sub.add_parser("info", help="print device info (firmware/serial) and exit")
    sub.add_parser("discover", help="scan for advertising speakers and exit")
    for name, hlp in [
        ("set", "volume: 18 | 50%%"), ("up", "louder [N]"), ("down", "quieter [N]"),
        ("mute", ""), ("in", "input: tv|bluetooth|optical|analog|usb|phono|1..6"),
        ("bass", "+-N|N"), ("mid", "+-N|N"), ("treble", "+-N|N"),
        ("sublevel", "subwoofer level dB: +-N|N  (-21..+10)"),
        ("submute", "on|off"), ("subinvert", "on|off"),
        ("night", "on|off"), ("dynbass", "on|off"),
        ("vocal", "0..3"), ("eq", "0..5"),
        ("play", ""), ("next", ""), ("prev", ""), ("name", "<new name>"),
        ("factory-reset", "wipe ALL settings (asks to confirm)"),
    ]:
        sp = sub.add_parser(name, help=hlp)
        sp.add_argument("value", nargs="*")
    return p


def _normalize_oneshot(args):
    if args.cmd == "set":
        args.cmd = (args.value[0] if args.value else "")
        args.value = []
    elif args.cmd == "up":
        n = args.value[0] if args.value else "1"
        args.cmd, args.value = f"+{n}", []
    elif args.cmd == "down":
        n = args.value[0] if args.value else "1"
        args.cmd, args.value = f"-{n}", []
    elif args.cmd == "mute":
        args.cmd, args.value = "m", []
    return args


def _force_utf8():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    _force_utf8()
    args = build_parser().parse_args()
    if args.cmd == "discover":
        raise SystemExit(asyncio.run(run_discover()))
    if args.cmd not in (None, "status"):
        args = _normalize_oneshot(args)
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
