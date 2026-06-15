"""Cross-platform desktop remote for Klipsch powered speakers (Flet GUI).

A graphical front-end on top of the :mod:`klipsch_ble` library — the same
async ``KlipschClient`` that drives the CLI, here behind a Flet UI. Flet runs its
own asyncio loop, so BLE coroutines are simply ``await``-ed inside the control
handlers; all access goes through one :class:`asyncio.Lock` so concurrent GATT
reads/writes never overlap on the single client.

Two screens:
  * **Connect** — pick a paired Klipsch (enumerated off the OS), type an address,
    or scan the air; the chosen address is remembered (``~/.klipsch.json``).
  * **Remote** — device-name header, volume + mute, a 3x2 input tile grid,
    transport, a 3-band EQ (vertical sliders + UI presets / reset-to-flat) and a
    collapsible Audio Adjustments panel (dynamic bass, night mode).

Run it with ``python -m klipsch_remote`` (or ``flet run klipsch_remote/app.py``). The
speaker must be paired with the OS as an AUDIO device — see the library README.
"""

from __future__ import annotations

import asyncio
import datetime
import math
import os
import sys
import webbrowser

import flet as ft

from klipsch_ble import (
    KlipschAccessError,
    KlipschClient,
    KlipschNotFoundError,
    discover,
    placement_name,
)
from klipsch_ble.cli import (
    list_paired_bluetooth,
    load_config,
    load_saved_address,
    save_address,
    save_config,
)
from klipsch_ble.constants import (
    CH_DYNBASS,
    CH_NIGHT,
    CH_POWERMODE,
    CH_SUBINVERT,
    EQ_MAX,
    EQ_MIN,
    MAX_VOLUME_RAW,
    SUB_DB_MAX,
    SUB_DB_MIN,
)

from . import autostart, screens
from .single_instance import SingleInstance, bring_to_front
from .theme import (
    CUSTOM,
    EQ_PRESETS,
    INPUT_KEYS,
    INPUTS,
    OUTLINE,
    SEED,
    TRANSPORT,
    build_theme,
)
from .tray import TRAY_SUPPORTED, start_tray
from .widgets import VSlider

# Default for a fresh install (preserves the original always-on-tray behaviour).
_TRAY_DEFAULT = True

# Screenshot / demo mode (off for every normal launch). KLIPSCH_DEMO swaps the
# BLE transport for an in-memory fake (see _demo.py); KLIPSCH_SHOT additionally
# drives the app straight to one screen and writes a ready-marker so an external
# capture script knows when to grab the window. See tools/capture_screenshots.ps1.
_DEMO = os.environ.get("KLIPSCH_DEMO") == "1"
_SHOT = (os.environ.get("KLIPSCH_SHOT") or "").strip().lower() or None
_SHOT_MODE = _DEMO and _SHOT is not None

# Speaker-placement (boundary-gain) copy: the live description shown under the
# selector for the current choice. The byte each maps to is the bass gain the
# speaker adds — most when free-standing, least in a corner (where the room
# already reinforces bass). See klipsch_ble.constants.Placement.
_PLACEMENT_HINT: dict[str, str] = {
    "corner": "In a corner the room reinforces bass the most — the speaker adds "
              "the least.",
    "wall": "Against a wall the room adds some bass — the speaker adds a "
            "moderate amount.",
    "open": "Free-standing, away from walls — no room reinforcement, so the "
            "speaker adds the most bass.",
}


def _diaglog(msg: str) -> None:
    """Best-effort startup diagnostics to ``~/.klipsch_remote.log``.

    A windowless login autostart has nowhere to print, so tray/startup failures
    were invisible. This appends a timestamped line we can read after a boot to
    see what actually happened (tray registered? fell back to the window?).
    Never raises — diagnostics must not affect startup.
    """
    try:
        path = os.path.join(os.path.expanduser("~"), ".klipsch_remote.log")
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{stamp}  {msg}\n")
    except Exception:  # diagnostics are best-effort only
        pass


def _icon_path() -> str | None:
    """Locate the app icon (`assets/icon.ico`) in source or a frozen bundle."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, "assets", "icon.ico")]
    base = getattr(sys, "_MEIPASS", None)  # set by PyInstaller at runtime
    if base:
        candidates.append(os.path.join(base, "klipsch_remote", "assets", "icon.ico"))
        candidates.append(os.path.join(base, "assets", "icon.ico"))
    return next((p for p in candidates if os.path.exists(p)), None)


class KlipschRemote:
    """Owns the page, one :class:`KlipschClient`, and all the bound controls."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.client: KlipschClient | None = None
        # Read-only device info (firmware/serial/…). Read ONCE per connection in
        # _load_state and cached here — it never changes while connected, so the
        # About page opens instantly from this cache instead of re-reading BLE.
        self._device_info = None
        self.lock = asyncio.Lock()
        self._mounted = False        # first screen mounts without a transition
        self._sub_detected: bool | None = None  # cached: drives the sub group state
        # System-tray state. `_tray` is the running pystray.Icon when close-to-
        # tray is active (its presence is what makes the window's X hide instead
        # of quit); `_loop` is captured so the tray's off-thread menu callbacks
        # can hop back onto the Flet event loop.
        self._tray = None  # the running pystray.Icon (from tray.start_tray) or None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._build_controls()

    # ------------------------------------------------------------------ setup
    def _build_controls(self) -> None:
        """Create every control once; later screens just (re)attach them."""
        # --- connect screen ---
        self.paired_dd = ft.Dropdown(
            label="Paired speakers", options=[], expand=True,
            on_select=self._on_pick_paired,
            border_color=OUTLINE, focused_border_color=SEED,
        )
        # In demo mode show a fake address so screenshots never expose the real
        # saved MAC (the connect screen also re-fills this from the fake list).
        if _DEMO:
            from . import _demo
            _saved_addr = _demo.DEMO_DISPLAY_ADDRESS
        else:
            _saved_addr = load_saved_address()
        self.address_tf = ft.TextField(
            label="Address (MAC, or CoreBluetooth UUID on macOS)",
            value=_saved_addr or "", expand=True,
            border_color=OUTLINE, focused_border_color=SEED,
        )
        # Bottom action bar: secondary (Scan) leading, primary (Connect, filled)
        # trailing — Material's button order. Both expand to share the width.
        self.connect_btn = ft.FilledButton(
            "Connect", icon=ft.Icons.LINK, on_click=self._on_connect,
            expand=True, height=46,
        )
        self.scan_btn = ft.OutlinedButton(
            "Scan the air", icon=ft.Icons.BLUETOOTH_SEARCHING, on_click=self._on_scan,
            expand=True, height=46,
            tooltip="Put the speaker in pairing mode first — hold its Bluetooth "
                    "button until it blinks — then scan, otherwise it won't "
                    "advertise over BLE.",
        )
        self.refresh_paired_btn = ft.IconButton(
            ft.Icons.REFRESH, tooltip="Re-enumerate paired speakers",
            on_click=self._on_load_paired,
        )
        self.conn_status = ft.Text("", color=ft.Colors.ON_SURFACE_VARIANT)
        self.conn_progress = ft.ProgressRing(visible=False, width=18, height=18)
        # Shown on the dedicated "Connecting…" screen during the connect+read flow.
        self.connecting_status = ft.Text(
            "", color=ft.Colors.ON_SURFACE_VARIANT,
            text_align=ft.TextAlign.CENTER)

        # --- remote screen header (the speaker's applied name) ---
        self.model_text = ft.Text(
            "Klipsch", size=22, weight=ft.FontWeight.BOLD)

        # --- volume + mute ---
        # The speaker icon doubles as the mute toggle: plain when live, red and
        # crossed-out (VOLUME_OFF) when muted.
        self._muted = False
        self.mute_btn = ft.IconButton(
            ft.Icons.VOLUME_UP, tooltip="Mute / unmute", on_click=self._on_mute)
        self.vol_slider = ft.Slider(
            min=0, max=MAX_VOLUME_RAW, divisions=MAX_VOLUME_RAW, value=0,
            label="{value}", expand=True,
            on_change_end=self._on_vol_commit,
        )

        # --- input selector: a 3x2 grid of selectable tiles ---
        # Each tile is the Flutter-recommended Material layering: a Stack of
        #   (1) the selection fill  — bottom, full-bleed, cross-fades on select;
        #   (2) the icon+label      — middle, defines the tile's intrinsic size;
        #   (3) the ink/click layer — top, full-bleed, transparent.
        # Because the fill (1) and the ink surface (3) are both Positioned.fill,
        # the hover/press state-layer is exactly the same size & shape as the
        # selection — no inset, no "cheap" small highlight.
        self.input_tiles: dict[str, ft.Control] = {}
        self._tile_fill: dict[str, ft.Container] = {}
        self._tile_icon: dict[str, ft.Icon] = {}
        self._tile_label: dict[str, ft.Text] = {}
        RADIUS = 14
        for key, label, icon in INPUTS:
            self._tile_icon[key] = ft.Icon(icon, size=26)
            self._tile_label[key] = ft.Text(label, size=12)
            self._tile_fill[key] = ft.Container(
                left=0, top=0, right=0, bottom=0, border_radius=RADIUS,
                # The selection state-layer cross-fades in/out (Material standard
                # short ~150 ms transition) instead of snapping.
                animate=ft.Animation(150, ft.AnimationCurve.EASE_IN_OUT))
            content = ft.Container(
                ft.Column([self._tile_icon[key], self._tile_label[key]],
                          spacing=6, tight=True,
                          horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.Padding.symmetric(vertical=12, horizontal=8),
                alignment=ft.Alignment.CENTER)
            interactive = ft.Container(
                left=0, top=0, right=0, bottom=0, border_radius=RADIUS,
                ink=True, data=key, on_click=self._on_input)
            self.input_tiles[key] = ft.Container(
                ft.Stack([self._tile_fill[key], content, interactive]),
                border_radius=RADIUS, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                expand=True)
        self._selected_input = "tv"

        # --- EQ: vertical sliders + a UI preset picker + reset-to-flat ---
        self.eq_sliders: dict[str, VSlider] = {}
        for ch in ("bass", "mid", "treble"):
            self.eq_sliders[ch] = VSlider(
                lo=EQ_MIN, hi=EQ_MAX, height=190,
                on_commit=lambda v, ch=ch: self._eq_user_commit(ch, v),
            )
        self.eq_preset_dd = ft.Dropdown(
            value="Flat", expand=True,
            options=([ft.DropdownOption(key=CUSTOM, text=CUSTOM)]
                     + [ft.DropdownOption(key=n, text=n) for n in EQ_PRESETS]),
            on_select=self._on_eq_preset,
            border_color=OUTLINE, focused_border_color=SEED,
        )
        self.eq_reset_btn = ft.IconButton(
            ft.Icons.REFRESH, tooltip="Reset EQ to flat", on_click=self._on_eq_reset)

        # --- subwoofer: a level slider (dB) + phase-invert / mute toggles ---
        # Sub Level is written as a channel-volume command; the speaker reports
        # it back via SubStatus, so the slider reflects the real value on load.
        self.sub_level_slider = ft.Slider(
            min=SUB_DB_MIN, max=SUB_DB_MAX, divisions=SUB_DB_MAX - SUB_DB_MIN,
            value=0, label="{value} dB", expand=True,
            on_change_end=self._on_sub_level_commit,
        )
        self.subinvert_sw = ft.Switch(value=False, data="subinvert",
                                      on_change=self._on_sub_toggle)
        # Sub Mute mirrors the Volume card's mute control: the speaker icon to the
        # left of the level slider doubles as the toggle — plain when live, red
        # and crossed-out (VOLUME_OFF) when muted — instead of a separate switch.
        self._sub_muted = False
        self.sub_mute_btn = ft.IconButton(
            ft.Icons.VOLUME_UP, tooltip="Mute / unmute the subwoofer",
            on_click=self._on_sub_mute)
        # Detection state, shown next to the "Subwoofer" section title (not as a
        # cheap line in the card body): empty when a sub is present, "Not detected"
        # otherwise — the label that explains why the group below is greyed out.
        self.sub_section_status = ft.Text(
            "", size=12, italic=True, color=ft.Colors.ON_SURFACE_VARIANT)
        # The current sub level in dB, shown trailing on the "Sub Level" row.
        self.sub_level_value_text = ft.Text(
            "0 dB", color=ft.Colors.ON_SURFACE_VARIANT)

        # --- speaker placement (boundary gain) — Settings ---
        # A Material 3 segmented button: one mutually-exclusive choice of where
        # the speaker sits, which sets how much bass it adds back (CH_BOUNDARY_
        # GAIN). Segment values are the placement names (corner/wall/open) so
        # they pass straight to client.set_placement. WALL is the speaker's
        # default until the real value is read on open (_load_placement).
        # NB: `selected` MUST be a list, not a set — Flet msgpack-serializes the
        # control tree and a set raises "can not serialize 'set' object".
        self._placement = "wall"
        self.placement_seg = ft.SegmentedButton(
            allow_multiple_selection=False, allow_empty_selection=False,
            show_selected_icon=False, selected=["wall"],
            on_change=self._on_placement,
            segments=[
                ft.Segment(value="corner", label=ft.Text("Corner"),
                           icon=ft.Icon(ft.Icons.ROUNDED_CORNER),
                           tooltip="In a corner — least added bass"),
                ft.Segment(value="wall", label=ft.Text("Wall"),
                           icon=ft.Icon(ft.Icons.CROP_SQUARE),
                           tooltip="Against a wall — balanced bass"),
                ft.Segment(value="open", label=ft.Text("Open"),
                           icon=ft.Icon(ft.Icons.OPEN_IN_FULL),
                           tooltip="Free-standing — most added bass"),
            ])
        # Live description of the current choice, under the selector.
        self.placement_hint_text = ft.Text(
            _PLACEMENT_HINT["wall"], size=11, color=ft.Colors.ON_SURFACE_VARIANT)

        # --- modes (Audio Adjustments collapsible) ---
        self.dynbass_sw = ft.Switch(value=False, data="dynamic_bass",
                                    on_change=self._on_toggle)
        self.night_sw = ft.Switch(value=False, data="night",
                                  on_change=self._on_toggle)
        self._adj_open = False
        # A single chevron that rotates 180° on expand (Material expansion motion)
        # rather than swapping the glyph.
        self.adj_chevron = ft.Icon(
            ft.Icons.KEYBOARD_ARROW_DOWN, rotate=ft.Rotate(0.0),
            animate_rotation=ft.Animation(200, ft.AnimationCurve.EASE_IN_OUT))

        # --- transport ---
        # A single, STATELESS play/pause control. The speaker doesn't reliably
        # report transport state, so there's no play-vs-pause icon to keep in
        # sync — every press just fires the toggle command. The combined ⏯ symbol
        # is built from two monochrome Material glyphs (play triangle + pause
        # bars) rather than the U+23EF character, which renders as a colour emoji
        # on Windows. Circular ink button to match the IconButtons around it.
        # One shared colour for the whole transport row so play/pause and the
        # prev/next IconButtons match (IconButton otherwise defaults to the
        # greyer on-surface-variant, while a bare Icon defaults to on-surface).
        self.play_btn = ft.Container(
            ft.Row([ft.Icon(ft.Icons.PLAY_ARROW, size=26, color=TRANSPORT),
                    ft.Icon(ft.Icons.PAUSE, size=23, color=TRANSPORT)],
                   spacing=0, tight=True,
                   alignment=ft.MainAxisAlignment.CENTER,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            width=68, height=56, border_radius=28, ink=True,
            alignment=ft.Alignment.CENTER, on_click=self._on_playpause,
            tooltip="Play / pause",
        )
        self.prev_btn = ft.IconButton(ft.Icons.SKIP_PREVIOUS, tooltip="Previous",
                                      icon_size=30, icon_color=TRANSPORT,
                                      on_click=self._on_prev)
        self.next_btn = ft.IconButton(ft.Icons.SKIP_NEXT, tooltip="Next",
                                      icon_size=30, icon_color=TRANSPORT,
                                      on_click=self._on_next)

        # --- settings ---
        # The current speaker name, shown trailing on the Settings > Name row.
        self.name_value_text = ft.Text(
            "", italic=True, color=ft.Colors.ON_SURFACE_VARIANT)
        # App-level: reconnect to the saved speaker automatically on next launch.
        self.autoconnect_sw = ft.Switch(
            value=bool(load_config().get("auto_connect", False)),
            on_change=self._on_toggle_autoconnect)
        # App-level (Windows only): close-to-tray. When on, the window's X hides
        # the app to the system tray (reveal / Quit from the tray icon); when
        # off, there's no tray icon and the X quits. The row is only shown on a
        # tray-capable platform (see show_settings); the value is forced False
        # elsewhere so the close handler always takes the plain-quit path.
        self.close_to_tray_sw = ft.Switch(
            value=TRAY_SUPPORTED and bool(
                load_config().get("close_to_tray", _TRAY_DEFAULT)),
            on_change=self._on_toggle_close_to_tray)
        # App-level: launch on system startup. The OS registration is the source
        # of truth (no config key) — read it back so the switch always matches
        # the real state, even if it was changed outside the app.
        self.autostart_sw = ft.Switch(
            value=autostart.is_supported() and autostart.is_enabled(),
            on_change=self._on_toggle_autostart)
        # Speaker-level: auto-standby (sleep after inactivity) — PowerMode char.
        self.standby_sw = ft.Switch(value=True, on_change=self._on_toggle_standby)

        # --- About page: one bound value Text per read-only DIS field. Filled
        # from a device_info() read when the page opens; "—" until then. ---
        self.about_values = {
            label: ft.Text("—", selectable=True,
                           color=ft.Colors.ON_SURFACE_VARIANT)
            for _icon, label, _attr in screens.ABOUT_FIELDS
        }
        self.about_status = ft.Text(
            "Reading device information…", size=12,
            color=ft.Colors.ON_SURFACE_VARIANT)

    # ----------------------------------------------------------------- screens
    # Screen transitions use a native Flutter AnimatedSwitcher (Material
    # "fade through"): one switcher stays mounted at the page root and we just
    # swap its `content`. Flutter runs the cross-fade on the GPU, so it's smooth
    # regardless of how heavy the two screen trees are — unlike a hand-rolled
    # offset/opacity tween, which stutters on flet_desktop. Each screen gets a
    # distinct `key` so the switcher treats a swap as a real change and animates.
    _NAV_DUR = 220  # ms — Material fade-through feel

    def _present(self, controls: list[ft.Control], *, key: str,
                 after=None) -> None:
        """Swap in a full screen via the root AnimatedSwitcher (fade through).

        ``after`` is an optional async callable run shortly after the swap, once
        the fade has settled — keep post-open work that calls ``page.update()``
        (e.g. a BLE read or device enumeration) out of the fade window or it
        interrupts the animation. The delay is deterministic (a timed task), not
        tied to an animation-end event, so it always runs even across rapid
        consecutive swaps.
        """
        p = self.page
        screen = ft.Column(controls, expand=True, spacing=0, key=key)
        if not self._mounted:
            self._mounted = True
            self.switcher = ft.AnimatedSwitcher(
                content=screen,
                transition=ft.AnimatedSwitcherTransition.FADE,
                duration=self._NAV_DUR,
                reverse_duration=self._NAV_DUR,
                switch_in_curve=ft.AnimationCurve.EASE_OUT,
                switch_out_curve=ft.AnimationCurve.EASE_IN,
                expand=True,
            )
            p.controls = [self.switcher]
            p.update()
            if after is not None:
                p.run_task(after)  # first mount: no fade, run immediately
            return
        self.switcher.content = screen
        p.update()
        if after is not None:
            p.run_task(self._run_after_settle, after)

    async def _run_after_settle(self, after) -> None:
        # Let the fade finish before post-open work that calls page.update().
        await asyncio.sleep(self._NAV_DUR / 1000 + 0.05)
        await after()

    def show_connect(self, *, status: str | None = None,
                     reload: bool = True,
                     preserve_status: bool = False) -> None:
        """The connect screen. ``status`` pre-fills the helper line (e.g. an
        error after a failed connect); ``preserve_status`` re-enumerates paired
        devices (so the picker is populated) but keeps that helper line instead
        of overwriting it with the device count."""
        self.page.scroll = None
        if status is not None:
            self.conn_status.value = status
        # Enumerate paired devices after the fade settles (the off-thread scan
        # ends in a page.update() that would otherwise interrupt the fade).
        # `after` must be a coroutine *function* (page.run_task rejects a plain
        # lambda), so wrap the parameterised call in a local async def.
        async def _reload_paired() -> None:
            await self._load_paired(preserve_status=preserve_status)
        self._present(screens.connect_controls(self), key="connect",
                      after=_reload_paired if reload else None)

    def show_connecting(self, message: str) -> None:
        self.page.scroll = None
        self.connecting_status.value = message
        self._present(screens.connecting_controls(self), key="connecting")

    def show_remote(self) -> None:
        self.page.scroll = None
        self._present(screens.remote_controls(self), key="remote")

    def show_settings(self) -> None:
        """The Settings screen. Only auto-standby (PowerMode) is read lazily on
        open (via ``after``) — it's not part of the main status read. The
        subwoofer is NOT re-read here: its state came with the last status()
        (connect / Refresh) and the card is built from that cache, so opening
        this tab is instant and doesn't visibly re-run detection."""
        self.page.scroll = None
        self._present(screens.settings_controls(self), key="settings",
                      after=self._load_settings_extras)

    def show_about(self) -> None:
        """The About page (Settings > Product > About).

        Device info was read once at connect (``_load_state``) and cached, so the
        page opens already populated — no loading flash. Only if that read failed
        (no cache) do we fall back to a lazy read with the status line showing."""
        self.page.scroll = None
        if self._device_info is not None:
            self._apply_device_info()
            self._present(screens.about_controls(self), key="about")
            return
        # Fallback: connect-time read failed — show the page in a loading state
        # and read on open.
        for text in self.about_values.values():
            text.value = "—"
        self.about_status.value = "Reading device information…"
        self.about_status.visible = True
        self._present(screens.about_controls(self), key="about",
                      after=self._load_device_info)

    # ------------------------------------------------------------ helpers / io
    def snack(self, message: str, *, error: bool = False) -> None:
        self.page.show_dialog(ft.SnackBar(
            ft.Text(message),
            bgcolor=ft.Colors.ERROR if error else None,
        ))

    async def _guard(self, make_coro) -> object | None:
        """Serialize one BLE op on the client; surface failures as a snackbar.

        ``make_coro`` is a zero-arg callable that builds the coroutine *inside*
        the lock — so ``self.client`` is dereferenced only after the None-check.
        A tap that races a disconnect then simply no-ops instead of raising
        ``AttributeError`` on a torn-down client.
        """
        if self.client is None:
            return None
        async with self.lock:
            try:
                return await make_coro()
            except Exception as exc:  # any BLE/GATT failure is user-facing
                self.snack(f"{type(exc).__name__}: {exc}", error=True)
                return None

    def _reflect_mute(self, muted: bool) -> None:
        self._muted = muted
        self.mute_btn.icon = ft.Icons.VOLUME_OFF if muted else ft.Icons.VOLUME_UP
        self.mute_btn.icon_color = ft.Colors.ERROR if muted else None

    def _reflect_input(self, name: str) -> None:
        """Highlight the selected input tile, dim the rest."""
        self._selected_input = name
        for key in self.input_tiles:
            on = key == name
            self._tile_fill[key].bgcolor = (
                ft.Colors.with_opacity(0.18, SEED) if on else None)
            self._tile_icon[key].color = SEED if on else None
            self._tile_label[key].color = SEED if on else None

    def _reflect_eq(self, bass: int, mid: int, treble: int) -> None:
        """Set the three band sliders and pick the matching preset (or Custom).

        Uses ``update=False`` so it is safe before the sliders are attached; the
        caller always follows with ``page.update()``.
        """
        for ch, val in (("bass", bass), ("mid", mid), ("treble", treble)):
            self.eq_sliders[ch].set_value(val, update=False)
        self.eq_preset_dd.value = self._match_preset(bass, mid, treble)

    @staticmethod
    def _match_preset(bass: int, mid: int, treble: int) -> str:
        for name, vals in EQ_PRESETS.items():
            if vals == (bass, mid, treble):
                return name
        return CUSTOM

    # ----------------------------------------------------------- connect logic
    async def _load_paired(self, *, preserve_status: bool = False) -> None:
        # PnP/bluetoothctl enumeration shells out and blocks, so run it in a
        # worker thread (awaited here) — the UI event loop never freezes.
        # ``preserve_status`` keeps an existing helper message (e.g. a connect
        # error after auto-connect failed) instead of replacing it with the
        # device count, so the picker still gets populated AND the error stays.
        self.refresh_paired_btn.disabled = True
        self.page.update()
        if _DEMO:
            from . import _demo
            devices = _demo.paired_devices()  # fake list — never the real speaker
        else:
            try:
                devices = await asyncio.to_thread(list_paired_bluetooth)
            except Exception:
                devices = []
        self.paired_dd.options = [
            ft.DropdownOption(key=d.address, text=f"{d.name}  [{d.address}]")
            for d in devices
        ]
        # Keep the picker in sync with the address field: preselect the saved /
        # typed speaker if it's among the paired devices (so after auto-connect
        # the right device shows selected), else auto-pick a lone device.
        typed = (self.address_tf.value or "").strip().upper()
        match = next((d for d in devices if d.address.upper() == typed), None)
        if match:
            self.paired_dd.value = match.address
        elif len(devices) == 1:
            self.paired_dd.value = devices[0].address
            self.address_tf.value = devices[0].address
        if not preserve_status:
            self.conn_status.value = (
                f"{len(devices)} paired Bluetooth device(s) found." if devices
                else "No paired Bluetooth devices found — type an address or scan.")
        self.refresh_paired_btn.disabled = False
        self.page.update()

    def _on_load_paired(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._load_paired)

    def _on_pick_paired(self, _e: ft.ControlEvent) -> None:
        if self.paired_dd.value:
            self.address_tf.value = self.paired_dd.value
            self.page.update()

    def _new_client(self, address: str) -> KlipschClient:
        """Build the client for an address — the real BLE one, or, in demo mode,
        an in-memory fake so the UI populates without hardware (screenshots)."""
        if _DEMO:
            from . import _demo
            return _demo.make_client(address)
        return KlipschClient(address)

    async def _connect(self, address: str, *, attempts: int = 2) -> None:
        # Show the loading screen here so every entry point — the Connect button
        # AND startup auto-connect — gets the same connecting indication.
        self._device_info = None  # new connection: drop any cached device info
        self.show_connecting(f"Connecting to {address} …")
        # BLE connects fail intermittently — the very first one after launch, a
        # speaker waking from standby, or a quick relaunch where the previous
        # process's GATT link hasn't been released yet — so retry a few times
        # before giving up. That last case surfaces as a *transient* access
        # error, so unlike before we retry KlipschAccessError too and only show
        # the "pair as AUDIO device" guidance if EVERY attempt hit it.
        last_exc: Exception | None = None
        client: KlipschClient | None = None
        for attempt in range(1, attempts + 1):
            client = self._new_client(address)
            try:
                await client.connect()
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                try:
                    await client.disconnect()
                except Exception:  # best-effort cleanup before retry
                    pass
                if attempt < attempts:
                    self.connecting_status.value = (
                        f"Attempt {attempt} didn't take — retrying "
                        f"({attempt + 1}/{attempts}) …")
                    self.page.update()
                    await asyncio.sleep(1.2)
        if last_exc is not None:
            if isinstance(last_exc, KlipschAccessError):
                self._connect_failed("No control access — pair the speaker as an "
                                     "AUDIO device (not 'Other'/LE). Never unpair.")
            elif isinstance(last_exc, KlipschNotFoundError):
                self._connect_failed(str(last_exc))
            else:
                self._connect_failed(f"{type(last_exc).__name__}: {last_exc}")
            return
        self.client = client
        if not _DEMO:
            save_address(address)  # demo runs never touch the saved address
        # Read the full state on the loading screen, then open an already-
        # populated remote (no flash of empty/disabled controls).
        self.connecting_status.value = "Reading speaker state …"
        self.page.update()
        await self._load_state()
        self.show_remote()

    async def _auto_connect(self, address: str) -> None:
        """Startup auto-connect — a few more retries than a manual click, since
        the adapter and the speaker may still be warming up right after launch."""
        await self._connect(address, attempts=3)

    def _connect_failed(self, msg: str) -> None:
        """Connection/read failed — return to the connect screen with the error
        shown AND the paired-device picker (re)populated, so the user can pick a
        device and retry without having to hit refresh first."""
        self.client = None
        self.show_connect(status=msg, reload=True, preserve_status=True)

    def _set_busy(self, busy: bool, status: str) -> None:
        self.conn_progress.visible = busy
        self.connect_btn.disabled = busy
        self.scan_btn.disabled = busy
        self.conn_status.value = status
        self.page.update()

    def _on_connect(self, _e: ft.ControlEvent) -> None:
        address = (self.address_tf.value or "").strip()
        if not address:
            self.conn_status.value = "Pick a paired speaker or type an address first."
            self.page.update()
            return
        self.page.run_task(self._connect, address)

    async def _scan(self) -> None:
        self._set_busy(True, "Scanning the air for advertising Klipsch …")
        try:
            hits = await discover()
        except Exception as exc:
            self._set_busy(False, f"Scan failed: {exc}")
            return
        if not hits:
            self._set_busy(False, "Nothing found (a speaker connected as audio "
                                  "may not advertise).")
            return
        self.paired_dd.options = [
            ft.DropdownOption(key=h.address, text=f"{h.name}  [{h.address}]")
            for h in hits
        ]
        self.paired_dd.value = hits[0].address
        self.address_tf.value = hits[0].address
        self._set_busy(False, f"Found {len(hits)} speaker(s).")

    def _on_scan(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._scan)

    # ------------------------------------------------------------ remote state
    async def _load_state(self) -> None:
        """Read the full speaker state into the controls. Safe whether the remote
        screen is attached yet (initial connect) or not (the refresh button)."""
        c = self.client
        if c is None:
            return
        st = await self._guard(c.status)
        if st is None:
            return
        name = (await self._guard(c.get_name)) or c.model.display_name
        self.model_text.value = name
        self.name_value_text.value = name
        # Device info is immutable for this connection — read it once (here, with
        # the rest of the state) and cache it so the About page opens instantly.
        # Refresh re-runs _load_state but skips this (already cached).
        if self._device_info is None:
            self._device_info = await self._guard(c.device_info)
        self.vol_slider.value = st.volume_raw
        self._reflect_mute(bool(st.mute))
        if st.input in INPUT_KEYS:
            self._reflect_input(st.input)
        if None not in (st.bass, st.mid, st.treble):
            self._reflect_eq(st.bass, st.mid, st.treble)
        self.night_sw.value = bool(st.night)
        self.dynbass_sw.value = bool(st.dynamic_bass)
        # Subwoofer lives on the Settings screen, but its state comes from this
        # one status read (shared with connect + the Refresh button) — NOT a
        # separate BLE read each time Settings opens. We reflect it into the
        # (persistent) sub controls here and cache detection; show_settings then
        # just applies the cached state to the freshly-built card, so opening the
        # tab is instant and doesn't visibly re-run detection.
        self._reflect_sub_detected(st.sub_detected)
        self._reflect_sub_level(st.sub_level_db)
        self.subinvert_sw.value = bool(st.sub_invert)
        self._reflect_sub_mute(bool(st.sub_mute))
        # Transport is stateless (single ⏯ command button), so there is nothing
        # to read or reflect here.
        self.page.update()

    def _on_refresh(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._load_state)

    async def _disconnect(self) -> None:
        if self.client is not None:
            await self._guard(lambda: self.client.disconnect())
            self.client = None
        self._device_info = None
        self.show_connect()

    def _on_disconnect(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._disconnect)

    # ---------------------------------------------------------------- handlers
    async def _vol_commit(self, raw: int) -> None:
        # The slider writes only on change-end, so dragging doesn't flood the link.
        await self._guard(lambda: self.client.set_volume_raw(raw))

    def _on_vol_commit(self, e: ft.ControlEvent) -> None:
        self.page.run_task(self._vol_commit, int(e.control.value))

    async def _mute(self) -> None:
        new = not self._muted
        await self._guard(lambda: self.client.set_mute(new))
        self._reflect_mute(new)
        self.mute_btn.update()

    def _on_mute(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._mute)

    async def _input(self, name: str) -> None:
        await self._guard(lambda: self.client.set_input(name))
        self._reflect_input(name)
        self.page.update()

    def _on_input(self, e: ft.ControlEvent) -> None:
        # Fired by an input tile; its key lives in .data.
        if e.control.data and e.control.data != self._selected_input:
            self.page.run_task(self._input, e.control.data)

    async def _eq_commit(self, ch: str, level: int) -> None:
        await self._guard(lambda: self.client.set_eq(ch, level))

    def _eq_user_commit(self, ch: str, level: int) -> None:
        # Called by a VSlider on tap / drag-end. A manual move makes the preset
        # "Custom" (or re-matches a named one), then writes the band.
        v = {c: self.eq_sliders[c].value for c in ("bass", "mid", "treble")}
        self.eq_preset_dd.value = self._match_preset(v["bass"], v["mid"], v["treble"])
        self.eq_preset_dd.update()
        self.page.run_task(self._eq_commit, ch, level)

    async def _apply_eq(self, bass: int, mid: int, treble: int) -> None:
        """Set all three bands on the speaker, then mirror them in the UI."""
        for ch, val in (("bass", bass), ("mid", mid), ("treble", treble)):
            await self._guard(lambda c=ch, v=val: self.client.set_eq(c, v))
        self._reflect_eq(bass, mid, treble)
        self.page.update()

    def _on_eq_preset(self, e: ft.ControlEvent) -> None:
        preset = EQ_PRESETS.get(e.control.value)
        if preset is not None:
            self.page.run_task(self._apply_eq, *preset)

    def _on_eq_reset(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._apply_eq, *EQ_PRESETS["Flat"])

    async def _toggle(self, name: str, on: bool) -> None:
        # Each mode maps straight to a characteristic toggle (0/1).
        char = {"night": CH_NIGHT, "dynamic_bass": CH_DYNBASS}[name]
        await self._guard(lambda: self.client.set_toggle(char, on))

    def _on_toggle(self, e: ft.ControlEvent) -> None:
        self.page.run_task(self._toggle, e.control.data, bool(e.control.value))

    # ------------------------------------------------------------- subwoofer
    def _reflect_sub_detected(self, detected: bool | None) -> None:
        """Show the detection status by the title and enable/disable the group.

        When no sub is connected the whole card is made non-interactive
        (``disabled``) and faded to Material's 38% disabled-content opacity — one
        uniform greyed-out group, not a patchwork of individually-dimmed widgets.
        A failed/absent read (None) is treated as "not detected".
        """
        present = detected is True
        self._sub_detected = detected
        self.sub_section_status.value = "" if present else "Not detected"
        # The card is rebuilt every time Settings opens; only touch it if it
        # exists (show_settings applies the cached state to a freshly-built card).
        card = getattr(self, "sub_card", None)
        if card is not None:
            card.disabled = not present
            card.opacity = 1.0 if present else 0.38

    def _reflect_sub_level(self, db: int | None) -> None:
        if db is not None:
            self.sub_level_slider.value = db
            self.sub_level_value_text.value = f"{db} dB"

    async def _sub_level_commit(self, db: int) -> None:
        # Slider writes only on change-end, so dragging doesn't flood the link.
        await self._guard(lambda: self.client.set_sub_level_db(db))
        self.sub_level_value_text.value = f"{db} dB"
        self.sub_level_value_text.update()

    def _on_sub_level_commit(self, e: ft.ControlEvent) -> None:
        self.page.run_task(self._sub_level_commit, int(e.control.value))

    async def _sub_toggle(self, name: str, on: bool) -> None:
        char = {"subinvert": CH_SUBINVERT}[name]
        await self._guard(lambda: self.client.set_toggle(char, on))

    def _on_sub_toggle(self, e: ft.ControlEvent) -> None:
        self.page.run_task(self._sub_toggle, e.control.data, bool(e.control.value))

    def _reflect_sub_mute(self, muted: bool) -> None:
        self._sub_muted = muted
        self.sub_mute_btn.icon = (
            ft.Icons.VOLUME_OFF if muted else ft.Icons.VOLUME_UP)
        self.sub_mute_btn.icon_color = ft.Colors.ERROR if muted else None

    async def _sub_mute(self) -> None:
        new = not self._sub_muted
        await self._guard(lambda: self.client.set_sub_mute(new))
        self._reflect_sub_mute(new)
        self.sub_mute_btn.update()

    def _on_sub_mute(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._sub_mute)

    # ----------------------------------------------- speaker placement
    def _reflect_placement(self, name: str) -> None:
        """Mirror a placement name into the segmented button + hint line.

        Pure (no ``.update()``) so it's safe before the controls are attached;
        callers push the update."""
        self._placement = name
        self.placement_seg.selected = [name]
        self.placement_hint_text.value = _PLACEMENT_HINT.get(
            name, _PLACEMENT_HINT["wall"])

    async def _apply_placement(self, name: str) -> None:
        # set_placement accepts the placement name directly (corner/wall/open).
        # NB: named _apply_placement, NOT _placement — the latter is the cached
        # name attribute (self._placement), which would shadow a same-named
        # method and make run_task receive a string ("handler must be a
        # coroutine function").
        await self._guard(lambda: self.client.set_placement(name))
        self._reflect_placement(name)
        self.page.update()

    def _on_placement(self, e: ft.ControlEvent) -> None:
        # SegmentedButton hands back the new selection as a one-element list.
        selected = e.control.selected
        if not selected:
            return
        name = selected[0]
        if name != self._placement:
            self.page.run_task(self._apply_placement, name)

    def _adj_radius(self) -> ft.BorderRadius:
        """Header corners: all rounded when collapsed, only the top when open."""
        return (ft.BorderRadius.only(top_left=12, top_right=12) if self._adj_open
                else ft.BorderRadius.all(12))

    def _on_toggle_adj(self, _e: ft.ControlEvent) -> None:
        self._adj_open = not self._adj_open
        self.adj_body.visible = self._adj_open          # AnimatedSize tweens height
        self.adj_chevron.rotate = ft.Rotate(math.pi if self._adj_open else 0.0)
        self.adj_header.border_radius = self._adj_radius()
        self.adj_body.update()
        self.adj_reveal.update()
        self.adj_chevron.update()
        self.adj_header.update()

    # ---------------------------------------------------------------- settings
    def _on_open_settings(self, _e: ft.ControlEvent) -> None:
        self.show_settings()

    def _on_close_settings(self, _e: ft.ControlEvent) -> None:
        self.show_remote()

    def _on_open_about(self, _e: ft.ControlEvent) -> None:
        self.show_about()

    def _on_open_repo(self, _e: ft.ControlEvent) -> None:
        # Open the public project repository in the user's default browser.
        # webbrowser.open hands the URL straight to the OS handler; Flet's
        # page.launch_url can fall back to copying it to the clipboard on
        # desktop, which isn't what we want here.
        webbrowser.open(screens.REPO_URL)

    def _on_close_about(self, _e: ft.ControlEvent) -> None:
        self.show_settings()

    def _apply_device_info(self) -> None:
        """Fill the About page's value Texts from the cached ``DeviceInfo``.

        Pure (no ``.update()``) so it works whether the page is mounted yet or
        not — callers push updates if the page is live. ``Name``/``Model`` use the
        live values (a rename keeps Name current without re-reading DIS)."""
        di = self._device_info
        if di is None:
            return
        live = {
            "name": (self.name_value_text.value or None),
            "model": (self.client.model.display_name if self.client else di.model),
        }
        for _icon, label, attr in screens.ABOUT_FIELDS:
            value = live[attr] if attr in live else getattr(di, attr, None)
            self.about_values[label].value = str(value) if value else "—"
        self.about_status.visible = False

    async def _load_device_info(self) -> None:
        """Fallback lazy read (only when the connect-time read failed): read the
        speaker's Device Information, cache it, and fill the open About page."""
        if self.client is None:
            return
        di = await self._guard(self.client.device_info)
        if di is None:
            self.about_status.value = "Couldn't read device information."
            self.about_status.update()
            return
        self._device_info = di
        self._apply_device_info()
        for text in self.about_values.values():
            text.update()
        self.about_status.update()

    async def _load_settings_extras(self) -> None:
        """Read the Settings-only speaker state lazily on open: auto-standby
        (PowerMode) and speaker placement (boundary gain). Neither is part of the
        main status() read, so they're fetched here when the tab appears."""
        await self._load_standby()
        await self._load_placement()

    async def _load_standby(self) -> None:
        """Read the speaker's auto-standby (PowerMode) state into the switch."""
        if self.client is None:
            return
        val = await self._guard(lambda: self.client.get_toggle(CH_POWERMODE))
        if val is not None:
            self.standby_sw.value = bool(val)
            self.standby_sw.update()

    async def _load_placement(self) -> None:
        """Read the speaker's placement (boundary gain) into the segmented button."""
        if self.client is None:
            return
        placement = await self._guard(self.client.get_placement)
        if placement is not None:
            self._reflect_placement(placement_name(placement))
            self.placement_seg.update()
            self.placement_hint_text.update()

    def _on_toggle_autoconnect(self, e: ft.ControlEvent) -> None:
        cfg = load_config()
        cfg["auto_connect"] = bool(e.control.value)
        save_config(cfg)

    def _on_toggle_autostart(self, e: ft.ControlEvent) -> None:
        enabled = bool(e.control.value)
        try:
            autostart.set_enabled(enabled)
        except Exception as exc:  # registry/FS write can fail
            # Revert the switch to the real (unchanged) state and tell the user.
            e.control.value = not enabled
            e.control.update()
            self.snack(f"Couldn't update startup setting: {exc}", error=True)

    # ------------------------------------------------------------- system tray
    def _ensure_tray(self, *, on_ready=None, on_fail=None) -> None:
        """Start the tray icon if it isn't already running (no-op otherwise).

        ``on_ready`` / ``on_fail`` (fired from the tray's worker thread) report
        whether the icon actually registered, so a silent autostart can fall
        back to showing the window instead of staying hidden with no icon.
        """
        if self._tray is not None or not TRAY_SUPPORTED:
            if on_fail:
                on_fail()
            return
        # Captured here (on the Flet event loop) so the tray's off-thread menu
        # callbacks can marshal back onto it.
        self._loop = asyncio.get_running_loop()
        self._tray = start_tray(
            icon_path=_icon_path(),
            on_show=lambda: self._loop.call_soon_threadsafe(
                self.page.run_task, bring_to_front, self.page),
            on_quit=lambda: self._loop.call_soon_threadsafe(
                self.page.run_task, self._quit),
            on_ready=on_ready,
            on_fail=on_fail,
        )

    def _remove_tray(self) -> None:
        """Stop and clear the tray icon (with it gone, the X quits the app)."""
        if self._tray is not None:
            self._tray.stop()
            self._tray = None

    async def _quit(self) -> None:
        """The single real-exit path (window X without a tray, or tray Quit).

        Tear the BLE link down cleanly first — without the explicit disconnect
        the process just dies and Windows takes a couple of seconds to release
        the GATT link, so a quick relaunch hits a speaker that's still
        "connected" to the dead session and the reconnect fails (transient
        access error). Bounded by a short timeout so quitting never hangs on a
        slow/stuck disconnect.
        """
        if self.client is not None:
            try:
                await asyncio.wait_for(self.client.disconnect(), timeout=2.0)
            except Exception:  # closing regardless of outcome
                pass
        self._remove_tray()
        await self.page.window.destroy()

    async def _on_window_event(self, e: ft.WindowEvent) -> None:
        """Window-close policy: with a tray, X hides; without one, X quits."""
        if e.type != ft.WindowEventType.CLOSE:
            return
        if self._tray is not None:
            self.page.window.visible = False
            self.page.update()
            return
        await self._quit()

    def _on_toggle_close_to_tray(self, e: ft.ControlEvent) -> None:
        enabled = bool(e.control.value)
        cfg = load_config()
        cfg["close_to_tray"] = enabled
        save_config(cfg)
        # Apply live: starting/stopping the tray flips the close behaviour too,
        # since _on_window_event keys off whether a tray is running.
        if enabled:
            self._ensure_tray()
        else:
            self._remove_tray()

    async def _standby(self, on: bool) -> None:
        await self._guard(lambda: self.client.set_toggle(CH_POWERMODE, on))

    def _on_toggle_standby(self, e: ft.ControlEvent) -> None:
        self.page.run_task(self._standby, bool(e.control.value))

    def _on_rename(self, _e: ft.ControlEvent) -> None:
        field = ft.TextField(
            label="Speaker name", value=self.name_value_text.value or "",
            autofocus=True, border_color=OUTLINE, focused_border_color=SEED)

        def close() -> None:
            self.page.pop_dialog()

        def save(_e: ft.ControlEvent) -> None:
            new = (field.value or "").strip()
            close()
            if new and new != self.name_value_text.value:
                self.page.run_task(self._do_rename, new)

        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Rename speaker"),
            content=field,
            actions=[ft.TextButton("Cancel", on_click=lambda _e: close()),
                     ft.FilledButton("Save", on_click=save)],
            actions_alignment=ft.MainAxisAlignment.END))

    async def _do_rename(self, name: str) -> None:
        if self.client is None:
            return
        await self._guard(lambda: self.client.set_name(name))
        # Mirror the new name everywhere it shows (header + settings row).
        self.model_text.value = name
        self.name_value_text.value = name
        self.page.update()

    def _on_factory_reset(self, _e: ft.ControlEvent) -> None:
        """Confirm (Yes / No) before wiping the speaker — it's irreversible."""
        def close() -> None:
            self.page.pop_dialog()

        def confirm(_e: ft.ControlEvent) -> None:
            close()
            self.page.run_task(self._do_factory_reset)

        self.page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("Factory Reset"),
            content=ft.Text(
                "This erases ALL speaker settings (name, EQ, modes, pairing) and "
                "restarts the speaker. This cannot be undone."),
            actions=[
                ft.TextButton("No", on_click=lambda _e: close()),
                ft.FilledButton(
                    "Yes", on_click=confirm,
                    style=ft.ButtonStyle(bgcolor=ft.Colors.ERROR,
                                         color=ft.Colors.ON_ERROR)),
            ],
            actions_alignment=ft.MainAxisAlignment.END))

    async def _do_factory_reset(self) -> None:
        if self.client is None:
            return

        async def _reset_ok() -> bool:
            # Return an explicit success flag: factory_reset() yields None, which
            # _guard also returns on failure, so we can't distinguish otherwise.
            await self.client.factory_reset()
            return True

        if not await self._guard(_reset_ok):
            return  # the write failed — _guard already showed the error
        # The speaker reboots and drops the link, so tear down our side and go
        # back to the connect screen rather than leaving a dead remote up.
        self.snack("Factory reset sent — the speaker will restart.")
        await self._disconnect()

    async def _playpause(self) -> None:
        # Stateless: fire the toggle command; the speaker handles play vs pause.
        await self._guard(lambda: self.client.play_pause())

    def _on_playpause(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._playpause)

    async def _prev(self) -> None:
        await self._guard(lambda: self.client.prev_track())

    def _on_prev(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._prev)

    async def _next(self) -> None:
        await self._guard(lambda: self.client.next_track())

    def _on_next(self, _e: ft.ControlEvent) -> None:
        self.page.run_task(self._next)


# A module-level handle to the single-instance guard (set in run() when we
# win the lock), so main() can wire its raise-handler to bring the window
# forward when a second launch pings us.
_instance_lock: SingleInstance | None = None


def _signal_shot_ready() -> None:
    """Touch the ready-marker file so the capture script knows to grab the window."""
    path = os.environ.get("KLIPSCH_SHOT_READY")
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("ready")
    except OSError:
        pass


async def _run_shot(page: ft.Page, remote: KlipschRemote, shot: str) -> None:
    """Navigate to one screen for an offline screenshot, reveal the window, and
    write the ready-marker. Drives the demo transport (no hardware)."""
    from . import _demo

    # Pin the window to the real app size (the shot path skips the normal reveal,
    # so without this it would open at Flutter's 1280x720 default).
    page.window.width = 460
    page.window.height = 860
    page.update()
    # Reveal the window (no tray / autostart side-effects in this path).
    try:
        await asyncio.wait_for(page.window.wait_until_ready_to_show(), timeout=10)
        await page.window.center()
    except Exception as exc:
        _diaglog(f"shot: window pre-show skipped ({exc!r})")
    page.window.visible = True
    page.window.focused = True
    page.update()
    try:
        await page.window.to_front()
    except Exception:  # best-effort
        pass

    if shot == "connect":
        remote.show_connect()
    else:
        # Every other screen lives behind a connection — do the (instant) demo
        # connect, which lands on the remote, then branch to the target screen.
        await remote._connect(_demo.DEMO_ADDRESS)
        if shot in ("settings", "settings2"):
            remote.show_settings()
        elif shot == "about":
            remote.show_about()
        elif shot == "equalizer":
            # The equalizer shot shows the Audio Adjustments panel expanded
            # (dynamic bass / night mode), so open it before the grab.
            remote._on_toggle_adj(None)

    # Let the screen settle (fade-through transition + any lazy reads) before the
    # capture script grabs the window. The 'equalizer' and 'settings2' shots are
    # screens scrolled to the bottom — that scroll is done by the capture script
    # driving a real mouse-wheel over the window (flet_desktop ignores
    # programmatic scroll_to mid-transition), so nothing extra to do here.
    await asyncio.sleep(1.0)
    page.update()
    await asyncio.sleep(0.5)
    _signal_shot_ready()
    _diaglog(f"shot: '{shot}' ready")


async def main(page: ft.Page) -> None:
    _diaglog("main: entered")
    # Upgrade any pre-Task-Scheduler (Run-key) autostart entry before we read the
    # setting back for the Settings switch below. Skipped under a screenshot run,
    # which must not touch the machine's real autostart registration.
    if not _SHOT_MODE:
        autostart.migrate_legacy()
    page.title = "Klipsch Remote"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = build_theme()
    # The native window is already hidden at this point (run() launches it with
    # AppView.FLET_APP_HIDDEN), so there's no default-size flash. We keep the
    # control's `visible` in sync with that, size the window, build the first
    # screen, then wait for the window manager, centre it, and reveal it — so the
    # very first thing the user sees is the finished, centred UI.
    page.window.min_width = 380
    page.window.min_height = 560
    page.window.width = 460
    page.window.height = 860
    icon = _icon_path()
    if icon:
        page.window.icon = icon
    page.window.visible = False
    page.padding = 0

    remote = KlipschRemote(page)
    # Screenshot run: drive straight to one screen against the fake transport,
    # reveal the window, signal ready, and stop — none of the tray / autostart /
    # auto-connect machinery below runs.
    if _SHOT_MODE:
        await _run_shot(page, remote, _SHOT)
        return
    # When auto-connect is on, build the "Connecting…" loading screen as the
    # FIRST screen — rendering the connect form first only to immediately
    # replace it makes it flash. Otherwise the connect screen is the start.
    saved = load_saved_address()
    auto = bool(saved and load_config().get("auto_connect"))
    if auto:
        remote.show_connecting(f"Connecting to {saved} …")
    else:
        remote.show_connect()  # builds the first screen, flushes via page.update()

    # Reveal flash-free. The window is held hidden through the whole Python boot
    # by flet's own mechanism: setupDesktop() only calls windowManager.show()
    # when neither hide_window_on_start nor FLET_HIDE_WINDOW_ON_START is set. We
    # ensure it stays hidden on every path —
    #   * native `flet build` exe: the generated lib/main.dart is patched to
    #     hide_window_on_start=true (see build_app.ps1), and the runner skips its
    #     own first-frame Show() (the C++ patch there);
    #   * dev / `flet pack`: AppView.FLET_APP_HIDDEN sets FLET_HIDE_WINDOW_ON_START.
    # So nothing is ever shown until we build the first screen and reveal it here,
    # exactly once: ready -> centre -> let it paint -> show + focus + raise.
    # Bounded so a login-time launch can never hang here forever: if the window
    # manager isn't ready yet (or the call stalls), we time out and carry on —
    # the app still becomes reachable instead of dying as a hidden zombie.
    try:
        await asyncio.wait_for(page.window.wait_until_ready_to_show(), timeout=10)
        await page.window.center()
    except Exception as exc:  # incl. asyncio.TimeoutError
        _diaglog(f"main: window pre-show step skipped ({exc!r})")
    _diaglog("main: window ready")

    # Window-close policy lives on the remote (_on_window_event): when the
    # close-to-tray feature is on, the X hides the window to the tray; when off
    # (or unsupported), it quits. prevent_close lets that handler run first.
    page.window.prevent_close = True
    page.window.on_event = remote._on_window_event

    # Keep the autostart registration current: if launch-on-startup is on,
    # rewrite the entry so its command always points at THIS install. Self-
    # healing — an entry written by an older or since-moved build is refreshed on
    # launch, so startup keeps working without the user re-toggling the setting.
    # On Windows this shells out to schtasks, so run it off the event loop (fire-
    # and-forget) to avoid delaying the first paint.
    def _refresh_autostart() -> None:
        try:
            if autostart.is_enabled():
                autostart.set_enabled(True)
        except Exception:  # a registration write must never matter
            pass
    asyncio.get_running_loop().run_in_executor(None, _refresh_autostart)

    # Reveal the window: size has settled, so show + focus + raise.
    async def _reveal() -> None:
        if page.window.visible:
            return
        await asyncio.sleep(0.15)
        page.window.visible = True
        page.window.focused = True
        page.update()
        try:
            await page.window.to_front()
        except Exception:  # best-effort, varies by platform
            pass

    tray_on = remote.close_to_tray_sw.value
    at_startup = autostart.launched_at_startup()
    silent = tray_on and at_startup
    _diaglog(f"startup: tray_on={tray_on} launched_at_startup={at_startup} "
             f"silent={silent}")

    if not silent:
        # Normal launch: start the tray (if enabled) and show the window.
        if tray_on:
            remote._ensure_tray()
        await _reveal()
    else:
        # Silent autostart: stay hidden in the tray. But NEVER end up hidden with
        # no icon (an unreachable process) — wait for the tray to actually
        # register, and if it doesn't within a grace window (it failed, or the
        # login-time notification area never became ready), reveal the window as
        # a fallback so the app is always reachable.
        loop = asyncio.get_running_loop()
        tray_ready = asyncio.Event()
        outcome = {"shown": False}

        def _on_ready() -> None:
            outcome["shown"] = True
            loop.call_soon_threadsafe(tray_ready.set)

        def _on_fail() -> None:
            loop.call_soon_threadsafe(tray_ready.set)  # wake up -> reveal below

        remote._ensure_tray(on_ready=_on_ready, on_fail=_on_fail)

        async def _hide_or_fallback() -> None:
            # The tray's own shell-ready wait tops out around 60s before it
            # reports on_fail; give it a touch longer so that signal (not this
            # backstop) drives the decision on a slow boot.
            try:
                await asyncio.wait_for(tray_ready.wait(), timeout=70)
            except asyncio.TimeoutError:
                pass
            if outcome["shown"]:
                _diaglog("silent: tray icon registered, staying hidden")
            else:
                _diaglog("silent: tray icon did NOT register, revealing window")
                await _reveal()
        page.run_task(_hide_or_fallback)

    # If a second copy is launched, the running instance is pinged on its
    # listener thread — bring THIS window to the front instead of opening anew.
    if _instance_lock is not None:
        loop = asyncio.get_running_loop()
        _instance_lock.set_raise_handler(
            lambda: loop.call_soon_threadsafe(
                page.run_task, bring_to_front, page))

    if auto:
        page.run_task(remote._auto_connect, saved)


def run() -> None:
    # Single instance (cross-platform): if we can't grab the lock, ask the
    # already-running copy to come forward and quit. If the port is held by some
    # foreign app (handshake fails), fall through and start anyway.
    _diaglog("run: starting")
    global _instance_lock
    # Screenshot runs launch repeatedly in sequence, so they skip the single-
    # instance guard entirely (no lock to contend, nothing to raise/quit).
    if not _SHOT_MODE:
        lock = SingleInstance()
        if lock.acquire():
            _instance_lock = lock
        elif lock.signal_raise():
            _diaglog("run: another instance already running — raised it, exiting")
            return
    _diaglog("run: launching window (hidden)")

    # Start the native window HIDDEN (this sets FLET_HIDE_WINDOW_ON_START for the
    # desktop client), so the OS never paints a default-size white window at the
    # top-left before our UI exists. `main` sizes + centres it, then reveals it.
    # Cross-platform: the window manager honours this on Windows/Linux/macOS.
    try:
        ft.run(main, view=ft.AppView.FLET_APP_HIDDEN)
    finally:
        if _instance_lock is not None:
            _instance_lock.release()


if __name__ == "__main__":
    run()
