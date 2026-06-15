# Klipsch firmware-update protocol (reverse-engineered)

This documents the over-the-air firmware-update protocol used by the **"Klipsch
The Fives Updater"** Android app, reverse-engineered instruction-by-instruction
from the app's compiled code. It is recorded here as a reference; this library
does **not** implement flashing (see *Scope & safety* at the end).

The control protocol (volume / EQ / input over BLE) is unrelated and documented
in [constants.py](constants.py). Firmware update is a **separate** transport and
stack.

Further down this file also records the **anatomy of the firmware image** itself
(`QCC512X_dfu_file.bin`) and the **on-device GATT database** extracted from it and
verified against a live The Fives — see *Firmware image anatomy* and *On-device
GATT database* below.

---

## 1. Transport

Firmware update runs over **Bluetooth Classic RFCOMM (SPP)**, *not* BLE GATT.

- GAIA service UUID: `00001107-D102-11E1-9B23-00025B00A5A5`
- The host opens an RFCOMM channel to that service (channel resolved via SDP),
  then speaks the Qualcomm **GAIA v1/v2** framing with the **VM Upgrade
  Protocol (VMUP)** layered on top.
- Target SoC: **Qualcomm QCC512x** (the Fives' Bluetooth/audio chip).

Firmware image (public): a single `.bin` the device parses itself —
`https://pubklipschfirmwarefiles.s3.us-east-2.amazonaws.com/Fives/FivesUpdaterFiles/QCC512X_dfu_file.bin`

---

## 2. GAIA frame (the wire format)

Built by `RfcommFormatter.format`; parsed by `GaiaStreamAnalyser` / `Frame`.

```
offset  field
0       SOF            = 0xFF
1       VERSION        = 0x01
2       FLAGS          bit0 = CHECKSUM present, bit1 = LENGTH_EXTENSION
3       LENGTH         = len(payload)            (1 byte; 2 bytes if LENGTH_EXTENSION)
4..5    VENDOR_ID      = 0x000A (Qualcomm), big-endian
6..7    COMMAND_ID     big-endian; bit 0x8000 set = acknowledgement
8..     PAYLOAD        LENGTH bytes
[last]  CHECKSUM       XOR of all preceding bytes (only if FLAGS bit0)
```

`frame_length = LENGTH + 8 (+1 if checksum) (+1 if length-extension)`. With no
checksum and no length extension (the common case) the header is 8 bytes and the
content (`vendor + command + payload`) starts at offset 4. All multi-byte
integers across GAIA **and** VMUP are **big-endian**
(`Utils.extractIntFromByteArray(..., reverse=false)`).

### GAIA commands used for upgrade (vendor `0x000A`)

| Command | Value | Direction | Payload |
|---|---|---|---|
| `REGISTER_NOTIFICATION` | `0x4001` | host→dev | `[0x12]` (register for the upgrade event) |
| `VM_UPGRADE_CONNECT`    | `0x0640` | host→dev | *(none)* — enter upgrade mode |
| `VM_UPGRADE_CONTROL`    | `0x0642` | host→dev | one VMUP message (see §3) |
| `VM_UPGRADE_DISCONNECT` | `0x0641` | host→dev | *(none)* — leave upgrade mode |
| `EVENT_NOTIFICATION`    | `0x4003` | dev→host | `[0x12, <VMUP message>]` |

Every device→host notification (`0x4003`, event `0x12`) must be **acknowledged**:
host sends `command | 0x8000` (`0xC003`) with payload `[status]`, `status = 0x00`
= success. The device gates the data flow on these acks.

---

## 3. VMUP message

Carried inside `VM_UPGRADE_CONTROL` (host→dev) and inside the `0x4003`
notification payload after the `0x12` event byte (dev→host). Built/parsed by
`UpgradeMessage`.

```
offset  field
0       OPCODE   (1 byte)
1..2    LENGTH   (big-endian) = len(DATA)
3..     DATA     (LENGTH bytes)
```

### Opcodes (`OpCodes.getOpCode`)

| Opcode | Name | Dir | DATA |
|---|---|---|---|
| `0x01` | START_REQ | →dev | *(none)* |
| `0x02` | START_CFM | dev→ | `[status(1), batteryLevel(2)]`; status 0 = OK, 9 = battery-low (retry ≤5× @2000 ms) |
| `0x03` | DATA_BYTES_REQ | dev→ | `[numBytes(4), fileOffset(4)]` (big-endian) |
| `0x04` | DATA | →dev | `[isEndOfData(1), <fileBytes>]` |
| `0x07` | ABORT_REQ | →dev | *(none)* |
| `0x08` | ABORT_CFM | dev→ | *(none)* |
| `0x0B` | TRANSFER_COMPLETE_IND | dev→ | *(none)* |
| `0x0C` | TRANSFER_COMPLETE_RES | →dev | `[action]` — 0 = INTERACTIVE_COMMIT (reboot now), 2 = SILENT_COMMIT |
| `0x0E` | PROCEED_TO_COMMIT (IN_PROGRESS_RES) | →dev | `[action]` — 0 = CONFIRM, 1 = ABORT |
| `0x0F` | COMMIT_REQ | dev→ | *(none)* |
| `0x10` | COMMIT_CFM | →dev | `[action]` — 0 = CONFIRM (commit), 1 = ABORT |
| `0x11` | ERROR_WARN_IND | dev→ | `[errorCode(2)]` |
| `0x12` | COMPLETE_IND | dev→ | *(none)* — upgrade done |
| `0x13` | SYNC_REQ | →dev | `[identifier(4)]` = last 4 bytes of MD5(file) |
| `0x14` | SYNC_CFM | dev→ | `[resumePoint(1), identifier(4), protocolVersion(1)]`; abort if version > 4 |
| `0x15` | START_DATA_REQ | →dev | *(none)* |
| `0x16` | IS_VALIDATION_DONE_REQ | →dev | *(none)* |
| `0x17` | IS_VALIDATION_DONE_CFM | dev→ | *(empty)* → resend req now; `[delay(2)]` → resend after `delay` ms |
| `0x19` | VERSION_REQ | →dev | *(none)* — ask the device's upgrade-protocol version |
| `0x1A` | VERSION_CFM | dev→ | `[major(2), minor(2), configVersion(2)]` |
| `0x1B` | VARIANT_REQ | →dev | *(none)* |
| `0x1C` | VARIANT_CFM | dev→ | `[variant(8)]` — device variant string (ASCII) |
| `0x1F` | ERROR_WARN_RES | →dev | echoes the 2-byte error code from ERROR_WARN_IND |
| `0x20` | SILENT_COMMIT_SUPPORTED_REQ | →dev | *(none)* |
| `0x21` | SILENT_COMMIT_SUPPORTED_CFM | dev→ | `[isSupported(1)]` |
| `0x22` | SILENT_COMMIT_CFM | dev→ | *(none)* — device acknowledges a queued silent commit |

The full opcode set, the byte values, the GAIA command ids and the message layout
above were **re-verified against the standalone "Klipsch The Fives Updater" app**,
which embeds Qualcomm's canonical QTIL GAIA library (`OpCodes.getOpCode` /
`OpCodes.getString`, `UpgradeMessage`, `V1V2QTILPlugin`). `0x19`–`0x1C` (version /
variant negotiation) and `0x20`–`0x22` (silent-commit support) are **newer-protocol
opcodes** present in that library; the Fives speaks protocol ≤ 4 (the host aborts a
`SYNC_CFM` with `protocolVersion > 4`), so it may not exercise all of them. DATA
shapes for `0x1A`/`0x1C`/`0x21` follow the QTIL ADK definitions.

`ConfirmationOptions` values: `CONFIRM=0`, `ABORT=1`, `INTERACTIVE_COMMIT=0`,
`SILENT_COMMIT=2`, `CANCEL=0xFF`.

`ResumePoint` (SYNC_CFM byte 0; verified `ResumePoint` enum): `START=0`,
`PRE_VALIDATE=1`, `PRE_REBOOT=2`, `POST_REBOOT=3`, `COMMIT=4`, `POST_COMMIT=5`.

The two GAIA wrapper commands map to `V1V2QTILPlugin.sendPacket`:
`setUpgradeModeOn → 0x0640` (CONNECT), `sendUpgradeMessage → 0x0642` (CONTROL),
`setUpgradeModeOff → 0x0641` (DISCONNECT).

---

## 4. State machine (`UpgradeManagerImpl`)

The host streams the file **verbatim** — it never parses partition headers. The
device drives the data phase by requesting byte ranges; the host answers.

```
host → REGISTER_NOTIFICATION [0x12]
host → VM_UPGRADE_CONNECT
host → SYNC_REQ (md5 tail)
dev  → SYNC_CFM (resumePoint, protocolVersion)        # abort if version > 4
host → START_REQ
dev  → START_CFM (status)                             # 9 = battery-low → retry
       ├─ resumePoint START        → host → START_DATA_REQ
       ├─ resumePoint PRE_VALIDATE → host → IS_VALIDATION_DONE_REQ
       ├─ resumePoint PRE_REBOOT   → host → TRANSFER_COMPLETE_RES (interactive commit)
       ├─ resumePoint POST_REBOOT  → host → PROCEED_TO_COMMIT (confirm)
       └─ resumePoint COMMIT       → host → COMMIT_CFM (confirm)

# data phase (loops):
dev  → DATA_BYTES_REQ (numBytes, fileOffset)
host → DATA (isEndOfData, fileBytes[fileOffset : fileOffset+chunk])  × N
       # split into ≤ maxChunkSize chunks; isEndOfData=1 on the final byte
# when the last byte is sent:
host → IS_VALIDATION_DONE_REQ
dev  → IS_VALIDATION_DONE_CFM (delay?)                # poll until validated
dev  → TRANSFER_COMPLETE_IND
host → TRANSFER_COMPLETE_RES (interactive commit)     # device REBOOTS → link drops

# reconnect after reboot, then:
host → VM_UPGRADE_CONNECT ; SYNC_REQ
dev  → SYNC_CFM (resumePoint = POST_REBOOT)
host → START_REQ → dev START_CFM → host PROCEED_TO_COMMIT (confirm)
dev  → COMMIT_REQ
host → COMMIT_CFM (confirm)
dev  → COMPLETE_IND                                   # success
host → VM_UPGRADE_DISCONNECT
```

`ERROR_WARN_IND` → host echoes the 2-byte code back via `ERROR_WARN_RES`, then
decides: the `WARN_*` codes (`0x80`–`0x82`) and battery-low (`0x21`) are
**continuable**; every `ERROR_*` code is fatal → abort.

### Return / error codes (`UpgradeErrorFormatter.getReturnCodeLabel`)

The 2-byte `errorCode` carried by `ERROR_WARN_IND` (`0x11`). Verified against the
updater app; this is the standard QTIL upgrade error set.

| Code | Label | Continue? |
|---|---|---|
| `0x11` | ERROR_UNKNOWN_ID | abort |
| `0x13` | ERROR_WRONG_VARIANT | abort |
| `0x14` | ERROR_WRONG_PARTITION_NUMBER | abort |
| `0x15` | ERROR_PARTITION_SIZE_MISMATCH | abort |
| `0x16`–`0x19` | ERROR_PARTITION_* (open/close/type) | abort |
| `0x1A` | ERROR_SFS_VALIDATION_FAILED | abort |
| `0x1B` | ERROR_OEM_VALIDATION_FAILED | abort |
| `0x1C` | ERROR_UPGRADE_FAILED | abort |
| `0x1D` | ERROR_APP_NOT_READY | abort |
| `0x1E` | ERROR_LOADER_ERROR | abort |
| `0x1F` | ERROR_UNEXPECTED_LOADER_MSG | abort |
| `0x20` | ERROR_MISSING_LOADER | abort |
| **`0x21`** | **ERROR_BATTERY_LOW** | **continuable** (charge & retry) |
| `0x22` | ERROR_INVALID_SYNC_ID | abort |
| `0x23` | ERROR_IN_ERROR_STATE | abort |
| `0x24` | ERROR_NO_MEMORY | abort |
| `0x30`–`0x35` | ERROR_BAD_LENGTH_* | abort |
| `0x38`–`0x42` | ERROR_OEM_VALIDATION_* / ERROR_PARTITION_* | abort |
| `0x48` | ERROR_PARTITION_TYPE_NOT_MATCHING | abort |
| `0x49` | ERROR_PARTITION_TYPE_TWO_DFU | abort |
| `0x50` | ERROR_PARTITION_WRITE_FAILED_HEADER | abort |
| `0x51` | ERROR_PARTITION_WRITE_FAILED_DATA | abort |
| `0x58` | ERROR_FILE_TOO_SMALL | abort |
| `0x59` | ERROR_FILE_TOO_BIG | abort |
| `0x65`–`0x6B` | ERROR_INTERNAL_ERROR_* | abort |
| `0x70` | ERROR_SILENT_COMMIT_NOT_SUPPORTED | abort |
| `0x80` | WARN_APP_CONFIG_VERSION_INCOMPATIBLE | continuable |
| **`0x81`** | **WARN_SYNC_ID_IS_DIFFERENT** (resuming a *different* file) | **continuable** |
| `0x82` | WARN_SYNC_ID_IS_ZERO | continuable |

Chunking: `DataReader` returns `min(remainingBytes, maxChunkSize)` per DATA. To
keep the 1-byte GAIA LENGTH field valid, a DATA message's payload (= 4 VMUP/EOF
header bytes + chunk) must stay ≤ 255, so `maxChunkSize ≤ 251`.

---

## Scope & safety

This library implements the **read-only** side of what the updater app exposes —
the standard Device Information characteristics (firmware / serial / hardware &
software revision / manufacturer / model / system id), surfaced via
`KlipschClient.device_info()`. It deliberately does **not** implement flashing:
a botched write to the bootloader can brick the speaker, and the flow above is
untested against hardware here. This document exists so the protocol is not lost.

---

## Firmware image anatomy (`QCC512X_dfu_file.bin`)

The public Fives firmware (the S3 `.bin` linked in §1) is **not a flat image** — it
is a Qualcomm **ADK "upgrade file"** container. Loading it straight into a
disassembler yields noise; it must be unpacked first.

### Container format (`APPUHDR5`)

```
offset  field
0       MAGIC      = "APPUHDR5"
8       LENGTH     = header payload length (4 bytes, big-endian) = 0x24
12      PAYLOAD    = device id "QCC512Xx" + header/partition-count fields
…       records …
```

Then a sequence of `PARTDATA` records, ending with an `APPUPFTR` footer (the OEM
signature). Every multi-byte integer is **big-endian**.

```
PARTDATA record:
0   MAGIC        = "PARTDATA"
8   LENGTH       (4 bytes, big-endian)            # counts the partition-id + data
12  PARTITION_ID (4 bytes, big-endian)
16  DATA         (LENGTH - 4 bytes)
```

### Partitions (this image)

| part | size | gzip ratio | contents |
|---|---|---|---|
| 0 | 16 B | — | stamp / hash |
| 1 | 272 B | 0.61 | image/partition layout table (`Imag…`) |
| 4 | 10.9 KB | 0.68 | curator config filesystem (`iFel`, `.hcf`) |
| **5** | 537 KB | 0.64 | **P0 code image** — `aura_p0_d01_signed`, built 2019-08-05; RO-FS driver |
| 6 | 67 KB | 0.67 | read-only filesystem (`File…`) |
| 7 | 227 KB | 0.83 | packed assets / audio |
| **9** | 459 KB | 0.60 | **P1 application** — `QTIL ADK 2021-11-03`; the Klipsch BT/audio app |
| — | tail | — | `APPUPFTR` = OEM signature (ECDSA) |

The image is **signed but not encrypted** (whole-file gzip ratio ≈ 0.67; the
config filesystem and code partitions are in the clear).

### Processor / architecture

The chip is a Qualcomm **QCC512x** ("Aura" platform, QTIL/CSR ADK6). The apps code
(P0/P1) runs on a **Kalimba (KAL_ARCH4)** 32-bit, *word-addressed* DSP — not ARM.
Two consequences for any static analysis:

1. **A Kalimba-aware disassembler is required.** KAL_ARCH4 is not a common stock
   target; it needs a dedicated Kalimba processor definition. The partitions are
   not ARM/Cortex-M, so disassembling `part_5`/`part_9` as ARM is meaningless.
2. **Strings are byte-swapped within each 16-bit word.** e.g. "Klipsch Group, Inc"
   is stored as `lKpics hrGuo,pI cn`. To recover ASCII, swap every pair of bytes
   (`b[0::2], b[1::2] = b[1::2], b[0::2]`).

Even without a disassembler, string/byte recon yields a lot — including the GATT
database below.

---

## On-device GATT database (from the firmware, hardware-verified)

The binary GATT table lives in the **P1** partition (`part_9`), found by searching
the 16-bit-word-swapped image for the Klipsch base UUID
(`…-0D18-442C-BABE-F85B5BAA6F11`, stored little-endian) — 36 entries starting at
offset `0x66c98`. The list below was then **confirmed against a live The Fives**
with a read-only dump (`gatt_dump.py`: plain-bleak full discovery, read every
characteristic that advertises Read).

Handles are from *this* firmware (they can shift between builds — always address by
UUID). "live" = an example value read off one unit, not a protocol constant.

```
service da6d0fa1  VolumeService
  h0x29 da6d0fa2 MasterVolume      r/w/notify   live 0x1a (=26/36)
  h0x2c da6d0fa3 Mute              r/w/notify   live 0x00
  h0x2f da6d0fa4 ChannelVolume     r/w/notify   live 08 08 00 00 1b 00 (6 bytes; byte[4]=sub level)

service da6d0fe1  UIService
  h0x33 da6d0fe3 (UNDOCUMENTED)    write,notify          write-only
  h0x36 da6d0fe4 (UNDOCUMENTED)    read,notify           READ TIMES OUT → drops the BLE link
  h0x39 da6d0fe5 PowerMode         r/w/notify   live 0x01 (auto-standby on)
  h0x3c da6d0fe6 DeviceName        r/w/notify   live "deep-bass"
  h0x3f da6d0fe8 FactoryReset      r/w/notify   live 0x00

service da6d0f01  EqService
  h0x43 da6d0f02 Bass              r/w/notify   live 0x10 (level +6; byte = level+10)
  h0x46 da6d0f03 Mid               r/w/notify   live 0x00 (level -10)
  h0x49 da6d0f04 Treble            r/w/notify   live 0x00 (level -10)
  h0x4c da6d0f05 NightMode         r/w/notify   live 0x00
  h0x4f da6d0f08 BoundaryGain      r/w/notify   live 0x0a (OPEN)
  h0x52 da6d0f09 SubMute           r/w/notify   live 0x01
  h0x55 da6d0f13 SubStatus         read,notify  live 0x00 (no sub detected)
  h0x58 da6d0f14 DynamicBass       r/w/notify   live 0x01
  h0x5b da6d0f15 SubInvert         r/w/notify   live 0x01

service da6d0fd1  InputService
  h0x5f da6d0fd2 Input             r/w/notify   live 0x05 (USB)
  h0x62 da6d0fd3 (UNDOCUMENTED)    write,notify          write-only
  h0x65 da6d0fd4 (UNDOCUMENTED)    write,notify          write-only
  h0x68 da6d0fd5 (UNDOCUMENTED)    write,notify,indicate write-only

service da6d0fb1  AVTransportService
  h0x6c da6d0fb2 PlayPause         r/w/notify   live 0x00
  h0x6f da6d0fb3 Next              write,notify          write-only
  h0x72 da6d0fb4 Prev              write,notify          write-only

service da6d0ff1  (UNDOCUMENTED — looks like a wireless sub/surround status+version group)
  h0x76 da6d0ff2 (UNDOCUMENTED)    r/w,indicate  live 0x0000
  h0x79 da6d0ff3 (UNDOCUMENTED)    read,notify   live 0x0000
  h0x7c da6d0ff4 (UNDOCUMENTED)    read,notify   live 0x01
  h0x7f da6d0ff5 (UNDOCUMENTED)    r/w,indicate  live 0x0000
  h0x82 da6d0ff6 (UNDOCUMENTED)    read          READ TIMES OUT → drops the BLE link
  h0x84 da6d0ff7 (UNDOCUMENTED)    read          live "01.00.02" (ASCII version string)
```

### Notes — what's NOT present, and what's not safe

- **`DA6D0FE7` PowerToggle and `DA6D0FE2` LEDMode are absent** from this firmware's
  GATT DB. That is why writing PowerToggle does nothing on this unit (the
  characteristic does not exist) — corroborating the app-side gate
  (`Constants.isPowerToggleFeatureSupport`, which needs MCU+BLE firmware above a
  high threshold; this image's BLE side is below it). LEDMode is Cinema-600-only.
- Also absent here: `06` VocalMode, `12` EQMode, `EB` FunctionSounds (this specific
  image may differ from a given unit's build — the live unit is ground truth).
- **The undocumented characteristics are not usable as reliable features:**
  - `e4` (h0x36) and `f6` (h0x82) **time out on read and drop the BLE link** — a
    naive "read everything" sweep is actively harmful; never touch them.
  - `d3/d4/d5` (Input svc) and `e3` (UI svc) are **write-only command channels** of
    unknown effect — never write blind.
  - the `F1` service (`f2`–`f7`) is **read-only status/version**, most likely the
    wireless sub/surround subsystem: `f7` = version string "01.00.02", `f4` = 1,
    `f2/f3/f5` = `0x0000` (zero because no sub/surround is attached — SubStatus is 0
    too). No new *control* surface, only diagnostics.
- **Conclusion:** beyond what `klipsch_ble` already exposes there is nothing
  reliably controllable on this hardware. The only safe new datum is the read-only
  `f7` accessory/version string.

The read-only dump tool used for this verification is kept out-of-tree (it writes
nothing to the speaker); the connection reuses `KlipschClient` but forces the plain
bleak backend so arbitrary UUIDs are discoverable and readable.
