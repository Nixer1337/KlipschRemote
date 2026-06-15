"""Cross-platform async client for Klipsch powered speakers over BLE GATT.

Covers the whole protocol-identical line — The Fives, The Sevens, The Nines
(incl. McLaren variants). One code path for Windows / Linux / macOS: bleak picks
the right OS backend (WinRT / BlueZ / CoreBluetooth), or the Windows WinRT
fast-path backend. Characteristics are addressed by UUID, so the same calls work
on every platform and every model.

Connection strategy:
  1. try to connect by address straight away — fast path that works while the
     speaker is connected as Bluetooth *audio* (it is in the OS cache but does
     NOT advertise BLE, so a scan would not find it);
  2. on failure, scan-prime with ``BleakScanner.find_device_by_address`` and
     retry with the discovered device.

Never pair()/unpair(): a dual-mode unit derives its LE key from the Classic
audio bond (CTKD). A separate LE bond breaks GATT; unpairing destroys the
working audio bond. The speaker must already be added to the OS as an audio
device.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Protocol, cast, runtime_checkable

from .constants import (
    CH_CHANNEL_VOLUME,
    CH_DYNBASS,
    CH_EQMODE,
    CH_FACTORY_RESET,
    CH_FIRMWARE_REVISION,
    CH_HW_REVISION,
    CH_INPUT,
    CH_MANUFACTURER,
    CH_MASTER_VOLUME,
    CH_MODEL_NUMBER,
    CH_MUTE,
    CH_NAME,
    CH_NEXT,
    CH_NIGHT,
    CH_PLAYPAUSE,
    CH_PREV,
    CH_SERIAL_NUMBER,
    CH_SOFTWARE_REVISION,
    CH_SUBINVERT,
    CH_SUBMUTE,
    CH_SUBSTATUS,
    CH_SYSTEM_ID,
    CH_VOCAL,
    EQ_CHANNELS,
    MAX_VOLUME_RAW,
    SUB_CHANNEL,
    SUB_DB_MAX,
    SUB_DB_MIN,
    SUB_LEVEL_BYTE_INDEX,
    SUB_RAW_MAX,
    SUB_RAW_MIN,
    Input,
    clamp,
    eq_byte_to_level,
    eq_level_to_byte,
    input_name,
    normalize_input,
    sub_db_to_raw,
    sub_detected_from_bytes,
    sub_raw_to_db,
    volume_percent_to_raw,
    volume_raw_to_db,
    volume_raw_to_percent,
)
from .models import KlipschModel, resolve_model


# ---- errors -----------------------------------------------------------------
class KlipschError(Exception):
    """Base error for the klipsch-ble client."""


class KlipschNotFoundError(KlipschError):
    """The speaker could not be resolved (not paired / asleep / out of range)."""


class KlipschAccessError(KlipschError):
    """Characteristics are unreachable — usually no working (audio) bond."""


class PowerOffDisabledError(ValueError):
    """``set_input('off')`` attempted without ``allow_power_off=True``."""


# ---- transport seam (so tests can inject a fake) ----------------------------
@runtime_checkable
class BleakLike(Protocol):
    """The subset of :class:`bleak.BleakClient` this library relies on."""

    async def connect(self) -> object: ...
    async def disconnect(self) -> object: ...
    async def read_gatt_char(self, char: str) -> bytearray: ...
    async def write_gatt_char(self, char: str, data: bytes, response: bool = ...) -> object: ...


# A factory builds an *unconnected* client for a target (address or BLEDevice).
ClientFactory = Callable[[object, float], BleakLike]


# ---- status -----------------------------------------------------------------
@dataclass
class KlipschStatus:
    model: str
    input: str
    input_value: int
    volume_raw: int
    volume_percent: int
    volume_db: int
    mute: bool | None
    bass: int | None
    mid: int | None
    treble: int | None
    night: bool | None
    dynamic_bass: bool | None
    sub_level_db: int | None
    sub_invert: bool | None
    sub_mute: bool | None
    sub_detected: bool | None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DeviceInfo:
    """Read-only identity from the standard Device Information Service (0x180A).

    Mirrors the full set of characteristics the Klipsch firmware-updater app's
    ``KlipschGATT`` reads (reversed from the app). Every field is best-effort:
    ``None`` if the speaker does not expose that characteristic.
    """

    model: str
    name: str | None
    manufacturer: str | None   # DIS 0x2A29
    model_number: str | None   # DIS 0x2A24
    serial_number: str | None  # DIS 0x2A25
    mac_address: str | None    # serial (= BD_ADDR) rendered as a colon MAC
    firmware_revision: str | None  # DIS 0x2A26
    software_revision: str | None  # DIS 0x2A28
    hardware_revision: str | None  # DIS 0x2A27
    system_id: str | None      # DIS 0x2A23 (8 bytes, shown as hex)

    def as_dict(self) -> dict:
        return asdict(self)


class KlipschClient:
    """Async controller for one Klipsch powered speaker (Fives/Sevens/Nines)."""

    def __init__(
        self,
        address: str,
        *,
        timeout: float = 10.0,
        scan_timeout: float = 15.0,
        allow_power_off: bool = False,
        model: KlipschModel | None = None,
        detect_model: bool = True,
        client_factory: ClientFactory | None = None,
    ) -> None:
        if not address:
            raise ValueError("Bluetooth address is required")
        self.address = address
        self.timeout = timeout
        self.scan_timeout = scan_timeout
        self.allow_power_off = allow_power_off
        # If the caller pins a model we trust it and skip auto-detection.
        self.model: KlipschModel = model or KlipschModel.UNKNOWN
        self._detect_model = detect_model and model is None
        self._client_factory = client_factory or _default_factory()
        self._client: BleakLike | None = None

    # --- connection ---
    async def connect(self) -> KlipschClient:
        if self._client is not None:
            return self
        client = self._client_factory(self.address, self.timeout)
        try:
            await client.connect()
        except Exception as exc:  # narrowed below
            client = await self._scan_prime_and_build(exc)
            await client.connect()
        self._client = client
        await self._ensure_access()
        if self._detect_model:
            await self.detect_model()
        return self

    async def detect_model(self) -> KlipschModel:
        """Identify the speaker from the standard DIS characteristics.

        Best-effort: reads Model Number (0x2A24) + Hardware Revision (0x2A27),
        falling back to the Klipsch device name. Never raises — an unrecognised
        or unreadable device just stays ``UNKNOWN``. The result is cached on
        ``self.model``.
        """
        model_number = _decode_ascii(await self.read_raw(CH_MODEL_NUMBER))
        hw_revision = _decode_ascii(await self.read_raw(CH_HW_REVISION))
        name = await self.get_name()
        self.model = resolve_model(model_number, hw_revision, name)
        return self.model

    def supports(self, feature: str) -> bool:
        """Whether the detected model exposes ``feature`` (see ``models.FEATURES``).

        An ``UNKNOWN`` model optimistically reports the full control set, since
        the GATT protocol is shared across the line.
        """
        return self.model.supports(feature)

    async def _scan_prime_and_build(self, original: Exception) -> BleakLike:
        """Speaker not in the OS cache: scan until it is seen, then rebuild."""
        try:
            from bleak import BleakScanner
            from bleak.exc import BleakDeviceNotFoundError, BleakError
        except Exception:  # pragma: no cover - bleak always present in practice
            raise KlipschNotFoundError(str(original)) from original
        if not isinstance(original, (BleakDeviceNotFoundError, BleakError)):
            raise original
        device = await BleakScanner.find_device_by_address(
            self.address, timeout=self.scan_timeout
        )
        if device is None:
            raise KlipschNotFoundError(
                f"{self.address} not found. The speaker must be paired with this "
                "machine as an AUDIO device and awake (not in standby)."
            ) from original
        return self._client_factory(device, self.timeout)

    async def _ensure_access(self) -> None:
        """One cheap read to surface an unencrypted/unbonded link early."""
        try:
            await self._require_client().read_gatt_char(CH_MASTER_VOLUME)
        except KlipschError:
            raise
        except Exception as exc:
            raise KlipschAccessError(
                "control characteristics are unreachable — there is no working "
                "bond. Add the speaker to the OS as an AUDIO device (not as a "
                "generic / LE 'Other device'), then retry. Never unpair."
            ) from exc

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    async def __aenter__(self) -> KlipschClient:
        return await self.connect()

    async def __aexit__(self, *exc: object) -> None:
        await self.disconnect()

    # --- raw I/O by UUID ---
    def _require_client(self) -> BleakLike:
        if self._client is None:
            raise KlipschError("not connected — call connect() first")
        return self._client

    async def read_raw(self, char_uuid: str) -> bytes | None:
        try:
            data = await self._require_client().read_gatt_char(char_uuid)
        except KlipschError:
            raise
        except Exception:  # characteristic absent / unreadable
            return None
        return bytes(data)

    async def write_raw(self, char_uuid: str, data: bytes) -> None:
        await self._require_client().write_gatt_char(char_uuid, bytes(data), response=True)

    async def read_byte(self, char_uuid: str) -> int | None:
        data = await self.read_raw(char_uuid)
        return data[0] if data else None

    async def write_byte(self, char_uuid: str, value: int) -> None:
        await self.write_raw(char_uuid, bytes([value & 0xFF]))

    # --- volume / mute ---
    async def get_volume_raw(self) -> int:
        return clamp(await self.read_byte(CH_MASTER_VOLUME) or 0, 0, MAX_VOLUME_RAW)

    async def set_volume_raw(self, raw: int) -> None:
        await self.write_byte(CH_MASTER_VOLUME, clamp(raw, 0, MAX_VOLUME_RAW))

    async def get_volume_percent(self) -> int:
        return volume_raw_to_percent(await self.get_volume_raw())

    async def set_volume_percent(self, percent: int) -> None:
        await self.set_volume_raw(volume_percent_to_raw(clamp(percent, 0, 100)))

    async def get_mute(self) -> bool | None:
        b = await self.read_byte(CH_MUTE)
        return None if b is None else bool(b)

    async def set_mute(self, on: bool) -> None:
        await self.write_byte(CH_MUTE, 1 if on else 0)

    # --- input ---
    async def get_input(self) -> Input:
        b = await self.read_byte(CH_INPUT)
        return normalize_input(b if b is not None else 0)

    async def set_input(self, value: Input | str | int) -> None:
        selected = normalize_input(value)
        if selected is Input.OFF and not self.allow_power_off:
            raise PowerOffDisabledError(
                "input 'off' (value 0) is unreliable on some units; pass "
                "allow_power_off=True to enable it"
            )
        await self.write_byte(CH_INPUT, selected.value)

    # --- EQ: bass / mid / treble, level -10..+6 ---
    async def get_eq(self, channel: str) -> int | None:
        b = await self.read_byte(_eq_uuid(channel))
        return None if b is None else eq_byte_to_level(b)

    async def set_eq(self, channel: str, level: int) -> None:
        await self.write_byte(_eq_uuid(channel), eq_level_to_byte(level))

    # --- subwoofer ---
    async def get_sub_detected(self) -> bool | None:
        """Whether a subwoofer is physically connected (the app's "Subwoofer
        not detected"). Reads SubStatus and tests ``int(value) == 1``. ``None``
        if the characteristic is absent/unreadable."""
        return sub_detected_from_bytes(await self.read_raw(CH_SUBSTATUS))

    async def get_sub_level_raw(self) -> int | None:
        """Current sub level (0..31), read from the ChannelVolume characteristic
        (byte[4] = the subwoofer channel). ``None`` if the characteristic is
        absent or the response is too short."""
        data = await self.read_raw(CH_CHANNEL_VOLUME)
        if not data or len(data) <= SUB_LEVEL_BYTE_INDEX:
            return None
        return clamp(data[SUB_LEVEL_BYTE_INDEX], SUB_RAW_MIN, SUB_RAW_MAX)

    async def set_sub_level_raw(self, raw: int) -> None:
        """Set the sub level by raw step (0..31). Written as the 2-byte
        channel-volume command ``[0x04, raw]`` (subwoofer = channel 4)."""
        level = clamp(raw, SUB_RAW_MIN, SUB_RAW_MAX)
        await self.write_raw(CH_CHANNEL_VOLUME, bytes([SUB_CHANNEL, level]))

    async def get_sub_level_db(self) -> int | None:
        """Current sub level as the on-screen dB value (-21..+10), or ``None``."""
        raw = await self.get_sub_level_raw()
        return None if raw is None else sub_raw_to_db(raw)

    async def set_sub_level_db(self, db: int) -> None:
        """Set the sub level by dB (-21..+10, clamped); 0 dB is the default."""
        await self.set_sub_level_raw(sub_db_to_raw(clamp(db, SUB_DB_MIN, SUB_DB_MAX)))

    async def get_sub_invert(self) -> bool | None:
        """Subwoofer phase-invert state (the app's "Phase Invert")."""
        return await self.get_toggle(CH_SUBINVERT)

    async def set_sub_invert(self, on: bool) -> None:
        await self.set_toggle(CH_SUBINVERT, on)

    async def get_sub_mute(self) -> bool | None:
        return await self.get_toggle(CH_SUBMUTE)

    async def set_sub_mute(self, on: bool) -> None:
        await self.set_toggle(CH_SUBMUTE, on)

    # --- factory reset ---
    async def factory_reset(self) -> None:
        """Wipe ALL speaker settings (name, EQ, modes, pairing) and restart it.

        Irreversible: writes a single ``0x00`` to the FactoryReset characteristic
        (the app's ``factoryResetForFives``). The speaker reboots and drops the
        link afterwards, so callers should expect to reconnect / re-pair.
        """
        await self.write_raw(CH_FACTORY_RESET, b"\x00")

    # --- toggles & modes ---
    async def get_toggle(self, char_uuid: str) -> bool | None:
        b = await self.read_byte(char_uuid)
        return None if b is None else bool(b)

    async def set_toggle(self, char_uuid: str, on: bool) -> None:
        await self.write_byte(char_uuid, 1 if on else 0)

    async def set_vocal(self, index: int) -> None:
        await self.write_byte(CH_VOCAL, clamp(index, 0, 3))

    async def set_eqmode(self, index: int) -> None:
        await self.write_byte(CH_EQMODE, clamp(index, 0, 5))

    # --- transport ---
    async def play_pause(self) -> None:
        """Fire a single play/pause toggle command (stateless).

        We deliberately do NOT read CH_PLAYPAUSE first: on this hardware that
        read returns a stale value that doesn't track real playback, so a
        read-then-write-opposite scheme desyncs. The characteristic toggles
        playback on write, so one fixed write per press is the reliable command.
        """
        await self.write_byte(CH_PLAYPAUSE, 1)

    async def next_track(self) -> None:
        await self.write_byte(CH_NEXT, 0)

    async def prev_track(self) -> None:
        await self.write_byte(CH_PREV, 0)

    # --- name ---
    async def get_name(self) -> str | None:
        data = await self.read_raw(CH_NAME)
        if not data:
            return None
        return data.split(b"\x00")[0].decode("utf-8", "replace")

    async def set_name(self, name: str) -> None:
        await self.write_raw(CH_NAME, name.encode("utf-8"))

    # --- device info (standard DIS 0x180A, read-only) ---
    async def get_serial_number(self) -> str | None:
        """Unit serial number string (DIS 0x2A25), or ``None`` if absent."""
        return _decode_ascii(await self.read_raw(CH_SERIAL_NUMBER))

    async def get_firmware_revision(self) -> str | None:
        """Installed firmware version string (DIS 0x2A26), or ``None`` if absent."""
        return _decode_ascii(await self.read_raw(CH_FIRMWARE_REVISION))

    async def device_info(self) -> DeviceInfo:
        """Read the standard Device Information characteristics in one call."""
        serial = await self.get_serial_number()
        return DeviceInfo(
            model=self.model.value,
            name=await self.get_name(),
            manufacturer=_decode_ascii(await self.read_raw(CH_MANUFACTURER)),
            model_number=_decode_ascii(await self.read_raw(CH_MODEL_NUMBER)),
            serial_number=serial,
            mac_address=_serial_to_mac(serial),
            firmware_revision=await self.get_firmware_revision(),
            software_revision=_decode_ascii(await self.read_raw(CH_SOFTWARE_REVISION)),
            hardware_revision=_decode_ascii(await self.read_raw(CH_HW_REVISION)),
            system_id=_decode_system_id(await self.read_raw(CH_SYSTEM_ID)),
        )

    # --- aggregate ---
    async def status(self) -> KlipschStatus:
        raw = await self.get_volume_raw()
        selected = await self.get_input()
        return KlipschStatus(
            model=self.model.value,
            input=input_name(selected),
            input_value=selected.value,
            volume_raw=raw,
            volume_percent=volume_raw_to_percent(raw),
            volume_db=volume_raw_to_db(raw),
            mute=await self.get_mute(),
            bass=await self.get_eq("bass"),
            mid=await self.get_eq("mid"),
            treble=await self.get_eq("treble"),
            night=await self.get_toggle(CH_NIGHT),
            dynamic_bass=await self.get_toggle(CH_DYNBASS),
            sub_level_db=await self.get_sub_level_db(),
            sub_invert=await self.get_sub_invert(),
            sub_mute=await self.get_sub_mute(),
            sub_detected=await self.get_sub_detected(),
        )

def _bleak_factory(target: object, timeout: float) -> BleakLike:
    """Plain bleak factory (used on Linux/macOS, and as the Windows fallback)."""
    from bleak import BleakClient

    # bleak's BleakClient satisfies BleakLike at runtime; its type stubs are just
    # broader (BLEDevice | str target, char_specifier vs char), so bridge the seam.
    return cast(BleakLike, BleakClient(target, timeout=timeout))  # type: ignore[arg-type]


def _default_factory() -> ClientFactory:
    """Pick the fastest factory for the current OS.

    On Windows, bleak's ``connect()`` does a full GATT discovery (~6-10 s on The
    Fives). The WinRT backend fetches only what's needed (cached), so a cold
    connect drops to ~1.7 s and a warm one to near-instant. Everywhere else,
    plain bleak is the single cross-platform path.
    """
    import sys

    if sys.platform == "win32":
        try:
            from .winrt_backend import winrt_factory

            return winrt_factory
        except Exception:  # pragma: no cover - missing winrt projection
            return _bleak_factory
    return _bleak_factory


def _eq_uuid(channel: str) -> str:
    try:
        return EQ_CHANNELS[channel.lower()]
    except (KeyError, AttributeError) as exc:
        raise ValueError(
            f"unknown EQ channel {channel!r}; expected bass, mid or treble"
        ) from exc


def _decode_ascii(data: bytes | None) -> str | None:
    """Decode a DIS string characteristic (NUL-terminated ASCII), or ``None``."""
    if not data:
        return None
    text = data.split(b"\x00")[0].decode("ascii", "replace").strip()
    return text or None


def _decode_system_id(data: bytes | None) -> str | None:
    """Decode the DIS System ID (0x2A23) — 8 raw bytes — as a colon-hex string."""
    if not data:
        return None
    return ":".join(f"{b:02X}" for b in data)


def _serial_to_mac(serial: str | None) -> str | None:
    """The unit's serial number is its Bluetooth MAC; render it colon-separated
    (e.g. ``54B7E58D8F0B`` -> ``54:B7:E5:8D:8F:0B``), or ``None`` if it isn't a
    12 hex-digit address."""
    if not serial:
        return None
    s = serial.strip()
    if len(s) != 12 or any(c not in "0123456789abcdefABCDEF" for c in s):
        return None
    return ":".join(s[i:i + 2] for i in range(0, 12, 2)).upper()
