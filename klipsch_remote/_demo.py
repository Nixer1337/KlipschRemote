"""Offline demo transport — populates the UI with a realistic speaker for
screenshots, without any Bluetooth hardware.

Enabled by ``KLIPSCH_DEMO=1`` (see ``app._new_client``). It reuses the library's
``client_factory`` injection seam (the same one the tests drive) so the *real*
``KlipschClient`` decoding runs against an in-memory GATT table — the UI is
identical to a live connection, only the bytes are canned.

This module is imported lazily, only when demo mode is on, so it never touches a
normal launch.
"""

from __future__ import annotations

from klipsch_ble import KlipschClient
from klipsch_ble import constants as c
from klipsch_ble.models import KlipschModel

# A friendly placeholder address (never dialled — the fake transport ignores it).
DEMO_ADDRESS = "DE:M0:00:00:00:01"

# What the Connect screen shows in demo mode: a fully random, made-up MAC (NOT
# Klipsch's real 54:B7:E5 OUI) so screenshots leak nothing about the user's
# actual paired speaker — not even the vendor prefix.
DEMO_DISPLAY_ADDRESS = "3F:A9:1C:7E:42:D8"


class _PairedDevice:
    """Minimal stand-in for list_paired_bluetooth()'s items (name + address)."""

    def __init__(self, name: str, address: str) -> None:
        self.name = name
        self.address = address


def paired_devices() -> list[_PairedDevice]:
    """The canned paired-device list for the Connect screen in demo mode."""
    return [_PairedDevice("The Fives", DEMO_DISPLAY_ADDRESS)]


def _store() -> dict[str, bytes]:
    """The canned GATT table behind the demo speaker.

    Chosen to look good in a screenshot: a named speaker on Optical, ~60%
    volume, the Rock EQ preset, a detected subwoofer at +6 dB, Dynamic Bass on,
    and a full Device-Information set for the About page.
    """
    return {
        # volume service
        c.CH_MASTER_VOLUME: bytes([22]),                 # 22/36 -> 61 %
        c.CH_MUTE: bytes([0]),
        c.CH_CHANNEL_VOLUME: bytes([0, 0, 0, 0, 27]),    # sub byte[4]=27 -> +6 dB
        # EQ service — Rock preset (bass +3, mid -1, treble +3)
        c.CH_BASS: bytes([c.eq_level_to_byte(3)]),
        c.CH_MID: bytes([c.eq_level_to_byte(-1)]),
        c.CH_TREBLE: bytes([c.eq_level_to_byte(3)]),
        c.CH_NIGHT: bytes([0]),
        c.CH_DYNBASS: bytes([1]),
        c.CH_SUBSTATUS: bytes([1]),                      # subwoofer detected
        c.CH_SUBINVERT: bytes([0]),
        c.CH_SUBMUTE: bytes([0]),
        # input service
        c.CH_INPUT: bytes([c.Input.OPTICAL.value]),
        # UI service
        c.CH_POWERMODE: bytes([1]),                      # auto-standby on
        c.CH_NAME: b"The Fives",
        # standard Device Information Service (About page)
        c.CH_MANUFACTURER: b"Klipsch Group, Inc.\x00",
        c.CH_MODEL_NUMBER: b"1067563",
        c.CH_SERIAL_NUMBER: b"KS-2417-008842\x00",
        c.CH_FIRMWARE_REVISION: b"1.6.9",
        c.CH_SOFTWARE_REVISION: b"7.0.3",
        c.CH_HW_REVISION: b"02.00.01",
        c.CH_SYSTEM_ID: bytes([0x00, 0x1A, 0x7D, 0x00, 0x00, 0xDA, 0x6D, 0x0F]),
    }


class DemoTransport:
    """In-memory ``BleakLike``: a dict of characteristic UUID -> bytes.

    Mirrors the test suite's ``FakeBleak`` — reads return the stored bytes,
    writes update the store (so toggling a switch in the demo sticks for the
    session), and connect/disconnect are no-ops.
    """

    def __init__(self) -> None:
        self.store = _store()
        self.connected = False

    async def connect(self) -> DemoTransport:
        self.connected = True
        return self

    async def disconnect(self) -> None:
        self.connected = False

    async def read_gatt_char(self, char: str) -> bytearray:
        if char not in self.store:
            raise OSError(f"no such characteristic {char}")
        return bytearray(self.store[char])

    async def write_gatt_char(self, char: str, data: bytes,
                              response: bool = True) -> None:
        self.store[char] = bytes(data)


def make_client(address: str) -> KlipschClient:
    """A ``KlipschClient`` wired to the in-memory demo transport.

    Model is pinned (no detection round-trip) so the About page reads cleanly.
    """
    transport = DemoTransport()
    return KlipschClient(
        address,
        model=KlipschModel.FIVES,
        detect_model=False,
        client_factory=lambda target, timeout: transport,
    )
