"""Tests for the Windows WinRT fast-path backend (klipsch_ble/winrt_backend.py).

The backend talks to the ``winrt`` projection, which only exists on Windows and is
imported *at call time* (so the module itself imports anywhere). These tests
install a fake ``winrt`` package tree — and a fake ``bleak.exc`` — into
``sys.modules`` so the whole backend runs on the Linux CI with no winrt, no bleak
and no Bluetooth hardware. They pin the GATT read / write / notify paths, the
targeted-and-cached service+characteristic resolution, and the
``not-in-cache -> BleakDeviceNotFoundError`` fallback that lets
``KlipschClient.connect()`` fall back to scan-priming.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from klipsch_ble import constants as c
from klipsch_ble.winrt_backend import (
    WinRTBleakLikeClient,
    _addr_to_int,
    winrt_factory,
)


# ---- fake winrt primitives --------------------------------------------------
class _Status:
    """Stand-in for ``GattCommunicationStatus`` (only ``SUCCESS`` is compared)."""

    SUCCESS = 0
    UNREACHABLE = 1


class _CCCD:
    """Stand-in for ``GattClientCharacteristicConfigurationDescriptorValue``."""

    NOTIFY = "notify"
    NONE = "none"


class _Buffer:
    """An ``IBuffer`` stand-in: just carries bytes and a ``.length``."""

    def __init__(self, data: bytes = b"") -> None:
        self.data = bytes(data)

    @property
    def length(self) -> int:
        return len(self.data)


class _DataReader:
    """``DataReader`` stand-in: copies a buffer's bytes into a bytearray."""

    def __init__(self, buffer: _Buffer) -> None:
        self._buffer = buffer

    @classmethod
    def from_buffer(cls, buffer: _Buffer) -> _DataReader:
        return cls(buffer)

    def read_bytes(self, out: bytearray) -> None:
        out[:] = self._buffer.data


class _DataWriter:
    """``DataWriter`` stand-in: collects written bytes into a buffer."""

    def __init__(self) -> None:
        self._data = b""

    def write_bytes(self, data: bytes) -> None:
        self._data = bytes(data)

    def detach_buffer(self) -> _Buffer:
        return _Buffer(self._data)


class _BleakDeviceNotFoundError(Exception):
    """Stand-in for ``bleak.exc.BleakDeviceNotFoundError``."""


# ---- fake GATT tree ---------------------------------------------------------
class _Char:
    """A GATT characteristic: records writes/CCCD writes and replays a value."""

    def __init__(self, value: bytes = b"") -> None:
        self.value = bytes(value)
        self.writes: list[bytes] = []
        self.cccd_writes: list[str] = []
        self.handlers: dict[int, object] = {}
        self._token = 0
        self.read_status = _Status.SUCCESS
        self.write_status = _Status.SUCCESS
        self.cccd_status = _Status.SUCCESS

    async def read_value_async(self):
        return types.SimpleNamespace(status=self.read_status, value=_Buffer(self.value))

    async def write_value_with_result_async(self, buf: _Buffer):
        self.writes.append(buf.data)
        return types.SimpleNamespace(status=self.write_status)

    async def write_value_async(self, buf: _Buffer):
        self.writes.append(buf.data)
        return self.write_status

    def add_value_changed(self, handler) -> int:
        self._token += 1
        self.handlers[self._token] = handler
        return self._token

    def remove_value_changed(self, token) -> None:
        self.handlers.pop(token, None)

    async def write_client_characteristic_configuration_descriptor_async(self, value):
        self.cccd_writes.append(value)
        return self.cccd_status

    def push(self, data: bytes) -> None:
        """Simulate the speaker pushing a notification to every subscriber."""
        args = types.SimpleNamespace(characteristic_value=_Buffer(data))
        for handler in list(self.handlers.values()):
            handler(None, args)


class _Service:
    def __init__(self, chars: dict[str, _Char]) -> None:
        self._chars = {k.lower(): v for k, v in chars.items()}
        self.lookups = 0

    async def get_characteristics_for_uuid_async(self, u):
        self.lookups += 1
        ch = self._chars.get(str(u).lower())
        return types.SimpleNamespace(characteristics=[ch] if ch else [])


class _Device:
    def __init__(self, services: dict[str, _Service]) -> None:
        self._services = {k.lower(): v for k, v in services.items()}
        self.lookups = 0
        self.closed = False

    async def get_gatt_services_for_uuid_async(self, u):
        self.lookups += 1
        svc = self._services.get(str(u).lower())
        return types.SimpleNamespace(services=[svc] if svc else [])

    def close(self) -> None:
        self.closed = True


def _device_with(char_uuid: str = c.CH_MASTER_VOLUME, value: bytes = b""):
    """Build a one-characteristic device and return ``(device, service, char)``."""
    char = _Char(value)
    service = _Service({char_uuid: char})
    device = _Device({c.CHAR_TO_SERVICE[char_uuid]: service})
    return device, service, char


@pytest.fixture
def fake_winrt(monkeypatch):
    """Install a fake ``winrt`` tree (and ``bleak.exc``) into ``sys.modules`` so
    the backend's call-time ``from winrt... import ...`` resolves to the fakes.

    Returns a holder: set ``.device`` to a ``_Device`` for ``connect()`` to hand
    back (or leave ``None`` to simulate "not in the OS cache"); ``.last_address``
    is the integer address ``connect()`` looked up; ``.NotFoundError`` is the
    bleak error type the not-found path raises.
    """
    holder = types.SimpleNamespace(device=None, last_address=None)

    class _BluetoothLEDevice:
        @staticmethod
        async def from_bluetooth_address_async(addr):
            holder.last_address = addr
            return holder.device

    def _mod(name: str, **attrs) -> types.ModuleType:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
        if "." in name:  # link child onto its (already-registered) parent
            parent, _, child = name.rpartition(".")
            setattr(sys.modules[parent], child, module)
        return module

    _mod("winrt")
    _mod("winrt.windows")
    _mod("winrt.windows.devices")
    _mod("winrt.windows.devices.bluetooth", BluetoothLEDevice=_BluetoothLEDevice)
    _mod(
        "winrt.windows.devices.bluetooth.genericattributeprofile",
        GattCommunicationStatus=_Status,
        GattClientCharacteristicConfigurationDescriptorValue=_CCCD,
    )
    _mod("winrt.windows.storage")
    _mod(
        "winrt.windows.storage.streams",
        DataReader=_DataReader,
        DataWriter=_DataWriter,
    )
    _mod("bleak")
    _mod("bleak.exc", BleakDeviceNotFoundError=_BleakDeviceNotFoundError)

    holder.NotFoundError = _BleakDeviceNotFoundError
    return holder


# ---- construction (no winrt needed) ----------------------------------------
def test_factory_builds_client_with_timeout():
    client = winrt_factory("AA:BB:CC:DD:EE:FF", 7.5)
    assert isinstance(client, WinRTBleakLikeClient)
    assert client.timeout == 7.5


def test_init_accepts_address_string():
    assert WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")._address == "54:B7:E5:8D:8F:0B"


def test_init_accepts_bledevice_with_address():
    dev = types.SimpleNamespace(address="54:B7:E5:8D:8F:0B")
    assert WinRTBleakLikeClient(dev)._address == "54:B7:E5:8D:8F:0B"


def test_init_rejects_target_without_address():
    with pytest.raises(ValueError):
        WinRTBleakLikeClient(object())


def test_addr_to_int_handles_colons_and_dashes():
    assert _addr_to_int("54:B7:E5:8D:8F:0B") == 0x54B7E58D8F0B
    assert _addr_to_int("54-B7-E5-8D-8F-0B") == 0x54B7E58D8F0B


# ---- connect ----------------------------------------------------------------
def test_connect_resolves_device_from_address(fake_winrt):
    device, _, _ = _device_with()
    fake_winrt.device = device

    async def go():
        client = WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")
        assert await client.connect() is client
        assert client._device is device

    asyncio.run(go())
    assert fake_winrt.last_address == 0x54B7E58D8F0B


def test_connect_not_in_cache_raises_bleak_notfound(fake_winrt):
    fake_winrt.device = None  # not in the OS cache (idle / not audio-connected)

    async def go():
        client = WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")
        with pytest.raises(fake_winrt.NotFoundError):
            await client.connect()

    asyncio.run(go())


# ---- read / write -----------------------------------------------------------
def test_read_gatt_char_returns_value(fake_winrt):
    device, _, _ = _device_with(c.CH_MASTER_VOLUME, b"\x12")
    fake_winrt.device = device

    async def go():
        client = WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")
        await client.connect()
        assert bytes(await client.read_gatt_char(c.CH_MASTER_VOLUME)) == b"\x12"

    asyncio.run(go())


def test_read_failure_status_raises_oserror(fake_winrt):
    device, _, char = _device_with(c.CH_MASTER_VOLUME, b"\x12")
    char.read_status = _Status.UNREACHABLE
    fake_winrt.device = device

    async def go():
        client = WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")
        await client.connect()
        with pytest.raises(OSError):
            await client.read_gatt_char(c.CH_MASTER_VOLUME)

    asyncio.run(go())


def test_write_gatt_char_sends_exact_bytes(fake_winrt):
    device, _, char = _device_with(c.CH_MASTER_VOLUME)
    fake_winrt.device = device

    async def go():
        client = WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")
        await client.connect()
        await client.write_gatt_char(c.CH_MASTER_VOLUME, b"\x20", response=True)

    asyncio.run(go())
    assert char.writes == [b"\x20"]


def test_write_failure_status_raises_oserror(fake_winrt):
    device, _, char = _device_with(c.CH_MASTER_VOLUME)
    char.write_status = _Status.UNREACHABLE
    fake_winrt.device = device

    async def go():
        client = WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")
        await client.connect()
        with pytest.raises(OSError):
            await client.write_gatt_char(c.CH_MASTER_VOLUME, b"\x01")

    asyncio.run(go())


def test_service_and_characteristic_are_cached(fake_winrt):
    device, service, _ = _device_with(c.CH_MASTER_VOLUME, b"\x05")
    fake_winrt.device = device

    async def go():
        client = WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")
        await client.connect()
        await client.read_gatt_char(c.CH_MASTER_VOLUME)
        await client.read_gatt_char(c.CH_MASTER_VOLUME)
        await client.write_gatt_char(c.CH_MASTER_VOLUME, b"\x06")

    asyncio.run(go())
    assert device.lookups == 1   # the service is resolved exactly once
    assert service.lookups == 1  # the characteristic is resolved exactly once


# ---- notifications ----------------------------------------------------------
def test_start_notify_delivers_pushed_values(fake_winrt):
    device, _, char = _device_with(c.CH_MASTER_VOLUME, b"\x00")
    fake_winrt.device = device
    received: list[bytes] = []

    async def go():
        client = WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")
        await client.connect()
        await client.start_notify(
            c.CH_MASTER_VOLUME, lambda data: received.append(bytes(data))
        )
        assert char.cccd_writes == [_CCCD.NOTIFY]  # subscribed via the CCCD
        char.push(b"\x14")
        await asyncio.sleep(0)  # let the loop run the marshalled callback
        char.push(b"\x1e")
        await asyncio.sleep(0)

    asyncio.run(go())
    assert received == [b"\x14", b"\x1e"]


def test_start_notify_failure_detaches_handler_and_raises(fake_winrt):
    device, _, char = _device_with(c.CH_MASTER_VOLUME)
    char.cccd_status = _Status.UNREACHABLE  # the CCCD write fails
    fake_winrt.device = device

    async def go():
        client = WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")
        await client.connect()
        with pytest.raises(OSError):
            await client.start_notify(c.CH_MASTER_VOLUME, lambda data: None)
        assert char.handlers == {}  # the value-changed handler was rolled back

    asyncio.run(go())


def test_disconnect_detaches_notify_closes_device_and_clears(fake_winrt):
    device, _, char = _device_with(c.CH_MASTER_VOLUME, b"\x00")
    fake_winrt.device = device

    async def go():
        client = WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")
        await client.connect()
        await client.start_notify(c.CH_MASTER_VOLUME, lambda data: None)
        await client.disconnect()
        assert char.handlers == {}     # handler detached before teardown
        assert device.closed           # device closed
        assert client._device is None  # caches dropped

    asyncio.run(go())


# ---- error paths ------------------------------------------------------------
def test_unknown_characteristic_has_no_service(fake_winrt):
    device, _, _ = _device_with(c.CH_MASTER_VOLUME)
    fake_winrt.device = device
    unknown = "00000000-0000-0000-0000-000000000000"

    async def go():
        client = WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")
        await client.connect()
        with pytest.raises(OSError):
            await client.read_gatt_char(unknown)

    asyncio.run(go())


def test_read_before_connect_raises(fake_winrt):
    async def go():
        client = WinRTBleakLikeClient("54:B7:E5:8D:8F:0B")
        with pytest.raises(OSError):
            await client.read_gatt_char(c.CH_MASTER_VOLUME)

    asyncio.run(go())
