# Klipsch firmware-update protocol (reverse-engineered)

This documents the over-the-air firmware-update protocol used by the **"Klipsch
The Fives Updater"** Android app, reverse-engineered instruction-by-instruction
from the app's compiled code. It is recorded here as a reference; this library
does **not** implement flashing (see *Scope & safety* at the end).

The control protocol (volume / EQ / input over BLE) is unrelated and documented
in [constants.py](constants.py). Firmware update is a **separate** transport and
stack.

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
| `0x1F` | ERROR_WARN_RES | →dev | echoes the 2-byte error code from ERROR_WARN_IND |

`ConfirmationOptions` values: `CONFIRM=0`, `ABORT=1`, `INTERACTIVE_COMMIT=0`,
`SILENT_COMMIT=2`, `CANCEL=0xFF`.

`ResumePoint` (SYNC_CFM byte 0): `START=0`, `PRE_VALIDATE=1`, `PRE_REBOOT=2`,
`POST_REBOOT=3`, `COMMIT=4`, `POST_COMMIT=5`.

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

`ERROR_WARN_IND` → host echoes it back via `ERROR_WARN_RES`, then: return code
`0x21` = battery-low (continuable), `0x81` = "file is different" warning
(continuable); anything else is fatal → abort.

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
