"""Cross-platform discovery of Klipsch powered speakers via BLE scan.

Works on every bleak backend. Note that on macOS the returned ``address`` is a
CoreBluetooth UUID, not a MAC, and that a speaker currently connected as audio
may not advertise — in that case discovery returns nothing even though direct
connection by a known address still works.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import KlipschModel, model_from_name


@dataclass(frozen=True)
class Discovered:
    address: str
    name: str
    model: KlipschModel = KlipschModel.UNKNOWN


# Vendor-unique suffix shared by every custom Klipsch 128-bit GATT service UUID
# (da6d0f??-0d18-442c-babe-f85b5baa6f11). A speaker that has been renamed no
# longer advertises a name containing "klipsch", but it still advertises these
# service UUIDs, so matching on the suffix identifies it regardless of name.
_KLIPSCH_UUID_SUFFIX = "-442c-babe-f85b5baa6f11"


async def discover(name_contains: str = "klipsch", timeout: float = 10.0) -> list[Discovered]:
    """Scan for advertising Klipsch speakers.

    A device is taken to be a Klipsch if it advertises one of the vendor's
    custom GATT services *or* its advertised name contains ``name_contains``.
    The service match makes discovery survive the speaker being renamed.
    """
    from bleak import BleakScanner

    needle = name_contains.lower()
    found: dict[str, Discovered] = {}
    # return_adv gives us the advertisement (local name + service UUIDs), which
    # is richer and more reliable than the cached BLEDevice.name alone.
    devices = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for dev, adv in devices.values():
        name = adv.local_name or dev.name or ""
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        is_klipsch = (needle in name.lower()) or any(
            _KLIPSCH_UUID_SUFFIX in u for u in uuids)
        if is_klipsch:
            found[dev.address] = Discovered(
                address=dev.address, name=name, model=model_from_name(name))
    return list(found.values())


async def find_address(name_contains: str = "klipsch", timeout: float = 10.0) -> str | None:
    """Return the address of the first matching speaker, or ``None``."""
    hits = await discover(name_contains, timeout)
    return hits[0].address if hits else None
