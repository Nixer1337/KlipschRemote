# klipsch-ble

Cross-platform async Python library for controlling **Klipsch powered speakers**
— **The Fives**, **The Sevens** and **The Nines** (including the McLaren
editions) — over Bluetooth LE. One code path for Windows, Linux and macOS: it
talks to the speaker's custom Klipsch GATT service through
[`bleak`](https://github.com/hbldh/bleak), which selects the right OS backend
(WinRT / BlueZ / CoreBluetooth) automatically.

The whole line speaks **one identical control protocol** — a shared GATT table
and shared volume / input / EQ encodings — so the same client drives every model.
It auto-detects which one it's talking to and exposes a friendly model name and a
capability check.

On **Windows** the client uses a small WinRT fast-path backend by default
instead of going through bleak's `connect()`. bleak does a full GATT discovery
(every service/characteristic/descriptor) on connect, which costs ~6-10 s; the
WinRT backend fetches only what it needs, cached, dropping a cold connect to
~2 s and a warm reconnect to near-instant. The backend is hidden behind the same
`BleakLike` seam, so the API is identical on every OS. Pass your own
`client_factory=` to `KlipschClient` to override it.

This is a self-contained package of plain scripts — no build step, no install.
Drop it on your import path and use the async API directly, or run the bundled
CLI with `python -m klipsch_ble`.

## Supported models

Detection uses the **standard BLE Device Information Service** — the Model Number
string (`0x2A24`), refined by the Hardware Revision (`0x2A27`). These are stable
product ids reported by the firmware, so detection survives the user **renaming**
the speaker (unlike the Klipsch name characteristic).

| Model | DIS Model Number | Hardware Revision |
|---|---|---|
| The Fives | `1067563` | 1 (V1), 2 (V2) |
| The Fives McLaren | `1067563` | 3 |
| The Sevens | `1071199` | 4 |
| The Nines | `1071200` | 5 |
| The Nines McLaren | `1071482` | 8 |

Out of scope (different input maps / a separate portable line): One Plus,
Three Plus, and the Cinema soundbars.

## Requirements

- Python 3.9+
- [`bleak`](https://github.com/hbldh/bleak) — `pip install bleak` (the only
  runtime dependency)
- On Windows, the WinRT fast-path backend needs the `winrt` projection
  (`pip install winrt-Windows.Devices.Bluetooth`); without it the client falls
  back to plain bleak automatically.
- The speaker paired with the OS as a Bluetooth **audio** device. A dual-mode
  unit derives its LE key from that Classic bond (CTKD), so a working audio
  pairing is what makes GATT reachable. **Never** pair/unpair it as an LE-only
  device — that breaks control.

## Use it

No packaging yet. Put the `klipsch_ble/` folder on your import path — e.g. run
your code from this folder's **parent** directory — then import the package:

```python
from klipsch_ble import KlipschClient
```

…or run the bundled CLI without installing anything:

```sh
python -m klipsch_ble            # interactive REPL
python -m klipsch_ble status     # one-shot
```

## Python API (async)

```python
import asyncio
from klipsch_ble import KlipschClient

async def main():
    async with KlipschClient("AA:BB:CC:DD:EE:FF") as spk:
        print(spk.model.display_name)        # e.g. "The Fives" (auto-detected)
        print((await spk.status()).as_dict())
        await spk.set_input("optical")
        await spk.set_volume_percent(40)
        await spk.set_eq("bass", +3)
        await spk.set_mute(True)

asyncio.run(main())
```

### Model detection & capabilities

The model is auto-detected on `connect()` from the DIS characteristics and cached
on `spk.model` (a `KlipschModel`). You can pin it explicitly to skip detection,
or query capabilities:

```python
from klipsch_ble import KlipschClient, KlipschModel

# Pin the model (skips the DIS reads):
spk = KlipschClient("54:B7:...", model=KlipschModel.SEVENS)

# Or detect, then branch on capabilities:
async with KlipschClient("54:B7:...") as spk:
    if spk.supports("phono"):
        await spk.set_input("phono")
```

The line is protocol-identical, so an `UNKNOWN` model optimistically reports the
full control set. `status().model` carries the detected model name.

### Device info (read-only)

The standard Device Information Service (`0x180A`) also reports the installed
firmware version and the unit serial number. `device_info()` reads them in one
call (every field is best-effort — `None` if the speaker omits it):

```python
async with KlipschClient("54:B7:...") as spk:
    di = await spk.device_info()
    print(di.firmware_revision, di.serial_number)
    # also: di.manufacturer, di.model_number, di.software_revision,
    #       di.hardware_revision, di.system_id
```

These are the same standard DIS characteristics the Klipsch firmware-updater app
reads. That app's *firmware-update* path (Qualcomm GAIA + VM Upgrade over
Bluetooth Classic RFCOMM) is reverse-engineered and documented in
[FIRMWARE_PROTOCOL.md](FIRMWARE_PROTOCOL.md); this library implements only the
read-only side, not flashing.

### Discovery

```python
from klipsch_ble import find_address, discover
address = await find_address()      # first advertising "Klipsch ..." speaker
hits = await discover()             # all of them; each hit has .address/.name/.model
```

On **macOS** the discovered address is a CoreBluetooth UUID, not a MAC. Also, a
speaker that is currently connected as audio does not advertise BLE, so it may
not show up in a scan even though connecting by a known address still works.

## CLI

The package ships an optional command-line front-end, run as
`python -m klipsch_ble`. It sits right next to the library and adds no
dependency of its own — the API works without it. The examples below abbreviate
`python -m klipsch_ble` as `klipsch` (e.g. via a shell alias).

```sh
klipsch                         # interactive REPL
klipsch status                  # full status (incl. model), then exit
klipsch info                    # device info: firmware / serial / hw rev
klipsch discover                # scan for advertising speakers
klipsch --address AA:BB:CC:DD:EE:FF status
klipsch set 50%                 # volume to 50%
klipsch up 2                    # +2 steps
klipsch in optical              # switch input
klipsch bass +3                 # raise bass
```

**Address resolution** searches your **paired** devices first:

1. `--address` always wins.
2. Otherwise the OS-paired Klipsch speakers are enumerated (Windows: PnP devices;
   Linux: `bluetoothctl devices Paired`):
   - **exactly one** paired → connect to it automatically;
   - **several** paired → list them and ask which one (Enter picks the last-used
     one); non-interactive with no saved choice → error asking for `--address`;
   - **none** enumerated (e.g. macOS) → fall back to a saved address, then a BLE
     air scan.
3. The chosen address is cached in `~/.klipsch.json`.

REPL commands: `?` for help; volume `N` / `N%` / `+`/`-`/`+N`/`-N` / `m`;
`in <src>`; `bass|mid|treble <+-N|N>`; `night|dynbass|submute on|off`;
`vocal 0..3`; `eq 0..5`; `play|next|prev`; `name <..>`;
`s` (status), `r` (volume), `q` (quit).

## Inputs

`off` · `tv` (hdmi/arc) · `bluetooth` (bt) · `optical` (opt) · `aux`
(analog/minijack) · `usb` · `phono` (line/rca) — or the raw byte `0..6`.

`set_input("off")` (value `0`) is unreliable on some units and is disabled
unless you pass `allow_power_off=True` to `KlipschClient`.

## Protocol notes

- Custom Klipsch GATT, base UUID suffix `-0D18-442C-BABE-F85B5BAA6F11`. The same
  30-characteristic table is used by Fives / Sevens / Nines.
- Master volume `DA6D0FA2` — 1 byte `0..0x24` (0..36).
- Input `DA6D0FD2` — `1=TV/ARC 2=BT 3=Optical 4=Analog 5=USB 6=Phono` (`0=off`).
- EQ bass/mid/treble `DA6D0F02/03/04` — byte = level + 10, level `-10..+6`.
- Percent uses integer truncation: `raw = pct*0x24//100`, `pct = raw*100//0x24`.
- Model id via standard DIS: Model Number `0x2A24`, Hardware Revision `0x2A27`.

## License

Apache License 2.0. Unofficial project, not affiliated with Klipsch Group, Inc.;
trademarks belong to their owners. Builds on the MIT-licensed
[`fives-api`](https://github.com/ssalaues/fives-api). See [`LICENSE`](../LICENSE),
[`NOTICE`](../NOTICE) and the [project README](../README.md#-license--legal).
