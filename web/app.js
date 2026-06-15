/*
 * app.js — wires the DOM to KlipschClient (klipsch.js / Web Bluetooth) and drives
 * the screen-based navigation that mirrors the desktop app:
 *   connect → connecting → remote ⇄ settings → about
 *
 * Writes are optimistic (act on input, no read-back — the speaker's reads lag);
 * a full status() seeds every control once on connect. Speaker calls are wrapped
 * so a transient BLE failure shows a toast instead of throwing.
 */
(function () {
  "use strict";

  const K = window.Klipsch;
  const $ = (id) => document.getElementById(id);
  const els = {};
  let client = null;
  let deviceInfo = null; // cached device_info() — read once per connection

  // The About page fields: [Material icon, label, DeviceInfo key]. Mirrors
  // screens.py ABOUT_FIELDS, minus Serial number: 0x2A25 is on the Web Bluetooth
  // blocklist and the serial (the BD_ADDR) is unreachable from a browser, so the
  // row would only ever show a dash. Desktop keeps it.
  const ABOUT_FIELDS = [
    ["speaker", "Name", "name"],
    ["category", "Model", "modelDisplay"],
    ["business", "Manufacturer", "manufacturer"],
    ["memory", "Firmware", "firmware_revision"],
    ["terminal", "MCU Firmware", "software_revision"],
    ["developer_board", "Hardware", "hardware_revision"],
    ["numbers", "Model number", "model_number"],
    ["fingerprint", "System ID", "system_id"],
  ];

  // ---- tiny helpers --------------------------------------------------------
  function toast(msg, isError) {
    const t = els.toast;
    t.textContent = msg;
    t.classList.toggle("toast-error", !!isError);
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (t.hidden = true), isError ? 5000 : 2200);
  }

  async function guard(fn, errPrefix) {
    try {
      return await fn();
    } catch (e) {
      console.error(e);
      toast(`${errPrefix || "Bluetooth error"}: ${e.message || e}`, true);
      return undefined;
    }
  }

  const SCREENS = ["connect", "connecting", "remote", "settings", "about"];
  function showScreen(name) {
    for (const s of SCREENS) $(`screen-${s}`).hidden = s !== name;
    window.scrollTo(0, 0);
  }

  // paint the blue fill of a range input via the --p variable
  function paint(el) {
    const min = +el.min, max = +el.max, val = +el.value;
    const p = max > min ? ((val - min) / (max - min)) * 100 : 0;
    el.style.setProperty("--p", `${p.toFixed(1)}%`);
  }

  const fmtSigned = (n) => (n > 0 ? `+${n}` : `${n}`);

  // ---- connect / disconnect ------------------------------------------------
  async function onConnect() {
    els.connectError.hidden = true;
    if (!K.isSupported()) {
      els.unsupported.hidden = false;
      return;
    }
    try {
      els.connectStatus.textContent = "Choosing a device…";
      client = await K.KlipschClient.request();
      client.onDisconnect(onGattDisconnected);
      showScreen("connecting");
      els.connectingStatus.textContent = "Connecting…";
      await client.connect();
      els.connectingStatus.textContent = "Reading state…";
      els.modelName.textContent = client.modelDisplay;
      await loadAll();
      await loadDeviceInfo(); // read device info once, up front (seeds the Name row)
      showScreen("remote");
    } catch (e) {
      console.error(e);
      client = null;
      showScreen("connect");
      els.connectStatus.textContent = "Not connected.";
      if (e && e.name === "NotFoundError") return; // user dismissed the chooser
      els.connectError.textContent = e.message || String(e);
      els.connectError.hidden = false;
    }
  }

  function onDisconnect() {
    if (client) client.disconnect();
    client = null;
    deviceInfo = null;
    els.connectStatus.textContent = "Not connected.";
    showScreen("connect");
  }

  function onGattDisconnected() {
    client = null;
    deviceInfo = null;
    els.connectStatus.textContent = "Speaker disconnected.";
    showScreen("connect");
    toast("Speaker disconnected", true);
  }

  // ---- seed every control from one status() --------------------------------
  async function loadAll() {
    const st = await guard(() => client.status(), "Couldn't read status");
    if (!st) return;

    renderInputs(st.input);

    setRange(els.volume, st.volume_raw);
    els.mute.dataset.on = st.mute ? "1" : "";
    reflectMute(els.muteBtn, !!st.mute);

    if (st.bass != null) setRange(els.eqBass, st.bass);
    if (st.mid != null) setRange(els.eqMid, st.mid);
    if (st.treble != null) setRange(els.eqTreble, st.treble);
    syncPresetFromBands();

    els.dynbass.checked = !!st.dynamic_bass;
    els.night.checked = !!st.night;

    // subwoofer (settings)
    const detected = st.sub_detected;
    els.subStatus.textContent = detected === false ? "Not detected" : "";
    setSubEnabled(detected !== false);
    if (st.sub_level_db != null) {
      setRange(els.subLevel, st.sub_level_db);
      els.subLevelVal.textContent = `${st.sub_level_db} dB`;
    }
    els.subMute.dataset.on = st.sub_mute ? "1" : "";
    reflectMute(els.subMuteBtn, !!st.sub_mute);
    els.subInvert.checked = !!st.sub_invert;

    els.powermode.checked = !!st.power_mode;
  }

  async function loadDeviceInfo() {
    els.aboutStatus.hidden = false;
    els.aboutStatus.textContent = "Reading…";
    const info = await guard(() => client.deviceInfo(), "Couldn't read device info");
    els.aboutStatus.hidden = true;
    if (!info) return;
    deviceInfo = info;
    els.nameValue.textContent = info.name || "—";
    els.aboutList.innerHTML = "";
    ABOUT_FIELDS.forEach(([icon, label, key], i) => {
      if (i) els.aboutList.append(hairline());
      const value = info[key];
      const row = document.createElement("div");
      row.className = "list-item";
      row.innerHTML =
        `<span class="mi">${icon}</span>` +
        `<div class="list-text"><div class="list-label">${label}</div></div>` +
        `<span class="trailing">${value ? esc(value) : "—"}</span>`;
      els.aboutList.append(row);
    });
  }

  function hairline() { const hr = document.createElement("hr"); hr.className = "hairline"; return hr; }
  function esc(s) { const d = document.createElement("div"); d.textContent = s; return d.innerHTML; }

  // ---- input tiles ---------------------------------------------------------
  function renderInputs(active) {
    els.inputGrid.innerHTML = "";
    for (const inp of K.INPUTS) {
      const tile = document.createElement("button");
      tile.className = "input-tile" + (inp.key === active ? " active" : "");
      tile.dataset.input = inp.key;
      tile.innerHTML = `<span class="mi">${inp.icon}</span><span>${inp.label}</span>`;
      tile.addEventListener("click", () => onInput(inp.key));
      els.inputGrid.append(tile);
    }
  }
  async function onInput(key) {
    for (const t of els.inputGrid.children) t.classList.toggle("active", t.dataset.input === key);
    await guard(() => client.setInput(key), "Couldn't switch input");
  }

  // ---- range helpers -------------------------------------------------------
  function setRange(el, value) { el.value = String(value); paint(el); }

  // ---- mute button glyph ---------------------------------------------------
  function reflectMute(btn, on) {
    btn.querySelector(".mi").textContent = on ? "volume_off" : "volume_up";
  }

  // ---- EQ presets ----------------------------------------------------------
  function fillPresets() {
    els.eqPreset.innerHTML = "";
    for (const name of Object.keys(K.EQ_PRESETS)) {
      const o = document.createElement("option");
      o.value = name; o.textContent = name;
      els.eqPreset.append(o);
    }
    const custom = document.createElement("option");
    custom.value = "Custom"; custom.textContent = "Custom";
    els.eqPreset.append(custom);
  }
  function currentBands() {
    return [Number(els.eqBass.value), Number(els.eqMid.value), Number(els.eqTreble.value)];
  }
  function syncPresetFromBands() {
    const [b, m, t] = currentBands();
    let match = "Custom";
    for (const [name, [pb, pm, pt]] of Object.entries(K.EQ_PRESETS)) {
      if (pb === b && pm === m && pt === t) { match = name; break; }
    }
    els.eqPreset.value = match;
  }
  async function applyBands(b, m, t) {
    setRange(els.eqBass, b); setRange(els.eqMid, m); setRange(els.eqTreble, t);
    await guard(async () => {
      await client.setEq("bass", b);
      await client.setEq("mid", m);
      await client.setEq("treble", t);
    }, "Couldn't apply EQ");
  }
  async function applyPreset(name) {
    const preset = K.EQ_PRESETS[name];
    if (!preset) return; // "Custom"
    await applyBands(preset[0], preset[1], preset[2]);
  }

  // ---- subwoofer enable ----------------------------------------------------
  function setSubEnabled(on) { els.subCard.classList.toggle("disabled", !on); }

  // ---- collapsible Audio Adjustments --------------------------------------
  function toggleAdj() {
    const collapsed = els.adjBody.classList.toggle("collapsed");
    els.adjChevron.classList.toggle("collapsed", collapsed);
    els.adjChevron.textContent = "expand_less";
  }

  // ---- confirm dialog ------------------------------------------------------
  function confirmDialog(title, body) {
    return new Promise((resolve) => {
      els.confirmTitle.textContent = title;
      els.confirmBody.textContent = body;
      els.confirmBackdrop.hidden = false;
      const done = (ok) => {
        els.confirmBackdrop.hidden = true;
        els.confirmOk.onclick = els.confirmCancel.onclick = null;
        resolve(ok);
      };
      els.confirmOk.onclick = () => done(true);
      els.confirmCancel.onclick = () => done(false);
    });
  }

  // ---- wire ----------------------------------------------------------------
  function init() {
    Object.assign(els, {
      unsupported: $("unsupported"),
      // connect
      connectBtn: $("connect-btn"), connectStatus: $("connect-status"), connectError: $("connect-error"),
      connectingStatus: $("connecting-status"),
      // remote
      remoteRefresh: $("remote-refresh"), modelName: $("model-name"),
      muteBtn: $("mute-btn"), volume: $("volume"),
      inputGrid: $("input-grid"),
      prev: $("prev"), playpause: $("playpause"), next: $("next"),
      eqPreset: $("eq-preset"), eqReset: $("eq-reset"),
      eqBass: $("eq-bass"), eqMid: $("eq-mid"), eqTreble: $("eq-treble"),
      adjHeader: $("adj-header"), adjBody: $("adj-body"), adjChevron: $("adj-chevron"),
      dynbass: $("dynbass"), night: $("night"),
      openSettings: $("open-settings"),
      // settings
      settingsBack: $("settings-back"), settingsDisconnect: $("settings-disconnect"),
      renameRow: $("rename-row"), nameValue: $("name-value"),
      subStatus: $("sub-status"), subCard: $("sub-card"),
      subLevel: $("sub-level"), subLevelVal: $("sub-level-val"),
      subMuteBtn: $("sub-mute-btn"), subInvert: $("sub-invert"),
      powermode: $("powermode"),
      aboutRow: $("about-row"), factoryReset: $("factory-reset"),
      // about
      aboutBack: $("about-back"), aboutStatus: $("about-status"), aboutList: $("about-list"),
      // misc
      toast: $("toast"),
      confirmBackdrop: $("confirm-backdrop"), confirmTitle: $("confirm-title"),
      confirmBody: $("confirm-body"), confirmOk: $("confirm-ok"), confirmCancel: $("confirm-cancel"),
    });
    // mute is button-driven (no checkbox); track its state on the button itself
    els.mute = els.muteBtn;
    els.subMute = els.subMuteBtn;

    if (!K.isSupported()) els.unsupported.hidden = false;

    fillPresets();
    showScreen("connect");

    // connect
    els.connectBtn.addEventListener("click", onConnect);

    // navigation
    els.settingsDisconnect.addEventListener("click", onDisconnect);
    els.remoteRefresh.addEventListener("click", () => guard(loadAll, "Refresh failed"));
    els.openSettings.addEventListener("click", () => showScreen("settings"));
    els.settingsBack.addEventListener("click", () => showScreen("remote"));
    els.aboutRow.addEventListener("click", () => {
      showScreen("about");
      if (!deviceInfo) loadDeviceInfo(); // already read on connect; only retry if it failed
    });
    els.aboutBack.addEventListener("click", () => showScreen("settings"));

    // volume
    els.volume.addEventListener("input", () => paint(els.volume));
    els.volume.addEventListener("change", () =>
      guard(() => client.setVolumeRaw(Number(els.volume.value)), "Couldn't set volume"));
    els.muteBtn.addEventListener("click", () => {
      const on = !(els.muteBtn.dataset.on === "1");
      els.muteBtn.dataset.on = on ? "1" : "";
      reflectMute(els.muteBtn, on);
      guard(() => client.setMute(on), "Couldn't toggle mute");
    });

    // transport
    els.prev.addEventListener("click", () => guard(() => client.prevTrack(), "Prev failed"));
    els.next.addEventListener("click", () => guard(() => client.nextTrack(), "Next failed"));
    els.playpause.addEventListener("click", () => guard(() => client.playPause(), "Play/Pause failed"));

    // EQ
    bindBand(els.eqBass, "bass");
    bindBand(els.eqMid, "mid");
    bindBand(els.eqTreble, "treble");
    els.eqPreset.addEventListener("change", () => applyPreset(els.eqPreset.value));
    els.eqReset.addEventListener("click", () => applyBands(0, 0, 0));

    // adjustments
    els.adjHeader.addEventListener("click", toggleAdj);
    els.dynbass.addEventListener("change", () =>
      guard(() => client.setDynamicBass(els.dynbass.checked), "Couldn't toggle dynamic bass"));
    els.night.addEventListener("change", () =>
      guard(() => client.setNight(els.night.checked), "Couldn't toggle night mode"));

    // subwoofer
    els.subLevel.addEventListener("input", () => {
      paint(els.subLevel);
      els.subLevelVal.textContent = `${els.subLevel.value} dB`;
    });
    els.subLevel.addEventListener("change", () =>
      guard(() => client.setSubLevelDb(Number(els.subLevel.value)), "Couldn't set sub level"));
    els.subMuteBtn.addEventListener("click", () => {
      const on = !(els.subMuteBtn.dataset.on === "1");
      els.subMuteBtn.dataset.on = on ? "1" : "";
      reflectMute(els.subMuteBtn, on);
      guard(() => client.setSubMute(on), "Couldn't toggle sub mute");
    });
    els.subInvert.addEventListener("change", () =>
      guard(() => client.setSubInvert(els.subInvert.checked), "Couldn't toggle phase invert"));

    // settings rows
    els.renameRow.addEventListener("click", onRename);
    els.powermode.addEventListener("change", () =>
      guard(() => client.setPowerMode(els.powermode.checked), "Couldn't toggle auto-standby"));
    els.factoryReset.addEventListener("click", onFactoryReset);
  }

  function bindBand(slider, channel) {
    slider.addEventListener("input", () => { paint(slider); els.eqPreset.value = "Custom"; });
    slider.addEventListener("change", () =>
      guard(() => client.setEq(channel, Number(slider.value)), `Couldn't set ${channel}`)
        .then(syncPresetFromBands));
  }

  async function onRename() {
    const current = els.nameValue.textContent === "—" ? "" : els.nameValue.textContent;
    const name = window.prompt("Speaker name", current);
    if (name == null) return;
    const trimmed = name.trim();
    if (!trimmed) return toast("Name can't be empty", true);
    await guard(() => client.setName(trimmed), "Couldn't rename");
    els.nameValue.textContent = trimmed; // optimistic
    toast("Renamed");
    await loadDeviceInfo(); // re-parse so the Name row + About reflect the speaker
  }

  async function onFactoryReset() {
    const ok = await confirmDialog(
      "Factory Reset?",
      "This erases every setting and restarts the speaker — you'll need to reconnect. This can't be undone."
    );
    if (!ok) return;
    await guard(() => client.factoryReset(), "Factory reset failed");
    toast("Factory reset sent — the speaker will reboot");
    onDisconnect();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
