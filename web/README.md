# KlipschRemote — Web (Web Bluetooth)

A zero-install, browser-based remote for Klipsch powered speakers (The Fives /
The Sevens / The Nines). It talks the **same BLE control protocol** as the
desktop app, but over the **Web Bluetooth API** instead of `bleak`, so it runs
as a plain static page — nothing to download.

It's a faithful port of [`klipsch_ble`](../klipsch_ble): volume, mute, input,
3-band EQ + presets, sound modes, subwoofer, transport, rename, auto-standby,
function sounds, factory reset and read-only device info.

**Live at [klipsch.io](https://klipsch.io/)** — just open it in Chrome / Edge /
Opera. The rest of this page is for running or self-hosting it yourself.

## Run it

It must be served from a **secure context** (Web Bluetooth requires HTTPS or
`localhost` — `file://` won't work):

```sh
# from the repo root
python -m http.server 8000 --directory web
# then open http://localhost:8000 in Chrome/Edge
```

Or host the `web/` folder anywhere static + HTTPS (GitHub Pages, Netlify, …) and
open the URL.

## Install as an app (PWA)

The page is a **Progressive Web App**: a service worker (`sw.js`) precaches the
shell (HTML/JS/CSS/icons) and runtime-caches the Google Fonts, so after the first
visit it launches instantly and works offline (the BLE control needs no network
anyway). On a supporting browser the Connect screen shows an **Install app**
button (and the address bar offers an install icon) — install it and it runs in
its own window from the launcher / Start menu / home screen, like a native app.
`manifest.json` defines the name, icons and standalone display.

> **Maintainer note:** the cache is keyed by `VERSION` in `sw.js`. **Bump it**
> whenever you change any shelled file (`index.html`, `app.js`, `klipsch.js`,
> `styles.css`, an icon, or the `SHELL` list) so installed clients pick up the
> new assets instead of the stale cached ones.

## Browser & platform support

| Works | Doesn't work |
|---|---|
| Chrome, Edge, Opera — desktop **and** Android | Firefox (no Web Bluetooth) |
| | Safari / **any browser on iOS / iPadOS** (Apple doesn't ship Web Bluetooth) |

## Before you connect

> [!IMPORTANT]
> Pair the speaker with your OS as a Bluetooth **audio** device first — exactly
> like the desktop app. The control characteristics need an encrypted link whose
> LE key is derived from that audio bond (CTKD). **Never** add/pair it as an
> LE-only / "Other" device, and never unpair it — that breaks GATT control.

Then click **Connect** and pick the speaker from the browser's chooser. The page
can't scan silently or connect by MAC — that's the browser's privacy model. On
later visits the browser can offer to reconnect to a previously granted device
without re-picking.

## Known limits vs. the desktop app

- **Serial number** row is omitted (desktop keeps it): characteristic `0x2A25`
  is on the [Web Bluetooth blocklist](https://github.com/WebBluetoothCG/registries)
  and can't be read from a page. It can't be reconstructed either — the serial is
  the BD_ADDR, which Web Bluetooth hides, and the System ID (`0x2A23`) reports only
  the OUI here, not the unique tail. Everything else in Device Info works.
- **No firmware update** — that's Bluetooth Classic / RFCOMM, which browsers
  can't speak. (The Python library doesn't implement flashing either; see
  [`FIRMWARE_PROTOCOL.md`](../klipsch_ble/FIRMWARE_PROTOCOL.md).)
- A speaker that is connected as audio but not advertising BLE may not appear in
  the chooser on some OSes; wake it / start playback and retry.
- **Icons/fonts load from Google Fonts** (Material Symbols + Roboto) over the
  network, so on the very first visit glyphs render as their text names until the
  font loads. The service worker caches them afterwards, so subsequent (and
  offline) launches render fully. The BLE control itself needs no network.

## Look & feel

A faithful, screen-based reproduction of the desktop app (`klipsch_remote`):
**Connect → Remote ⇄ Settings → About**, with the same Material 3 dark theme,
card layout, vertical EQ bands, collapsible *Audio Adjustments*, and grouped
settings rows.

## Files

| File | Role |
|---|---|
| `klipsch.js` | The BLE client — port of `constants.py` + `client.py` to Web Bluetooth. No DOM, reusable. |
| `app.js` | Wires the DOM controls to the client; screen navigation. |
| `index.html` | Markup (connect / remote / settings / about screens). |
| `styles.css` | Google "dark neutral" theme + Material widgets, matching `klipsch_remote/theme.py` / `screens.py`. |
| `icon.png` | App icon (copy of `klipsch_remote/assets/icon.png`) so `web/` deploys standalone. |
| `manifest.json` | PWA manifest — name, icons, standalone display. |
| `sw.js` | Service worker — precaches the shell + runtime-caches fonts for offline / install. |
| `icon-192.png` / `icon-512.png` / `maskable-512.png` | PWA install icons (generated from `icon.png`; maskable has safe-zone padding). |

## Keeping in sync with the Python protocol

`klipsch.js` is a hand-maintained mirror of [`klipsch_ble/constants.py`](../klipsch_ble/constants.py)
and [`models.py`](../klipsch_ble/models.py) — there is no build step linking them.
[`tests/test_web_parity.py`](../tests/test_web_parity.py) guards against drift: it
parses the GATT UUIDs, characteristic→service map, numeric constants, inputs and
model tables straight out of this file and asserts they equal the Python source
of truth (it runs in the normal `pytest` CI, no Node needed). If you change a
UUID, add an input/model, or touch a conversion here, run `pytest` — a mismatch
fails the build until both sides agree.

## License

Same as the parent project — [Apache 2.0](../LICENSE). Unofficial; not affiliated
with Klipsch.
