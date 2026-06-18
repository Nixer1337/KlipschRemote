"""KlipschClient driven against a fake in-memory GATT transport.

These exercise the ``client_factory`` / ``BleakLike`` injection seam the library
was designed around — no bleak, no hardware, no event-loop plumbing beyond
``asyncio.run`` per test.
"""

from __future__ import annotations

import asyncio

import pytest

from klipsch_ble import (
    KlipschClient,
    KlipschError,
    PowerOffDisabledError,
)
from klipsch_ble import constants as c
from klipsch_ble.models import KlipschModel


class FakeBleak:
    """In-memory ``BleakLike``: a dict of characteristic UUID -> bytes."""

    def __init__(self, values: dict[str, bytes] | None = None) -> None:
        # Sensible defaults so _ensure_access (reads master volume) succeeds.
        self.store: dict[str, bytes] = {c.CH_MASTER_VOLUME: bytes([12])}
        if values:
            self.store.update(values)
        self.connected = False
        self.writes: list[tuple[str, bytes]] = []

    async def connect(self) -> FakeBleak:
        self.connected = True
        return self

    async def disconnect(self) -> None:
        self.connected = False

    async def read_gatt_char(self, char: str) -> bytearray:
        if char not in self.store:
            raise OSError(f"no such characteristic {char}")
        return bytearray(self.store[char])

    async def write_gatt_char(self, char: str, data: bytes, response: bool = True) -> None:
        self.store[char] = bytes(data)
        self.writes.append((char, bytes(data)))


def make_client(fake: FakeBleak, **kw) -> KlipschClient:
    return KlipschClient(
        "AA:BB:CC:DD:EE:FF",
        client_factory=lambda target, timeout: fake,
        detect_model=False,
        **kw,
    )


def run(coro):
    return asyncio.run(coro)


# ---- connection ------------------------------------------------------------
def test_connect_and_disconnect():
    fake = FakeBleak()

    async def go():
        client = make_client(fake)
        async with client:
            assert fake.connected
        assert not fake.connected

    run(go())


def test_read_before_connect_raises():
    async def go():
        client = make_client(FakeBleak())
        with pytest.raises(KlipschError):
            await client.get_volume_raw()

    run(go())


# ---- volume / mute ---------------------------------------------------------
def test_volume_raw_clamped_on_read_and_write():
    fake = FakeBleak({c.CH_MASTER_VOLUME: bytes([200])})  # absurd raw

    async def go():
        async with make_client(fake) as client:
            assert await client.get_volume_raw() == c.MAX_VOLUME_RAW
            await client.set_volume_raw(999)
            assert fake.store[c.CH_MASTER_VOLUME] == bytes([c.MAX_VOLUME_RAW])

    run(go())


def test_volume_percent_truncates():
    fake = FakeBleak()

    async def go():
        async with make_client(fake) as client:
            await client.set_volume_percent(50)
            assert fake.store[c.CH_MASTER_VOLUME] == bytes([18])
            assert await client.get_volume_percent() == 50

    run(go())


def test_mute_roundtrip_and_absent_is_none():
    async def go():
        fake = FakeBleak({c.CH_MUTE: bytes([1])})
        async with make_client(fake) as client:
            assert await client.get_mute() is True
            await client.set_mute(False)
            assert fake.store[c.CH_MUTE] == bytes([0])
        # Characteristic absent -> None, not an exception.
        async with make_client(FakeBleak()) as client:
            assert await client.get_mute() is None

    run(go())


# ---- input -----------------------------------------------------------------
def test_set_input_by_alias_writes_byte():
    fake = FakeBleak()

    async def go():
        async with make_client(fake) as client:
            await client.set_input("optical")
            assert fake.store[c.CH_INPUT] == bytes([c.Input.OPTICAL.value])

    run(go())


def test_power_off_guarded_by_default():
    fake = FakeBleak()

    async def go():
        async with make_client(fake) as client:
            with pytest.raises(PowerOffDisabledError):
                await client.set_input("off")
        async with make_client(FakeBleak(), allow_power_off=True) as client:
            await client.set_input("off")  # now allowed

    run(go())


# ---- EQ --------------------------------------------------------------------
def test_eq_level_maps_through_offset():
    fake = FakeBleak()

    async def go():
        async with make_client(fake) as client:
            await client.set_eq("bass", 3)
            assert fake.store[c.CH_BASS] == bytes([13])  # 3 + offset 10
            assert await client.get_eq("bass") == 3

    run(go())


def test_eq_unknown_channel_raises():
    async def go():
        async with make_client(FakeBleak()) as client:
            with pytest.raises(ValueError):
                await client.set_eq("subwoofer", 1)

    run(go())


# ---- subwoofer -------------------------------------------------------------
def test_sub_level_reads_channel_volume_byte4_and_writes_command():
    # The level is read from the ChannelVolume characteristic, byte[4] (the sub
    # channel); 21 -> 0 dB. Writes go to the SAME characteristic as [channel, raw].
    fake = FakeBleak({c.CH_CHANNEL_VOLUME: bytes([0, 0, 0, 0, 21])})

    async def go():
        async with make_client(fake) as client:
            assert await client.get_sub_level_raw() == 21
            assert await client.get_sub_level_db() == 0
            await client.set_sub_level_db(5)            # raw 26
            assert fake.store[c.CH_CHANNEL_VOLUME] == bytes([c.SUB_CHANNEL, 26])
            await client.set_sub_level_raw(999)         # clamps to 31
            assert fake.store[c.CH_CHANNEL_VOLUME] == bytes([c.SUB_CHANNEL, 31])

    run(go())


def test_sub_level_absent_or_short_is_none():
    async def go():
        # Characteristic absent entirely.
        async with make_client(FakeBleak()) as client:
            assert await client.get_sub_level_raw() is None
            assert await client.get_sub_level_db() is None
        # Present but too short to hold byte[4] -> None, not IndexError.
        short = FakeBleak({c.CH_CHANNEL_VOLUME: bytes([0, 0])})
        async with make_client(short) as client:
            assert await client.get_sub_level_raw() is None

    run(go())


def test_sub_detected_from_substatus():
    async def go():
        # int(value) == 1 -> detected; 0 -> not; multi-byte big-endian still works.
        async with make_client(FakeBleak({c.CH_SUBSTATUS: bytes([1])})) as client:
            assert await client.get_sub_detected() is True
        async with make_client(FakeBleak({c.CH_SUBSTATUS: bytes([0])})) as client:
            assert await client.get_sub_detected() is False
        async with make_client(FakeBleak({c.CH_SUBSTATUS: bytes([0, 1])})) as client:
            assert await client.get_sub_detected() is True
        # Absent characteristic -> None (unknown), not False.
        async with make_client(FakeBleak()) as client:
            assert await client.get_sub_detected() is None

    run(go())


def test_sub_toggles_roundtrip():
    fake = FakeBleak({c.CH_SUBINVERT: bytes([1]), c.CH_SUBMUTE: bytes([0])})

    async def go():
        async with make_client(fake) as client:
            assert await client.get_sub_invert() is True
            await client.set_sub_invert(False)
            assert fake.store[c.CH_SUBINVERT] == bytes([0])
            assert await client.get_sub_mute() is False
            await client.set_sub_mute(True)
            assert fake.store[c.CH_SUBMUTE] == bytes([1])

    run(go())


# ---- speaker placement / boundary gain -------------------------------------
def test_placement_roundtrip_and_default():
    from klipsch_ble.constants import Placement

    async def go():
        # A valid stored byte decodes to the matching placement.
        fake = FakeBleak({c.CH_BOUNDARY_GAIN: bytes([Placement.OPEN.value])})
        async with make_client(fake) as client:
            assert await client.get_placement() is Placement.OPEN
            await client.set_placement("corner")
            assert fake.store[c.CH_BOUNDARY_GAIN] == bytes([Placement.CORNER.value])
        # Absent (or unrecognised) -> WALL default, not an exception.
        async with make_client(FakeBleak()) as client:
            assert await client.get_placement() is Placement.WALL

    run(go())


# ---- factory reset ---------------------------------------------------------
def test_factory_reset_writes_single_zero():
    fake = FakeBleak()

    async def go():
        async with make_client(fake) as client:
            await client.factory_reset()

    run(go())
    assert (c.CH_FACTORY_RESET, b"\x00") in fake.writes


# ---- transport (stateless) -------------------------------------------------
def test_transport_writes_fixed_commands():
    fake = FakeBleak()

    async def go():
        async with make_client(fake) as client:
            await client.play_pause()
            await client.next_track()
            await client.prev_track()

    run(go())
    assert (c.CH_PLAYPAUSE, bytes([1])) in fake.writes
    assert (c.CH_NEXT, bytes([0])) in fake.writes
    assert (c.CH_PREV, bytes([0])) in fake.writes


# ---- name ------------------------------------------------------------------
def test_name_decodes_to_nul_terminator():
    fake = FakeBleak({c.CH_NAME: b"Living Room\x00garbage"})

    async def go():
        async with make_client(fake) as client:
            assert await client.get_name() == "Living Room"
            await client.set_name("Den")
            assert fake.store[c.CH_NAME] == b"Den"

    run(go())


# ---- device info (standard DIS, read-only) ---------------------------------
def test_device_info_reads_dis_and_tolerates_absent():
    fake = FakeBleak({
        c.CH_NAME: b"Living Room\x00",
        c.CH_MANUFACTURER: b"Klipsch Group, Inc.\x00",
        c.CH_MODEL_NUMBER: b"1067563",
        c.CH_SERIAL_NUMBER: b"SN12345\x00",
        c.CH_FIRMWARE_REVISION: b"1.2.3",
        c.CH_SOFTWARE_REVISION: b"4.5.6",
        c.CH_SYSTEM_ID: bytes([0xAA, 0xBB, 0xCC, 0x00, 0x00, 0xDD, 0xEE, 0xFF]),
        # hardware revision deliberately absent -> None, not an error
    })

    async def go():
        async with make_client(fake, model=KlipschModel.FIVES) as client:
            assert await client.get_firmware_revision() == "1.2.3"
            assert await client.get_serial_number() == "SN12345"
            return await client.device_info()

    di = run(go())
    assert di.model == "fives"
    assert di.name == "Living Room"
    assert di.manufacturer == "Klipsch Group, Inc."
    assert di.model_number == "1067563"
    assert di.serial_number == "SN12345"
    assert di.firmware_revision == "1.2.3"
    assert di.software_revision == "4.5.6"
    assert di.hardware_revision is None
    assert di.system_id == "AA:BB:CC:00:00:DD:EE:FF"
    assert isinstance(di.as_dict(), dict)


# ---- live notifications ----------------------------------------------------
class NotifyFake(FakeBleak):
    """FakeBleak that records notification subscriptions and can push values.

    ``start_notify`` stores the callback; ``push`` invokes it with raw bytes the
    way the WinRT backend does (single positional arg = the value).
    """

    def __init__(self, values: dict[str, bytes] | None = None) -> None:
        super().__init__(values)
        self.subs: dict[str, object] = {}

    async def start_notify(self, char: str, callback) -> None:
        self.subs[char] = callback

    def push(self, char: str, data: bytes) -> None:
        self.subs[char](bytes(data))


def test_subscribe_wires_volume_mute_and_input():
    """``subscribe`` wires up master volume (the physical knob) plus the mute /
    input channels (pushed by the IR remote), decoding each pushed value: volume
    is clamped, mute -> bool, input byte -> canonical name."""
    fake = NotifyFake()
    events: list[tuple[str, object]] = []

    async def go():
        async with make_client(fake) as client:
            await client.subscribe(lambda field, value: events.append((field, value)))
            assert list(fake.subs) == [c.CH_MASTER_VOLUME, c.CH_MUTE, c.CH_INPUT]
            fake.push(c.CH_MASTER_VOLUME, bytes([20]))
            fake.push(c.CH_MASTER_VOLUME, bytes([200]))  # over-range -> clamped
            fake.push(c.CH_MUTE, bytes([1]))
            fake.push(c.CH_INPUT, bytes([c.Input.OPTICAL.value]))
            fake.push(c.CH_INPUT, bytes([99]))  # unknown input byte -> dropped

    run(go())
    assert events == [
        ("volume_raw", 20),
        ("volume_raw", c.MAX_VOLUME_RAW),
        ("mute", True),
        ("input", "optical"),
    ]


def test_subscribe_drops_empty_volume_push():
    fake = NotifyFake()
    events: list[tuple[str, object]] = []

    async def go():
        async with make_client(fake) as client:
            await client.subscribe(lambda field, value: events.append((field, value)))
            fake.push(c.CH_MASTER_VOLUME, b"")  # empty payload -> ignored

    run(go())
    assert events == []


# ---- aggregate status ------------------------------------------------------
def test_status_aggregates_all_fields():
    fake = FakeBleak({
        c.CH_MASTER_VOLUME: bytes([18]),
        c.CH_MUTE: bytes([0]),
        c.CH_INPUT: bytes([c.Input.OPTICAL.value]),
        c.CH_BASS: bytes([13]), c.CH_MID: bytes([10]), c.CH_TREBLE: bytes([8]),
        c.CH_NIGHT: bytes([1]), c.CH_DYNBASS: bytes([0]),
        c.CH_CHANNEL_VOLUME: bytes([0, 0, 0, 0, 21]),  # byte[4]=21 -> 0 dB
        c.CH_SUBSTATUS: bytes([1]),                    # int==1 -> detected
        c.CH_SUBINVERT: bytes([1]), c.CH_SUBMUTE: bytes([0]),
    })

    async def go():
        async with make_client(fake, model=KlipschModel.FIVES) as client:
            st = await client.status()
            return st

    st = run(go())
    assert st.model == "fives"
    assert st.input == "optical" and st.input_value == c.Input.OPTICAL.value
    assert st.volume_raw == 18 and st.volume_percent == 50
    assert (st.bass, st.mid, st.treble) == (3, 0, -2)
    assert st.mute is False and st.night is True and st.dynamic_bass is False
    assert st.sub_level_db == 0 and st.sub_invert is True and st.sub_mute is False
    assert st.sub_detected is True
    assert isinstance(st.as_dict(), dict)
