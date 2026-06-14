"""Windows-only fast GATT backend (WinRT) implementing the :class:`BleakLike` seam.

Why this exists: bleak's ``BleakClient.connect()`` performs a *full* GATT
discovery (every service, characteristic and descriptor) before it returns,
which on Klipsch powered speakers costs ~6-10 s. The speaker only needs a handful
of characteristics, so this backend resolves the device from the OS cache
(``from_bluetooth_address_async``) and fetches one service + one characteristic
on demand, cached. That brings a cold connect to ~1.7 s and a warm reconnect to
near-instant, matching the original hand-written WinRT CLI.

It is used automatically as the default factory on Windows (see
``KlipschClient``); Linux/macOS keep using bleak. The public client code only
ever talks to it through the four ``BleakLike`` methods.

Never pair()/unpair(): the LE key is derived from the Classic audio bond (CTKD).
"""

from __future__ import annotations

import uuid as _uuid

from .constants import CHAR_TO_SERVICE


def _addr_to_int(address: str) -> int:
    return int(address.replace(":", "").replace("-", ""), 16)


class WinRTBleakLikeClient:
    """Minimal WinRT GATT client matching the subset of bleak we rely on.

    ``target`` is either a MAC string or a bleak ``BLEDevice`` (passed by the
    scan-prime fallback); in both cases we connect via the numeric address.
    """

    def __init__(self, target: object, timeout: float = 10.0) -> None:
        self._address = target if isinstance(target, str) else getattr(target, "address", None)
        if not self._address:
            raise ValueError("WinRT backend needs a Bluetooth address")
        self.timeout = timeout
        self._device = None
        # caches so repeated reads/writes don't re-resolve service + char
        self._services: dict[str, object] = {}
        self._chars: dict[str, object] = {}

    # --- BleakLike ---
    async def connect(self) -> "WinRTBleakLikeClient":
        from winrt.windows.devices.bluetooth import BluetoothLEDevice

        device = await BluetoothLEDevice.from_bluetooth_address_async(
            _addr_to_int(self._address)
        )
        if device is None:
            # Not in the OS cache (idle / not audio-connected). Raise the bleak
            # error type so KlipschClient.connect() falls back to scan-prime.
            from bleak.exc import BleakDeviceNotFoundError

            raise BleakDeviceNotFoundError(
                f"{self._address} not in the OS device cache"
            )
        self._device = device
        return self

    async def disconnect(self) -> None:
        self._chars.clear()
        self._services.clear()
        dev, self._device = self._device, None
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass

    async def read_gatt_char(self, char: str) -> bytearray:
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattCommunicationStatus,
        )
        from winrt.windows.storage.streams import DataReader

        characteristic = await self._characteristic(char)
        result = await characteristic.read_value_async()
        if result.status != GattCommunicationStatus.SUCCESS:
            raise OSError(f"GATT read failed for {char}: status {result.status}")
        buf = result.value
        out = bytearray(buf.length)
        if buf.length:
            DataReader.from_buffer(buf).read_bytes(out)
        return out

    async def write_gatt_char(self, char: str, data: bytes, response: bool = True) -> None:
        # This winrt projection only exposes the single-argument overloads, which
        # default to write-with-response (what the protocol always uses).
        from winrt.windows.devices.bluetooth.genericattributeprofile import (
            GattCommunicationStatus,
        )
        from winrt.windows.storage.streams import DataWriter

        characteristic = await self._characteristic(char)
        writer = DataWriter()
        writer.write_bytes(bytes(data))
        buf = writer.detach_buffer()
        if response:
            result = await characteristic.write_value_with_result_async(buf)
            status = result.status
        else:
            status = await characteristic.write_value_async(buf)
        if status != GattCommunicationStatus.SUCCESS:
            raise OSError(f"GATT write failed for {char}: status {status}")

    # --- internals: targeted, cached service/char lookup ---
    async def _characteristic(self, char: str):
        if self._device is None:
            raise OSError("not connected")
        cached = self._chars.get(char)
        if cached is not None:
            return cached
        service = await self._service_for(char)
        result = await service.get_characteristics_for_uuid_async(_uuid.UUID(char))
        chars = list(result.characteristics)
        if not chars:
            raise OSError(f"characteristic {char} not found")
        self._chars[char] = chars[0]
        return chars[0]

    async def _service_for(self, char: str):
        service_uuid = CHAR_TO_SERVICE.get(char.lower())
        if service_uuid is None:
            raise OSError(f"no known service for characteristic {char}")
        cached = self._services.get(service_uuid)
        if cached is not None:
            return cached
        result = await self._device.get_gatt_services_for_uuid_async(
            _uuid.UUID(service_uuid)
        )
        services = list(result.services)
        if not services:
            raise OSError(f"service {service_uuid} not found")
        self._services[service_uuid] = services[0]
        return services[0]


def winrt_factory(target: object, timeout: float) -> WinRTBleakLikeClient:
    """``ClientFactory`` returning a WinRT fast-path client."""
    return WinRTBleakLikeClient(target, timeout)
