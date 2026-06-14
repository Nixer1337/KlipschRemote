# KlipschRemote — Web (Web Bluetooth)

A zero-install, browser-based remote for Klipsch powered speakers (The Fives /
The Sevens / The Nines). It talks the **same BLE control protocol** as the
desktop app, but over the **Web Bluetooth API** instead of `bleak`, so it runs
as a plain static page — nothing to download.

It's a faithful port of [`klipsch_ble`](../klipsch_ble): volume, mute, input,
3-band EQ + presets, sound modes, subwoofer, transport, rename, auto-standby,
function sounds, factory reset and read-only device info.

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

- **Serial number** isn't shown: characteristic `0x2A25` is on the
  [Web Bluetooth blocklist](https://github.com/WebBluetoothCG/registries) and
  can't be read from a page. Everything else in Device Info works.
- **No firmware update** — that's Bluetooth Classic / RFCOMM, which browsers
  can't speak. (The Python library doesn't implement flashing either; see
  [`FIRMWARE_PROTOCOL.md`](../klipsch_ble/FIRMWARE_PROTOCOL.md).)
- A speaker that is connected as audio but not advertising BLE may not appear in
  the chooser on some OSes; wake it / start playback and retry.
- **Icons/fonts load from Google Fonts** (Material Symbols + Roboto) over the
  network, so glyphs render as their text names until the font loads, and won't
  appear fully offline. The BLE control itself needs no network.

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

## License

Same as the parent project — [Apache 2.0](../LICENSE). Unofficial; not affiliated
with Klipsch.
