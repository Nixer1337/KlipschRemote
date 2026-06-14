"""Protocol constants, GATT UUIDs and value conversions for Klipsch powered speakers.

These cover the whole "CinemaStream" powered line — **The Fives, The Sevens and
The Nines** (incl. McLaren variants). The line is protocol-identical: one shared
GATT table of ``DA6D0F..`` characteristics, one shared feature set, and an
identity input map (input index i -> byte i) across all three. The unrelated One
Plus / Three Plus portables use a different input map and are out of scope.

The ATT value handles map 1:1 to these UUIDs:
  volume handle ``0x002a`` == ``DA6D0FA2`` (master volume),
  input  handle ``0x0060`` == ``DA6D0FD2`` (input select).
"""

from __future__ import annotations

from enum import IntEnum

# ---- GATT UUIDs -------------------------------------------------------------
# Base suffix for every Klipsch control characteristic.
_SFX = "0d18-442c-babe-f85b5baa6f11"


def _u(short: str) -> str:
    """Build a full Klipsch control UUID from its ``da6d0f<short>`` shorthand."""
    return f"da6d0f{short}-{_SFX}"


# Services
SVC_VOLUME = _u("a1")
SVC_EQ = _u("01")
SVC_INPUT = _u("d1")
SVC_UI = _u("e1")
SVC_AVT = _u("b1")

# Volume service
CH_MASTER_VOLUME = _u("a2")  # 1 byte, 0..0x24
CH_MUTE = _u("a3")           # 1 byte, 0/1
CH_CHANNEL_VOLUME = _u("a4")  # 2 bytes [channel, level]; sub level = [0x04, raw]

# EQ service (level -10..+6 => byte 0..16, flat = 10)
CH_BASS = _u("02")
CH_MID = _u("03")
CH_TREBLE = _u("04")
CH_NIGHT = _u("05")          # 0/1
CH_VOCAL = _u("06")          # 0..3
CH_SUBMUTE = _u("09")        # 0/1
CH_EQMODE = _u("12")         # preset 0..5
CH_SUBSTATUS = _u("13")      # read-only; whole value as int == 1 => sub detected
CH_DYNBASS = _u("14")        # 0/1
CH_SUBINVERT = _u("15")      # 0/1 — subwoofer "Phase Invert"

# Input service
CH_INPUT = _u("d2")          # byte 0..6

# UI service
CH_POWERMODE = _u("e5")      # 0/1 — auto-standby (sleep after inactivity)
CH_NAME = _u("e6")           # UTF-8 string
CH_FACTORY_RESET = _u("e8")  # write 1 byte 0x00 to wipe all settings
CH_FUNCSOUNDS = _u("eb")     # 0/1

# AV transport service
CH_PLAYPAUSE = _u("b2")      # toggle, 1=play / 0=pause
CH_NEXT = _u("b3")           # write 0 to trigger
CH_PREV = _u("b4")           # write 0 to trigger

# Standard Bluetooth Device Information Service (0x180A). Used to identify the
# model: the Model Number string is reported here (not the user-settable Klipsch
# name char), so model detection survives the user renaming the speaker.
SVC_DIS = "0000180a-0000-1000-8000-00805f9b34fb"
CH_SYSTEM_ID = "00002a23-0000-1000-8000-00805f9b34fb"      # 8 bytes (mfr id + OUI)
CH_MODEL_NUMBER = "00002a24-0000-1000-8000-00805f9b34fb"  # e.g. "1067563" (Fives)
CH_SERIAL_NUMBER = "00002a25-0000-1000-8000-00805f9b34fb"   # unit serial string
CH_FIRMWARE_REVISION = "00002a26-0000-1000-8000-00805f9b34fb"  # installed FW version
CH_HW_REVISION = "00002a27-0000-1000-8000-00805f9b34fb"   # int rev: refines McLaren
CH_SOFTWARE_REVISION = "00002a28-0000-1000-8000-00805f9b34fb"  # software/build string
CH_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"   # "Klipsch Group, Inc." etc.


# Which service each characteristic lives under. Used by the Windows WinRT
# fast-path backend to fetch one service + one characteristic on demand
# (targeted, cached) instead of enumerating the whole GATT database.
CHAR_TO_SERVICE: dict[str, str] = {
    CH_MASTER_VOLUME: SVC_VOLUME,
    CH_MUTE: SVC_VOLUME,
    CH_CHANNEL_VOLUME: SVC_VOLUME,
    CH_BASS: SVC_EQ,
    CH_MID: SVC_EQ,
    CH_TREBLE: SVC_EQ,
    CH_NIGHT: SVC_EQ,
    CH_VOCAL: SVC_EQ,
    CH_SUBMUTE: SVC_EQ,
    CH_EQMODE: SVC_EQ,
    CH_SUBSTATUS: SVC_EQ,
    CH_DYNBASS: SVC_EQ,
    CH_SUBINVERT: SVC_EQ,
    CH_INPUT: SVC_INPUT,
    CH_POWERMODE: SVC_UI,
    CH_NAME: SVC_UI,
    CH_FACTORY_RESET: SVC_UI,
    CH_FUNCSOUNDS: SVC_UI,
    CH_PLAYPAUSE: SVC_AVT,
    CH_NEXT: SVC_AVT,
    CH_PREV: SVC_AVT,
    # standard Device Information Service (for model detection + read-only info)
    CH_SYSTEM_ID: SVC_DIS,
    CH_MODEL_NUMBER: SVC_DIS,
    CH_SERIAL_NUMBER: SVC_DIS,
    CH_FIRMWARE_REVISION: SVC_DIS,
    CH_HW_REVISION: SVC_DIS,
    CH_SOFTWARE_REVISION: SVC_DIS,
    CH_MANUFACTURER: SVC_DIS,
}


# ---- volume -----------------------------------------------------------------
MAX_VOLUME_RAW = 0x24  # 36 steps


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def volume_percent_to_raw(percent: int) -> int:
    """Convert 0..100 percent to the raw 0..0x24 step (truncating, per fives-api)."""
    if not 0 <= percent <= 100:
        raise ValueError("volume percent must be between 0 and 100")
    return percent * MAX_VOLUME_RAW // 100


def volume_raw_to_percent(raw: int) -> int:
    """Convert a raw 0..0x24 step to 0..100 percent (truncating, per fives-api)."""
    if not 0 <= raw <= MAX_VOLUME_RAW:
        raise ValueError(f"raw volume must be between 0 and {MAX_VOLUME_RAW}")
    return raw * 100 // MAX_VOLUME_RAW


def volume_raw_to_db(raw: int) -> int:
    """Approximate the on-screen dB label for a raw step (-80..+8). Display-only."""
    return -80 + round(raw * (88 / MAX_VOLUME_RAW))


# ---- EQ (bass / mid / treble) -----------------------------------------------
EQ_MIN, EQ_MAX = -10, 6
EQ_OFFSET = 10  # byte = level + 10, so flat (level 0) == byte 10

EQ_CHANNELS = {"bass": CH_BASS, "mid": CH_MID, "treble": CH_TREBLE}


def eq_level_to_byte(level: int) -> int:
    return clamp(level, EQ_MIN, EQ_MAX) + EQ_OFFSET


def eq_byte_to_level(byte: int) -> int:
    return clamp(byte - EQ_OFFSET, EQ_MIN, EQ_MAX)


# ---- subwoofer --------------------------------------------------------------
# "Sub Level" is written as a 2-byte channel-volume command [channel, raw] to
# CH_CHANNEL_VOLUME, where the subwoofer is channel 0x04 and ``raw`` is 0..31
# (the app's getValueForChanelVolume / checkSubVolumeForWrite). The on-screen dB
# label is ``raw - 21``, so the range is -21..+10 dB and 0 dB == raw 21.
#
# The current level is read BACK from CH_CHANNEL_VOLUME too (the app's
# getChanelInformation reads that characteristic and ``checkSubVolumeForRead``
# takes byte[4] = the subwoofer channel). Whether a sub is physically connected
# is a SEPARATE signal: read CH_SUBSTATUS and test ``int(value) == 1``
# (``checkSubConnectedOnOff``) — that's what drives "Subwoofer not detected".
SUB_CHANNEL = 0x04
SUB_RAW_MIN, SUB_RAW_MAX = 0, 31
SUB_DB_OFFSET = 21               # db = raw - 21
SUB_DB_MIN = SUB_RAW_MIN - SUB_DB_OFFSET   # -21
SUB_DB_MAX = SUB_RAW_MAX - SUB_DB_OFFSET   # +10
SUB_LEVEL_BYTE_INDEX = 4         # subwoofer channel's byte in a ChannelVolume read


def sub_raw_to_db(raw: int) -> int:
    return clamp(raw, SUB_RAW_MIN, SUB_RAW_MAX) - SUB_DB_OFFSET


def sub_db_to_raw(db: int) -> int:
    return clamp(db + SUB_DB_OFFSET, SUB_RAW_MIN, SUB_RAW_MAX)


def sub_detected_from_bytes(data: bytes | None) -> bool | None:
    """Decode the SubStatus characteristic: big-endian int == 1 => detected.

    ``None`` if the characteristic is absent/unreadable (mirrors the app's
    ``checkSubConnectedOnOff``, which is ``BigInteger(bytes).intValue() == 1``).
    """
    if not data:
        return None
    return int.from_bytes(data, "big") == 1


# ---- inputs -----------------------------------------------------------------
class Input(IntEnum):
    """Input selector values for the Fives/Sevens/Nines line (identity byte map)."""

    OFF = 0
    TV = 1
    BLUETOOTH = 2
    OPTICAL = 3
    AUX = 4
    USB = 5
    PHONO = 6


INPUT_ALIASES: dict[str, Input] = {
    "off": Input.OFF,
    "tv": Input.TV,
    "hdmi": Input.TV,
    "arc": Input.TV,
    "bt": Input.BLUETOOTH,
    "bluetooth": Input.BLUETOOTH,
    "optical": Input.OPTICAL,
    "opt": Input.OPTICAL,
    "aux": Input.AUX,
    "analog": Input.AUX,
    "minijack": Input.AUX,
    "usb": Input.USB,
    "phono": Input.PHONO,
    "line": Input.PHONO,
    "rca": Input.PHONO,
}

INPUT_NAMES: dict[Input, str] = {
    Input.OFF: "off",
    Input.TV: "tv",
    Input.BLUETOOTH: "bluetooth",
    Input.OPTICAL: "optical",
    Input.AUX: "aux",
    Input.USB: "usb",
    Input.PHONO: "phono",
}


def normalize_input(value: "Input | str | int") -> Input:
    """Coerce a user-facing input (enum / name / number) to an :class:`Input`."""
    if isinstance(value, Input):
        return value
    if isinstance(value, str):
        key = value.strip().lower()
        if key.isdigit():
            return normalize_input(int(key))
        try:
            return INPUT_ALIASES[key]
        except KeyError as exc:
            choices = ", ".join(sorted(INPUT_ALIASES))
            raise ValueError(f"unknown input {value!r}; expected one of: {choices}") from exc
    try:
        return Input(value)
    except ValueError as exc:
        valid = ", ".join(f"{name}={item.value}" for item, name in INPUT_NAMES.items())
        raise ValueError(f"unknown input value {value!r}; expected one of: {valid}") from exc


def input_name(value: "Input | int") -> str:
    """Canonical lowercase name for an input value."""
    return INPUT_NAMES[normalize_input(value)]
