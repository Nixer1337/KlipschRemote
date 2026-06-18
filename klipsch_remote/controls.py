"""Control factory — builds every bound Flet control the remote owns.

Split out of ``app.py`` (mirroring ``screens.py``, which holds the screen
assembly): :func:`build_controls` creates each control once and attaches it to the
:class:`~klipsch_remote.app.KlipschRemote` instance ``r``, wiring each to ``r``'s
``_on_*`` handlers. The remote calls this once at construction; the screen builders
in ``screens.py`` then (re)attach these same controls into layouts.

Where ``screens.py`` only *reads* ``r``'s controls, this *populates* them -- it's
the verbose, one-time widget construction lifted wholesale out of the controller
so ``app.py`` reads as behaviour, not 250 lines of layout literals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from klipsch_ble.cli import load_config, load_saved_address
from klipsch_ble.constants import (
    EQ_MAX,
    EQ_MIN,
    MAX_VOLUME_RAW,
    SUB_DB_MAX,
    SUB_DB_MIN,
)

from . import autostart, screens, viewstate
from .theme import CUSTOM, EQ_PRESETS, INPUTS, OUTLINE, SEED, TRANSPORT
from .tray import TRAY_DEFAULT, TRAY_SUPPORTED
from .widgets import VSlider

if TYPE_CHECKING:
    from .app import KlipschRemote


def build_controls(r: KlipschRemote) -> None:
    """Create every control once; later screens just (re)attach them.

    The demo-mode address override stays in the caller (``app``), so this stays
    free of run-mode flags."""
    # --- connect screen ---
    r.paired_dd = ft.Dropdown(
        label="Paired speakers", options=[], expand=True,
        on_select=r._on_pick_paired,
        border_color=OUTLINE, focused_border_color=SEED,
    )
    r.address_tf = ft.TextField(
        label="Address (MAC, or CoreBluetooth UUID on macOS)",
        value=load_saved_address() or "", expand=True,
        border_color=OUTLINE, focused_border_color=SEED,
    )
    # Bottom action bar: secondary (Scan) leading, primary (Connect, filled)
    # trailing — Material's button order. Both expand to share the width.
    r.connect_btn = ft.FilledButton(
        "Connect", icon=ft.Icons.LINK, on_click=r._on_connect,
        expand=True, height=46,
    )
    r.scan_btn = ft.OutlinedButton(
        "Scan the air", icon=ft.Icons.BLUETOOTH_SEARCHING, on_click=r._on_scan,
        expand=True, height=46,
        tooltip="Put the speaker in pairing mode first — hold its Bluetooth "
                "button until it blinks — then scan, otherwise it won't "
                "advertise over BLE.",
    )
    r.refresh_paired_btn = ft.IconButton(
        ft.Icons.REFRESH, tooltip="Re-enumerate paired speakers",
        on_click=r._on_load_paired,
    )
    r.conn_status = ft.Text("", color=ft.Colors.ON_SURFACE_VARIANT)
    r.conn_progress = ft.ProgressRing(visible=False, width=18, height=18)
    # Shown on the dedicated "Connecting…" screen during the connect+read flow.
    r.connecting_status = ft.Text(
        "", color=ft.Colors.ON_SURFACE_VARIANT,
        text_align=ft.TextAlign.CENTER)

    # --- remote screen header (the speaker's applied name) ---
    r.model_text = ft.Text(
        "Klipsch", size=22, weight=ft.FontWeight.BOLD)

    # --- volume + mute ---
    # The speaker icon doubles as the mute toggle: plain when live, red and
    # crossed-out (VOLUME_OFF) when muted.
    r._muted = False
    r.mute_btn = ft.IconButton(
        ft.Icons.VOLUME_UP, tooltip="Mute / unmute", on_click=r._on_mute)
    r.vol_slider = ft.Slider(
        min=0, max=MAX_VOLUME_RAW, divisions=MAX_VOLUME_RAW, value=0,
        label="{value}", expand=True,
        on_change_end=r._on_vol_commit,
    )

    # --- input selector: a 3x2 grid of selectable tiles ---
    # Each tile is the Flutter-recommended Material layering: a Stack of
    #   (1) the selection fill  — bottom, full-bleed, cross-fades on select;
    #   (2) the icon+label      — middle, defines the tile's intrinsic size;
    #   (3) the ink/click layer — top, full-bleed, transparent.
    # Because the fill (1) and the ink surface (3) are both Positioned.fill,
    # the hover/press state-layer is exactly the same size & shape as the
    # selection — no inset, no "cheap" small highlight.
    r.input_tiles: dict[str, ft.Control] = {}
    r._tile_fill: dict[str, ft.Container] = {}
    r._tile_icon: dict[str, ft.Icon] = {}
    r._tile_label: dict[str, ft.Text] = {}
    RADIUS = 14
    for key, label, icon in INPUTS:
        r._tile_icon[key] = ft.Icon(icon, size=26)
        r._tile_label[key] = ft.Text(label, size=12)
        r._tile_fill[key] = ft.Container(
            left=0, top=0, right=0, bottom=0, border_radius=RADIUS,
            # The selection state-layer cross-fades in/out (Material standard
            # short ~150 ms transition) instead of snapping.
            animate=ft.Animation(150, ft.AnimationCurve.EASE_IN_OUT))
        content = ft.Container(
            ft.Column([r._tile_icon[key], r._tile_label[key]],
                      spacing=6, tight=True,
                      horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(vertical=12, horizontal=8),
            alignment=ft.Alignment.CENTER)
        interactive = ft.Container(
            left=0, top=0, right=0, bottom=0, border_radius=RADIUS,
            ink=True, data=key, on_click=r._on_input)
        r.input_tiles[key] = ft.Container(
            ft.Stack([r._tile_fill[key], content, interactive]),
            border_radius=RADIUS, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            expand=True)
    r._selected_input = "tv"

    # --- EQ: vertical sliders + a UI preset picker + reset-to-flat ---
    r.eq_sliders: dict[str, VSlider] = {}
    for ch in ("bass", "mid", "treble"):
        r.eq_sliders[ch] = VSlider(
            lo=EQ_MIN, hi=EQ_MAX, height=190,
            on_commit=lambda v, ch=ch: r._eq_user_commit(ch, v),
        )
    r.eq_preset_dd = ft.Dropdown(
        value="Flat", expand=True,
        options=([ft.DropdownOption(key=CUSTOM, text=CUSTOM)]
                 + [ft.DropdownOption(key=n, text=n) for n in EQ_PRESETS]),
        on_select=r._on_eq_preset,
        border_color=OUTLINE, focused_border_color=SEED,
    )
    r.eq_reset_btn = ft.IconButton(
        ft.Icons.REFRESH, tooltip="Reset EQ to flat", on_click=r._on_eq_reset)

    # --- subwoofer: a level slider (dB) + phase-invert / mute toggles ---
    # Sub Level is written as a channel-volume command; the speaker reports
    # it back via SubStatus, so the slider reflects the real value on load.
    r.sub_level_slider = ft.Slider(
        min=SUB_DB_MIN, max=SUB_DB_MAX, divisions=SUB_DB_MAX - SUB_DB_MIN,
        value=0, label="{value} dB", expand=True,
        on_change_end=r._on_sub_level_commit,
    )
    r.subinvert_sw = ft.Switch(value=False, data="subinvert",
                               on_change=r._on_sub_toggle)
    # Sub Mute mirrors the Volume card's mute control: the speaker icon to the
    # left of the level slider doubles as the toggle — plain when live, red
    # and crossed-out (VOLUME_OFF) when muted — instead of a separate switch.
    r._sub_muted = False
    r.sub_mute_btn = ft.IconButton(
        ft.Icons.VOLUME_UP, tooltip="Mute / unmute the subwoofer",
        on_click=r._on_sub_mute)
    # Detection state, shown next to the "Subwoofer" section title (not as a
    # cheap line in the card body): empty when a sub is present, "Not detected"
    # otherwise — the label that explains why the group below is greyed out.
    r.sub_section_status = ft.Text(
        "", size=12, italic=True, color=ft.Colors.ON_SURFACE_VARIANT)
    # The current sub level in dB, shown trailing on the "Sub Level" row.
    r.sub_level_value_text = ft.Text(
        "0 dB", color=ft.Colors.ON_SURFACE_VARIANT)

    # --- speaker placement (boundary gain) — Settings ---
    # A Material 3 segmented button: one mutually-exclusive choice of where
    # the speaker sits, which sets how much bass it adds back (CH_BOUNDARY_
    # GAIN). Segment values are the placement names (corner/wall/open) so
    # they pass straight to client.set_placement. WALL is the speaker's
    # default until the real value is read once at connect (_load_state).
    # NB: `selected` MUST be a list, not a set — Flet msgpack-serializes the
    # control tree and a set raises "can not serialize 'set' object".
    r._placement = "wall"
    r.placement_seg = ft.SegmentedButton(
        allow_multiple_selection=False, allow_empty_selection=False,
        show_selected_icon=False, selected=["wall"],
        on_change=r._on_placement,
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
    r.placement_hint_text = ft.Text(
        viewstate.placement_hint("wall"), size=11,
        color=ft.Colors.ON_SURFACE_VARIANT)

    # --- modes (Audio Adjustments collapsible) ---
    r.dynbass_sw = ft.Switch(value=False, data="dynamic_bass",
                             on_change=r._on_toggle)
    r.night_sw = ft.Switch(value=False, data="night",
                           on_change=r._on_toggle)
    r._adj_open = False
    # A single chevron that rotates 180° on expand (Material expansion motion)
    # rather than swapping the glyph.
    r.adj_chevron = ft.Icon(
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
    r.play_btn = ft.Container(
        ft.Row([ft.Icon(ft.Icons.PLAY_ARROW, size=26, color=TRANSPORT),
                ft.Icon(ft.Icons.PAUSE, size=23, color=TRANSPORT)],
               spacing=0, tight=True,
               alignment=ft.MainAxisAlignment.CENTER,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        width=68, height=56, border_radius=28, ink=True,
        alignment=ft.Alignment.CENTER, on_click=r._on_playpause,
        tooltip="Play / pause",
    )
    r.prev_btn = ft.IconButton(ft.Icons.SKIP_PREVIOUS, tooltip="Previous",
                               icon_size=30, icon_color=TRANSPORT,
                               on_click=r._on_prev)
    r.next_btn = ft.IconButton(ft.Icons.SKIP_NEXT, tooltip="Next",
                               icon_size=30, icon_color=TRANSPORT,
                               on_click=r._on_next)

    # --- settings ---
    # The current speaker name, shown trailing on the Settings > Name row.
    r.name_value_text = ft.Text(
        "", italic=True, color=ft.Colors.ON_SURFACE_VARIANT)
    # App-level: reconnect to the saved speaker automatically on next launch.
    r.autoconnect_sw = ft.Switch(
        value=bool(load_config().get("auto_connect", False)),
        on_change=r._on_toggle_autoconnect)
    # App-level (Windows only): close-to-tray. When on, the window's X hides
    # the app to the system tray (reveal / Quit from the tray icon); when
    # off, there's no tray icon and the X quits. The row is only shown on a
    # tray-capable platform (see show_settings); the value is forced False
    # elsewhere so the close handler always takes the plain-quit path.
    r.close_to_tray_sw = ft.Switch(
        value=TRAY_SUPPORTED and bool(
            load_config().get("close_to_tray", TRAY_DEFAULT)),
        on_change=r._on_toggle_close_to_tray)
    # App-level: launch on system startup. The OS registration is the source
    # of truth (no config key) — read it back so the switch always matches
    # the real state, even if it was changed outside the app.
    r.autostart_sw = ft.Switch(
        value=autostart.is_supported() and autostart.is_enabled(),
        on_change=r._on_toggle_autostart)
    # Speaker-level: auto-standby (sleep after inactivity) — PowerMode char.
    r.standby_sw = ft.Switch(value=True, on_change=r._on_toggle_standby)

    # --- About page: one bound value Text per read-only DIS field. Filled
    # from a device_info() read when the page opens; "—" until then. ---
    r.about_values = {
        label: ft.Text("—", selectable=True,
                       color=ft.Colors.ON_SURFACE_VARIANT)
        for _icon, label, _attr in screens.ABOUT_FIELDS
    }
    r.about_status = ft.Text(
        "Reading device information…", size=12,
        color=ft.Colors.ON_SURFACE_VARIANT)
