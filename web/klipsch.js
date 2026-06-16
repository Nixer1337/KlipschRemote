/*
 * klipsch.js — Web Bluetooth port of the klipsch_ble control library.
 *
 * The whole "CinemaStream" powered line (The Fives, The Sevens, The Nines incl.
 * McLaren) speaks one identical BLE GATT control protocol: plain read/write of
 * a handful of bytes to fixed `da6d0f..` characteristics. That maps 1:1 onto the
 * Web Bluetooth API, so this file mirrors klipsch_ble/constants.py +
 * klipsch_ble/client.py in the browser. There is intentionally NO firmware
 * update here — that runs over Bluetooth Classic RFCOMM, which browsers cannot do
 * (and the Python library doesn't implement it either).
 *
 * Bonding note (same as the desktop app): control characteristics need an
 * encrypted link whose LE key is derived from the Classic *audio* bond (CTKD).
 * Pair the speaker with the OS as an AUDIO device first; never as an LE-only
 * device. Web Bluetooth goes through the same OS BLE stack as bleak/WinRT, so it
 * reuses that bond.
 *
 * Exposes everything under the global `Klipsch` object (no build step / modules
 * needed — just include this file before app.js).
 */
(function (global) {
  "use strict";

  // ---- GATT UUIDs ----------------------------------------------------------
  // Web Bluetooth requires full, lowercase 128-bit UUIDs for vendor services.
  const SFX = "0d18-442c-babe-f85b5baa6f11";
  const u = (short) => `da6d0f${short}-${SFX}`;

  // Services
  const SVC_VOLUME = u("a1");
  const SVC_EQ = u("01");
  const SVC_INPUT = u("d1");
  const SVC_UI = u("e1");
  const SVC_AVT = u("b1");

  // Volume service
  const CH_MASTER_VOLUME = u("a2"); // 1 byte, 0..0x24
  const CH_MUTE = u("a3"); // 1 byte, 0/1
  const CH_CHANNEL_VOLUME = u("a4"); // 2 bytes [channel, level]; sub = [0x04, raw]

  // EQ service (level -10..+6 => byte 0..16, flat = 10)
  const CH_BASS = u("02");
  const CH_MID = u("03");
  const CH_TREBLE = u("04");
  const CH_NIGHT = u("05"); // 0/1
  const CH_VOCAL = u("06"); // 0..3
  const CH_SUBMUTE = u("09"); // 0/1
  const CH_EQMODE = u("12"); // preset 0..5
  const CH_SUBSTATUS = u("13"); // read-only; int(value) == 1 => sub detected
  const CH_DYNBASS = u("14"); // 0/1
  const CH_SUBINVERT = u("15"); // 0/1 — subwoofer "Phase Invert"
  const CH_BOUNDARY_GAIN = u("08"); // speaker placement: 1 byte (4=corner/7=wall/10=open)

  // Input service
  const CH_INPUT = u("d2"); // byte 0..6

  // UI service
  const CH_POWERMODE = u("e5"); // 0/1 — auto-standby
  const CH_NAME = u("e6"); // UTF-8 string
  const CH_FACTORY_RESET = u("e8"); // write 0x00 to wipe all settings
  const CH_FUNCSOUNDS = u("eb"); // 0/1

  // AV transport service
  const CH_PLAYPAUSE = u("b2"); // toggle, 1=play / 0=pause
  const CH_NEXT = u("b3"); // write 0 to trigger
  const CH_PREV = u("b4"); // write 0 to trigger

  // Standard Device Information Service (0x180A) — model id + read-only info.
  const SVC_DIS = "0000180a-0000-1000-8000-00805f9b34fb";
  const CH_SYSTEM_ID = "00002a23-0000-1000-8000-00805f9b34fb";
  const CH_MODEL_NUMBER = "00002a24-0000-1000-8000-00805f9b34fb";
  const CH_SERIAL_NUMBER = "00002a25-0000-1000-8000-00805f9b34fb"; // BLOCKED by Web Bluetooth
  const CH_FIRMWARE_REVISION = "00002a26-0000-1000-8000-00805f9b34fb";
  const CH_HW_REVISION = "00002a27-0000-1000-8000-00805f9b34fb";
  const CH_SOFTWARE_REVISION = "00002a28-0000-1000-8000-00805f9b34fb";
  const CH_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb";

  // Which service each characteristic lives under — lets us resolve one service +
  // one characteristic on demand (cached) instead of enumerating the whole DB.
  const CHAR_TO_SERVICE = {
    [CH_MASTER_VOLUME]: SVC_VOLUME,
    [CH_MUTE]: SVC_VOLUME,
    [CH_CHANNEL_VOLUME]: SVC_VOLUME,
    [CH_BASS]: SVC_EQ,
    [CH_MID]: SVC_EQ,
    [CH_TREBLE]: SVC_EQ,
    [CH_NIGHT]: SVC_EQ,
    [CH_VOCAL]: SVC_EQ,
    [CH_SUBMUTE]: SVC_EQ,
    [CH_EQMODE]: SVC_EQ,
    [CH_SUBSTATUS]: SVC_EQ,
    [CH_DYNBASS]: SVC_EQ,
    [CH_SUBINVERT]: SVC_EQ,
    [CH_BOUNDARY_GAIN]: SVC_EQ,
    [CH_INPUT]: SVC_INPUT,
    [CH_POWERMODE]: SVC_UI,
    [CH_NAME]: SVC_UI,
    [CH_FACTORY_RESET]: SVC_UI,
    [CH_FUNCSOUNDS]: SVC_UI,
    [CH_PLAYPAUSE]: SVC_AVT,
    [CH_NEXT]: SVC_AVT,
    [CH_PREV]: SVC_AVT,
    [CH_SYSTEM_ID]: SVC_DIS,
    [CH_MODEL_NUMBER]: SVC_DIS,
    [CH_SERIAL_NUMBER]: SVC_DIS,
    [CH_FIRMWARE_REVISION]: SVC_DIS,
    [CH_HW_REVISION]: SVC_DIS,
    [CH_SOFTWARE_REVISION]: SVC_DIS,
    [CH_MANUFACTURER]: SVC_DIS,
  };

  // All services we may touch — must be declared up front for requestDevice().
  const ALL_SERVICES = [
    SVC_VOLUME, SVC_EQ, SVC_INPUT, SVC_UI, SVC_AVT, SVC_DIS,
  ];

  // ---- value conversions (mirror constants.py) -----------------------------
  const MAX_VOLUME_RAW = 0x24; // 36 steps

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, Math.trunc(v)));

  const volumePercentToRaw = (p) => Math.trunc((clamp(p, 0, 100) * MAX_VOLUME_RAW) / 100);
  const volumeRawToPercent = (r) => Math.trunc((clamp(r, 0, MAX_VOLUME_RAW) * 100) / MAX_VOLUME_RAW);
  const volumeRawToDb = (r) => -80 + Math.round(clamp(r, 0, MAX_VOLUME_RAW) * (88 / MAX_VOLUME_RAW));

  const EQ_MIN = -10, EQ_MAX = 6, EQ_OFFSET = 10;
  const eqLevelToByte = (lvl) => clamp(lvl, EQ_MIN, EQ_MAX) + EQ_OFFSET;
  const eqByteToLevel = (b) => clamp(b - EQ_OFFSET, EQ_MIN, EQ_MAX);
  const EQ_CHANNELS = { bass: CH_BASS, mid: CH_MID, treble: CH_TREBLE };

  // Subwoofer: written as 2-byte channel-volume [0x04, raw]; raw 0..31; db=raw-21.
  const SUB_CHANNEL = 0x04;
  const SUB_RAW_MIN = 0, SUB_RAW_MAX = 31, SUB_DB_OFFSET = 21;
  const SUB_DB_MIN = SUB_RAW_MIN - SUB_DB_OFFSET; // -21
  const SUB_DB_MAX = SUB_RAW_MAX - SUB_DB_OFFSET; // +10
  const SUB_LEVEL_BYTE_INDEX = 4;
  const subRawToDb = (r) => clamp(r, SUB_RAW_MIN, SUB_RAW_MAX) - SUB_DB_OFFSET;
  const subDbToRaw = (d) => clamp(d + SUB_DB_OFFSET, SUB_RAW_MIN, SUB_RAW_MAX);

  // ---- inputs --------------------------------------------------------------
  const Input = { OFF: 0, TV: 1, BLUETOOTH: 2, OPTICAL: 3, AUX: 4, USB: 5, PHONO: 6 };
  const INPUT_NAMES = {
    0: "off", 1: "tv", 2: "bluetooth", 3: "optical", 4: "aux", 5: "usb", 6: "phono",
  };
  const INPUT_ALIASES = {
    off: 0, tv: 1, hdmi: 1, arc: 1, bt: 2, bluetooth: 2, optical: 3, opt: 3,
    aux: 4, analog: 4, minijack: 4, usb: 5, phono: 6, line: 6, rca: 6,
  };
  function normalizeInput(value) {
    if (typeof value === "number") return clamp(value, 0, 6);
    const key = String(value).trim().toLowerCase();
    if (/^\d+$/.test(key)) return clamp(parseInt(key, 10), 0, 6);
    if (key in INPUT_ALIASES) return INPUT_ALIASES[key];
    throw new Error(`unknown input ${value}`);
  }
  const inputName = (v) => INPUT_NAMES[normalizeInput(v)];

  // ---- speaker placement / boundary gain (mirror constants.py) -------------
  // 1 byte written to CH_BOUNDARY_GAIN; the byte IS the low-frequency gain, so a
  // free-standing speaker boosts bass most and a corner (which already reinforces
  // bass) least. An unrecognised read defaults to WALL.
  const Placement = { CORNER: 4, WALL: 7, OPEN: 10 };
  const PLACEMENT_DEFAULT = 7;
  const PLACEMENT_NAMES = { 4: "corner", 7: "wall", 10: "open" };
  const PLACEMENT_ALIASES = {
    corner: 4, wall: 7, on_wall: 7,
    open: 10, free: 10, freestanding: 10, table: 10, tabletop: 10, other: 10, others: 10,
  };
  function normalizePlacement(value) {
    if (typeof value === "number") {
      if (PLACEMENT_NAMES[value]) return value;
      throw new Error(`unknown placement value ${value}`);
    }
    const key = String(value).trim().toLowerCase();
    if (/^\d+$/.test(key)) return normalizePlacement(parseInt(key, 10));
    if (key in PLACEMENT_ALIASES) return PLACEMENT_ALIASES[key];
    throw new Error(`unknown placement ${value}`);
  }
  const placementName = (v) => PLACEMENT_NAMES[normalizePlacement(v)];
  // Decode CH_BOUNDARY_GAIN: only 4/7/10 are valid; anything else (or absent) -> WALL.
  const placementFromByte = (b) => (b != null && PLACEMENT_NAMES[b] ? b : PLACEMENT_DEFAULT);

  // ---- model identification (mirror models.py) -----------------------------
  // DIS Model Number (0x2A24) -> model; the Fives share 1067563 and are split by
  // Hardware Revision (0x2A27): V1=1, V2=2, McLaren=3.
  const MODELS = {
    fives: { display: "The Fives" },
    fives_mclaren: { display: "The Fives McLaren" },
    sevens: { display: "The Sevens" },
    nines: { display: "The Nines" },
    nines_mclaren: { display: "The Nines McLaren" },
    unknown: { display: "Klipsch speaker" },
  };
  const MODEL_BY_NUMBER = { "1067563": "fives", "1071199": "sevens", "1071200": "nines", "1071482": "nines_mclaren" };
  const MODEL_BY_HW_REV = { 1: "fives", 2: "fives", 3: "fives_mclaren", 4: "sevens", 5: "nines", 8: "nines_mclaren" };

  function modelFromName(name) {
    if (!name) return "unknown";
    const low = name.toLowerCase();
    if (low.includes("nines") || low.includes("the nine"))
      return low.includes("mclaren") ? "nines_mclaren" : "nines";
    if (low.includes("sevens") || low.includes("the seven")) return "sevens";
    if (low.includes("fives") || low.includes("the five"))
      return low.includes("mclaren") ? "fives_mclaren" : "fives";
    return "unknown";
  }
  function resolveModel(modelNumber, hwRevision, name) {
    const byNumber = modelNumber ? (MODEL_BY_NUMBER[modelNumber.trim()] || "unknown") : "unknown";
    const rev = hwRevision != null && /^\d+$/.test(String(hwRevision).trim())
      ? MODEL_BY_HW_REV[parseInt(String(hwRevision).trim(), 10)] : undefined;
    if (byNumber === "fives") {
      // refine the shared Fives id with the hardware revision
      if (rev === "fives" || rev === "fives_mclaren") return rev;
      return byNumber;
    }
    if (byNumber !== "unknown") return byNumber;
    if (rev) return rev;
    return modelFromName(name);
  }

  // ---- string decoders -----------------------------------------------------
  function decodeAscii(bytes) {
    if (!bytes || !bytes.length) return null;
    let end = bytes.indexOf(0); // NUL-terminated
    if (end < 0) end = bytes.length;
    const text = new TextDecoder("ascii").decode(bytes.subarray(0, end)).trim();
    return text || null;
  }
  function decodeUtf8(bytes) {
    if (!bytes || !bytes.length) return null;
    let end = bytes.indexOf(0);
    if (end < 0) end = bytes.length;
    return new TextDecoder("utf-8").decode(bytes.subarray(0, end));
  }
  function decodeSystemId(bytes) {
    if (!bytes || !bytes.length) return null;
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0").toUpperCase()).join(":");
  }
  // The unit's serial is its Bluetooth MAC; render it colon-separated
  // ("54B7E58D8F0B" -> "54:B7:E5:8D:8F:0B"). Mirrors _serial_to_mac in client.py.
  // Always null on web (the serial itself is unreadable), but kept for shape parity.
  function serialToMac(serial) {
    if (!serial || !/^[0-9a-fA-F]{12}$/.test(serial.trim())) return null;
    return (serial.trim().match(/../g) || []).join(":").toUpperCase();
  }
  function subDetectedFromBytes(bytes) {
    if (!bytes || !bytes.length) return null;
    // big-endian int == 1 => detected
    let n = 0;
    for (const b of bytes) n = n * 256 + b;
    return n === 1;
  }

  // ---- client --------------------------------------------------------------
  class KlipschClient {
    constructor(device, { allowPowerOff = false } = {}) {
      this.device = device;
      this.allowPowerOff = allowPowerOff;
      this.model = "unknown";
      this._server = null;
      this._services = new Map(); // uuid -> BluetoothRemoteGATTService
      this._chars = new Map(); // uuid -> BluetoothRemoteGATTCharacteristic
    }

    /** Open a chooser and connect. `onDisconnect` fires on link loss. */
    static async request({ allowPowerOff = false } = {}) {
      if (!navigator.bluetooth) {
        throw new Error("Web Bluetooth is not available in this browser. Use Chrome, Edge or Opera.");
      }
      // The speaker may not advertise BLE while audio-connected, so accept all
      // devices and let the user pick; we still declare every service we touch.
      const device = await navigator.bluetooth.requestDevice({
        acceptAllDevices: true,
        optionalServices: ALL_SERVICES,
      });
      return new KlipschClient(device, { allowPowerOff });
    }

    get connected() {
      return !!(this._server && this._server.connected);
    }

    onDisconnect(handler) {
      this.device.addEventListener("gattserverdisconnected", handler);
    }

    async connect() {
      this._server = await this.device.gatt.connect();
      this._services.clear();
      this._chars.clear();
      // One cheap read to surface an unencrypted/unbonded link early. This goes
      // straight to the characteristic (not readRaw, which swallows errors) so a
      // genuine access failure raises the helpful message below.
      try {
        const c = await this._char(CH_MASTER_VOLUME);
        await c.readValue();
      } catch (e) {
        this.disconnect();
        throw new Error(
          "Control characteristics are unreachable — no working bond. Add the " +
          "speaker to the OS as an AUDIO device (not LE-only), then retry."
        );
      }
      await this.detectModel();
      return this;
    }

    disconnect() {
      if (this.device.gatt && this.device.gatt.connected) this.device.gatt.disconnect();
      this._server = null;
      this._services.clear();
      this._chars.clear();
    }

    // --- targeted, cached service/char lookup ---
    async _service(serviceUuid) {
      let svc = this._services.get(serviceUuid);
      if (!svc) {
        svc = await this._server.getPrimaryService(serviceUuid);
        this._services.set(serviceUuid, svc);
      }
      return svc;
    }
    async _char(charUuid) {
      let c = this._chars.get(charUuid);
      if (!c) {
        const serviceUuid = CHAR_TO_SERVICE[charUuid];
        if (!serviceUuid) throw new Error(`no known service for ${charUuid}`);
        const svc = await this._service(serviceUuid);
        c = await svc.getCharacteristic(charUuid);
        this._chars.set(charUuid, c);
      }
      return c;
    }

    // --- raw I/O by UUID ---
    /** Returns a Uint8Array, or null if the characteristic is absent/unreadable. */
    async readRaw(charUuid) {
      try {
        const c = await this._char(charUuid);
        const dv = await c.readValue();
        return new Uint8Array(dv.buffer, dv.byteOffset, dv.byteLength);
      } catch (e) {
        return null;
      }
    }
    async writeRaw(charUuid, bytes) {
      const c = await this._char(charUuid);
      const data = bytes instanceof Uint8Array ? bytes : Uint8Array.from(bytes);
      // The protocol always uses write-with-response.
      if (c.writeValueWithResponse) await c.writeValueWithResponse(data);
      else await c.writeValue(data);
    }
    async readByte(charUuid) {
      const data = await this.readRaw(charUuid);
      return data && data.length ? data[0] : null;
    }
    writeByte(charUuid, value) {
      return this.writeRaw(charUuid, [value & 0xff]);
    }

    // --- volume / mute ---
    async getVolumeRaw() { return clamp((await this.readByte(CH_MASTER_VOLUME)) || 0, 0, MAX_VOLUME_RAW); }
    setVolumeRaw(raw) { return this.writeByte(CH_MASTER_VOLUME, clamp(raw, 0, MAX_VOLUME_RAW)); }
    async getVolumePercent() { return volumeRawToPercent(await this.getVolumeRaw()); }
    setVolumePercent(p) { return this.setVolumeRaw(volumePercentToRaw(p)); }
    async getMute() { const b = await this.readByte(CH_MUTE); return b == null ? null : !!b; }
    setMute(on) { return this.writeByte(CH_MUTE, on ? 1 : 0); }

    // --- live notifications ---
    // Master volume is the ONLY thing the speaker pushes — the physical knob is
    // its sole on-device control; input / EQ / subwoofer / etc. all change
    // silently. So we subscribe to volume alone (the slider then follows the
    // knob) and read everything else once at connect. Best-effort: if
    // notifications can't start, the remote still works, just without live volume.
    async subscribeVolume(onChange) {
      try {
        const c = await this._char(CH_MASTER_VOLUME);
        c.addEventListener("characteristicvaluechanged", (e) => {
          const dv = e.target.value;
          if (dv && dv.byteLength) onChange(clamp(dv.getUint8(0), 0, MAX_VOLUME_RAW));
        });
        await c.startNotifications();
      } catch (e) {
        /* notifications are a nicety — never block the remote */
      }
    }

    // --- input ---
    async getInput() { const b = await this.readByte(CH_INPUT); return normalizeInput(b == null ? 0 : b); }
    setInput(value) {
      const sel = normalizeInput(value);
      if (sel === Input.OFF && !this.allowPowerOff) {
        throw new Error("input 'off' is unreliable; enable allowPowerOff to use it");
      }
      return this.writeByte(CH_INPUT, sel);
    }

    // --- EQ: bass / mid / treble, level -10..+6 ---
    async getEq(channel) {
      const b = await this.readByte(this._eqUuid(channel));
      return b == null ? null : eqByteToLevel(b);
    }
    setEq(channel, level) { return this.writeByte(this._eqUuid(channel), eqLevelToByte(level)); }
    _eqUuid(channel) {
      const c = EQ_CHANNELS[String(channel).toLowerCase()];
      if (!c) throw new Error(`unknown EQ channel ${channel}`);
      return c;
    }

    // --- subwoofer ---
    getSubDetected() { return this.readRaw(CH_SUBSTATUS).then(subDetectedFromBytes); }
    async getSubLevelRaw() {
      const data = await this.readRaw(CH_CHANNEL_VOLUME);
      if (!data || data.length <= SUB_LEVEL_BYTE_INDEX) return null;
      return clamp(data[SUB_LEVEL_BYTE_INDEX], SUB_RAW_MIN, SUB_RAW_MAX);
    }
    setSubLevelRaw(raw) { return this.writeRaw(CH_CHANNEL_VOLUME, [SUB_CHANNEL, clamp(raw, SUB_RAW_MIN, SUB_RAW_MAX)]); }
    async getSubLevelDb() { const r = await this.getSubLevelRaw(); return r == null ? null : subRawToDb(r); }
    setSubLevelDb(db) { return this.setSubLevelRaw(subDbToRaw(clamp(db, SUB_DB_MIN, SUB_DB_MAX))); }
    getSubInvert() { return this.getToggle(CH_SUBINVERT); }
    setSubInvert(on) { return this.setToggle(CH_SUBINVERT, on); }
    getSubMute() { return this.getToggle(CH_SUBMUTE); }
    setSubMute(on) { return this.setToggle(CH_SUBMUTE, on); }

    // --- speaker placement / boundary gain ---
    async getPlacement() { return placementFromByte(await this.readByte(CH_BOUNDARY_GAIN)); }
    setPlacement(value) { return this.writeByte(CH_BOUNDARY_GAIN, normalizePlacement(value)); }

    // --- toggles & modes ---
    async getToggle(charUuid) { const b = await this.readByte(charUuid); return b == null ? null : !!b; }
    setToggle(charUuid, on) { return this.writeByte(charUuid, on ? 1 : 0); }
    setVocal(i) { return this.writeByte(CH_VOCAL, clamp(i, 0, 3)); }
    setEqmode(i) { return this.writeByte(CH_EQMODE, clamp(i, 0, 5)); }

    // --- transport ---
    playPause() { return this.writeByte(CH_PLAYPAUSE, 1); }
    nextTrack() { return this.writeByte(CH_NEXT, 0); }
    prevTrack() { return this.writeByte(CH_PREV, 0); }

    // --- name ---
    getName() { return this.readRaw(CH_NAME).then(decodeUtf8); }
    setName(name) { return this.writeRaw(CH_NAME, new TextEncoder().encode(name)); }

    // --- factory reset (irreversible; speaker reboots and drops the link) ---
    factoryReset() { return this.writeRaw(CH_FACTORY_RESET, [0x00]); }

    // --- model + device info ---
    async detectModel() {
      const modelNumber = decodeAscii(await this.readRaw(CH_MODEL_NUMBER));
      const hwRevision = decodeAscii(await this.readRaw(CH_HW_REVISION));
      const name = await this.getName();
      this.model = resolveModel(modelNumber, hwRevision, name);
      return this.model;
    }
    get modelDisplay() { return (MODELS[this.model] || MODELS.unknown).display; }

    async deviceInfo() {
      // 0x2A25 is on the Web Bluetooth blocklist (read throws -> null). The serial
      // can't be recovered any other way in a browser either: it's the BD_ADDR,
      // which Web Bluetooth hides, and the System ID (0x2A23) is only 4 bytes here
      // (OUI + 0xFF) without the unique tail. So serial — and the MAC derived from
      // it — stay null on web; both are desktop-only.
      const serial = decodeAscii(await this.readRaw(CH_SERIAL_NUMBER));
      return {
        model: this.model,
        modelDisplay: this.modelDisplay,
        name: await this.getName(),
        manufacturer: decodeAscii(await this.readRaw(CH_MANUFACTURER)),
        model_number: decodeAscii(await this.readRaw(CH_MODEL_NUMBER)),
        serial_number: serial,
        mac_address: serialToMac(serial),
        firmware_revision: decodeAscii(await this.readRaw(CH_FIRMWARE_REVISION)),
        software_revision: decodeAscii(await this.readRaw(CH_SOFTWARE_REVISION)),
        hardware_revision: decodeAscii(await this.readRaw(CH_HW_REVISION)),
        system_id: decodeSystemId(await this.readRaw(CH_SYSTEM_ID)),
      };
    }

    // --- aggregate status (mirror KlipschStatus) ---
    async status() {
      const raw = await this.getVolumeRaw();
      const sel = await this.getInput();
      return {
        model: this.model,
        input: inputName(sel),
        input_value: sel,
        volume_raw: raw,
        volume_percent: volumeRawToPercent(raw),
        volume_db: volumeRawToDb(raw),
        mute: await this.getMute(),
        bass: await this.getEq("bass"),
        mid: await this.getEq("mid"),
        treble: await this.getEq("treble"),
        night: await this.getToggle(CH_NIGHT),
        dynamic_bass: await this.getToggle(CH_DYNBASS),
        func_sounds: await this.getToggle(CH_FUNCSOUNDS),
        power_mode: await this.getToggle(CH_POWERMODE),
        sub_level_db: await this.getSubLevelDb(),
        sub_invert: await this.getSubInvert(),
        sub_mute: await this.getSubMute(),
        sub_detected: await this.getSubDetected(),
      };
    }

    // --- extra UI-facing toggles ---
    getFuncSounds() { return this.getToggle(CH_FUNCSOUNDS); }
    setFuncSounds(on) { return this.setToggle(CH_FUNCSOUNDS, on); }
    getPowerMode() { return this.getToggle(CH_POWERMODE); }
    setPowerMode(on) { return this.setToggle(CH_POWERMODE, on); }
    getNight() { return this.getToggle(CH_NIGHT); }
    setNight(on) { return this.setToggle(CH_NIGHT, on); }
    getDynamicBass() { return this.getToggle(CH_DYNBASS); }
    setDynamicBass(on) { return this.setToggle(CH_DYNBASS, on); }
  }

  // EQ presets reproduce the desktop app's theme.py (bass, mid, treble).
  const EQ_PRESETS = {
    Flat: [0, 0, 0],
    Vocal: [-3, 6, 0],
    Bass: [6, 0, 0],
    Rock: [3, -1, 3],
    Boom: [6, -10, -10],
  };

  // The physical inputs shown in the remote (skip OFF), in the desktop's order.
  // `icon` is a Material Symbols glyph name, matching theme.py's ft.Icons.*.
  const INPUTS = [
    { key: "tv", label: "TV", icon: "tv" },
    { key: "bluetooth", label: "Bluetooth", icon: "bluetooth" },
    { key: "optical", label: "Optical", icon: "settings_input_svideo" },
    { key: "usb", label: "USB", icon: "usb" },
    { key: "aux", label: "Analog", icon: "cable" },
    { key: "phono", label: "Phono", icon: "album" },
  ];

  global.Klipsch = {
    KlipschClient,
    Input, inputName, normalizeInput,
    Placement, placementName, normalizePlacement,
    EQ_PRESETS, INPUTS,
    MAX_VOLUME_RAW, EQ_MIN, EQ_MAX, SUB_DB_MIN, SUB_DB_MAX,
    volumeRawToPercent, volumeRawToDb, subRawToDb,
    isSupported: () => !!navigator.bluetooth,
  };
})(window);
