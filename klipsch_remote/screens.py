"""Screen builders — the presentation layer of the remote.

Each function takes the :class:`~klipsch_remote.app.KlipschRemote` instance and
returns the list of controls for one screen; the instance owns all the bound
controls (built in ``_build_controls``), the BLE state, and the ``_on_*`` event
handlers these layouts wire up. Navigation itself (the AnimatedSwitcher swap in
``_present``) and the small per-screen orchestration (scroll mode, status text,
the post-open ``after`` callback) stay on the class — only the view assembly
lives here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

from . import autostart
from .theme import INPUTS
from .tray import TRAY_SUPPORTED

if TYPE_CHECKING:  # avoid a circular import — app imports this module
    from .app import KlipschRemote


# The About page: every read-only field the speaker exposes, in display order.
# Each entry is (Material icon, row label, DeviceInfo attribute). The app builds
# one bound value Text per label (``r.about_values``) and fills them from a
# ``device_info()`` read when the page opens.
# The public project repository, opened from Settings > About this app.
REPO_URL = "https://github.com/Nixer1337/KlipschRemote"


ABOUT_FIELDS: list[tuple[str, str, str]] = [
    (ft.Icons.SPEAKER, "Name", "name"),
    (ft.Icons.CATEGORY, "Model", "model"),
    (ft.Icons.BUSINESS, "Manufacturer", "manufacturer"),
    (ft.Icons.MEMORY, "Firmware", "firmware_revision"),
    (ft.Icons.TERMINAL, "MCU Firmware", "software_revision"),
    (ft.Icons.DEVELOPER_BOARD, "Hardware", "hardware_revision"),
    (ft.Icons.NUMBERS, "Model number", "model_number"),
    (ft.Icons.QR_CODE_2, "Serial number", "serial_number"),
    (ft.Icons.LAN, "MAC Address", "mac_address"),
    (ft.Icons.FINGERPRINT, "System ID", "system_id"),
]


def connect_controls(r: KlipschRemote) -> list[ft.Control]:
    """The connect screen: a top form (paired picker + address) and a fixed
    bottom action bar (Scan · Connect)."""
    form = ft.Column(
        [
            ft.Row([ft.Icon(ft.Icons.SPEAKER, size=30),
                    ft.Text("Klipsch Remote", size=24,
                            weight=ft.FontWeight.BOLD)], spacing=12),
            ft.Text("Connect to a powered speaker (The Fives / Sevens / Nines).",
                    color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Divider(),
            ft.Row([r.paired_dd, r.refresh_paired_btn],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            r.address_tf,
        ],
        spacing=16, tight=True,
    )
    # Bottom action bar pinned to the foot of the screen, helper/status line
    # just above it. The spacer Container pushes it all down.
    actions = ft.Column(
        [
            ft.Row([ft.Container(r.conn_status, expand=True),
                    r.conn_progress], spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([r.scan_btn, r.connect_btn], spacing=12),
        ],
        spacing=12, tight=True,
    )
    screen = ft.Container(
        ft.Column([form, ft.Container(expand=True), actions], expand=True),
        padding=16, expand=True,
    )
    return [screen]


def connecting_controls(r: KlipschRemote) -> list[ft.Control]:
    """A centered Material loading screen shown while connecting + reading
    state: an indeterminate progress ring over a title and live status."""
    screen = ft.Column(
        [
            ft.Container(expand=True),
            ft.ProgressRing(width=48, height=48, stroke_width=4),
            ft.Container(
                ft.Column(
                    [ft.Text("Connecting", size=20,
                             weight=ft.FontWeight.BOLD,
                             text_align=ft.TextAlign.CENTER),
                     r.connecting_status],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=6, tight=True),
                padding=ft.Padding.only(top=24, left=24, right=24)),
            ft.Container(expand=True),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER, expand=True,
    )
    return [screen]


def remote_controls(r: KlipschRemote) -> list[ft.Control]:
    """The main remote: header + a scroll viewport of cards (volume, input,
    playback, EQ, audio adjustments, settings entry)."""
    def card(icon: str, title: str, *content: ft.Control) -> ft.Card:
        # 16dp icon→title gutter so every card title shares the same left rail
        # as the adjustment/settings list-item labels (icon 16 + 20 + 16).
        head = ft.Row([ft.Icon(icon, size=20),
                       ft.Text(title, weight=ft.FontWeight.BOLD, size=16)],
                      spacing=16)
        return ft.Card(ft.Container(
            ft.Column([head, *content], spacing=12, tight=True), padding=16))

    header = ft.Row(
        [
            # Spacer balancing the refresh button so the title stays centred.
            # No back-arrow here: disconnect lives in Settings now — an arrow in
            # this spot was too easy to hit by reflex, dropping the connection.
            ft.Container(width=48),
            ft.Container(r.model_text, expand=True,
                         alignment=ft.Alignment.CENTER),
            ft.IconButton(ft.Icons.REFRESH, tooltip="Refresh status",
                          on_click=r._on_refresh),
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    volume = card(
        ft.Icons.VOLUME_UP, "Volume",
        ft.Row([r.mute_btn, r.vol_slider],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
    )

    # Input: two rows of three equal-width tiles.
    tile_rows = [
        ft.Row([r.input_tiles[k] for k, _, _ in INPUTS[i:i + 3]], spacing=8)
        for i in (0, 3)
    ]
    inputs = card(ft.Icons.INPUT, "Input", *tile_rows)

    def band(ch: str) -> ft.Column:
        return ft.Column(
            [r.eq_sliders[ch].control, ft.Text(ch.capitalize(), size=12)],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8,
        )

    eq = card(
        ft.Icons.GRAPHIC_EQ, "Equalizer",
        ft.Row([r.eq_preset_dd, r.eq_reset_btn],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Container(
            ft.Row([band("bass"), band("mid"), band("treble")],
                   alignment=ft.MainAxisAlignment.SPACE_AROUND),
            padding=ft.Padding.only(top=8)),
    )

    transport = card(
        ft.Icons.PLAY_CIRCLE_OUTLINE, "Playback",
        ft.Row([r.prev_btn, r.play_btn, r.next_btn],
               alignment=ft.MainAxisAlignment.CENTER),
    )

    # Audio Adjustments: a custom collapsible (avoids ExpansionTile's
    # top/bottom divider lines), expandable by tapping the header.
    def adj_row(icon: str, label: str, desc: str, sw: ft.Switch) -> ft.Container:
        # A two-line Material list item: 16dp horizontal inset, 12dp vertical,
        # 16dp gutter between leading icon / text / trailing switch.
        return ft.Container(
            ft.Row(
                [ft.Icon(icon, size=20),
                 ft.Column(
                     [ft.Text(label),
                      ft.Text(desc, size=11,
                              color=ft.Colors.ON_SURFACE_VARIANT)],
                     spacing=2, tight=True, expand=True),
                 sw],
                vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=16),
            padding=ft.Padding.symmetric(vertical=12, horizontal=16),
        )

    # Full-width header: the on_click/ink state-layer spans the whole card
    # edge-to-edge (Material list-item behaviour) instead of a small inset
    # rounded rect. Its corners match the card's so the ripple is clipped to
    # the rounded shape; bottom corners square off while expanded.
    r.adj_header = ft.Container(
        ft.Row([ft.Icon(ft.Icons.TUNE, size=20),
                ft.Text("Audio Adjustments", weight=ft.FontWeight.BOLD,
                        size=16, expand=True),
                r.adj_chevron], spacing=16),
        on_click=r._on_toggle_adj, ink=True,
        border_radius=r._adj_radius(),
        padding=ft.Padding.symmetric(vertical=14, horizontal=16),
    )
    # Rows are full-width list items (own padding); 0 spacing between them and
    # a small bottom inset so the last row doesn't hug the card edge.
    r.adj_body = ft.Container(
        ft.Column(
            [adj_row(ft.Icons.GRAPHIC_EQ, "Dynamic Bass",
                     "Boosts bass at lower volume levels for a fuller sound.",
                     r.dynbass_sw),
             adj_row(ft.Icons.DARK_MODE, "Night Mode",
                     "Compresses the dynamic range so loud sounds are softer "
                     "and quiet sounds are cleaner at low volume.",
                     r.night_sw)],
            spacing=0, tight=True),
        padding=ft.Padding.only(bottom=4),
        visible=r._adj_open,
    )
    # AnimatedSize: the card grows/shrinks smoothly as the body shows/hides
    # (the ExpansionTile reveal), with content clipped during the tween.
    r.adj_reveal = ft.Container(
        r.adj_body, clip_behavior=ft.ClipBehavior.HARD_EDGE,
        animate_size=ft.Animation(220, ft.AnimationCurve.EASE_IN_OUT),
    )
    adjustments = ft.Card(
        ft.Column([r.adj_header, r.adj_reveal], spacing=0, tight=True),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
    )

    # Settings entry — a single full-width tappable list-item (gear · label ·
    # chevron) that opens the Settings screen, mirroring the original app.
    settings_entry = ft.Card(
        ft.Container(
            ft.Row([ft.Icon(ft.Icons.SETTINGS, size=20),
                    ft.Text("Settings", weight=ft.FontWeight.BOLD, size=16,
                            expand=True),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, size=22)], spacing=16),
            on_click=r._on_open_settings, ink=True,
            border_radius=12,
            padding=ft.Padding.symmetric(vertical=14, horizontal=16)),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
    )

    # Built only after the state is read (see _connect), so controls render
    # pre-populated. The header stays fixed; the cards live in a scroll
    # viewport whose content is inset on the right, so the Material scrollbar
    # floats in that gutter instead of overlapping the cards. STRETCH gives
    # every card the full content width.
    body = ft.Column(
        [volume, inputs, transport, eq, adjustments, settings_entry],
        spacing=12, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    # Material center-aligned top app bar: 4dp horizontal inset (the
    # IconButton's own padding lands the glyph optically at ~16, on the card
    # rail) and a balanced 8dp vertical band that clears the OS title bar
    # above and separates from the content below.
    header_bar = ft.Container(
        header, padding=ft.Padding.symmetric(horizontal=4, vertical=8))
    scroller = ft.Column(
        [ft.Container(body, padding=ft.Padding.only(
            left=16, right=16, top=4, bottom=20))],
        scroll=ft.ScrollMode.AUTO, expand=True,
    )
    return [header_bar, scroller]


def settings_controls(r: KlipschRemote) -> list[ft.Control]:
    """The Settings screen: rename, auto-connect, startup/tray, power, sub,
    factory reset."""
    def section(title: str) -> ft.Control:
        # Group label, aligned with the card content inset (16) below it.
        return ft.Container(
            ft.Text(title, weight=ft.FontWeight.BOLD, size=14,
                    color=ft.Colors.ON_SURFACE_VARIANT),
            padding=ft.Padding.only(left=16, top=16, bottom=4))

    def row(icon: str, label: str, trailing: ft.Control, *,
            desc: str | None = None, on_click=None) -> ft.Container:
        # Same Material list-item metrics as the remote's adjustment rows:
        # 16dp inset, 12dp vertical, 16dp gutter, 2dp label/description gap.
        texts = [ft.Text(label)]
        if desc:
            texts.append(ft.Text(desc, size=11,
                                 color=ft.Colors.ON_SURFACE_VARIANT))
        return ft.Container(
            ft.Row([ft.Icon(icon, size=20),
                    ft.Column(texts, spacing=2, tight=True, expand=True),
                    trailing],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=16),
            on_click=on_click, ink=bool(on_click),
            padding=ft.Padding.symmetric(vertical=12, horizontal=16))

    def grouped(*rows: ft.Control) -> ft.Card:
        # Rows stacked with hairline dividers between them, ripple clipped.
        kids: list[ft.Control] = []
        for i, row_ in enumerate(rows):
            if i:
                kids.append(ft.Divider(height=1, thickness=1))
            kids.append(row_)
        return ft.Card(ft.Column(kids, spacing=0, tight=True),
                       clip_behavior=ft.ClipBehavior.ANTI_ALIAS)

    general_rows = [
        row(ft.Icons.PERSON, "Name",
            ft.Row([r.name_value_text,
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, size=22)],
                   spacing=4, tight=True),
            on_click=r._on_rename),
        row(ft.Icons.BLUETOOTH_CONNECTED, "Connect on launch",
            r.autoconnect_sw,
            desc="Reconnect to this speaker automatically when the app starts."),
    ]
    # Close-to-tray is Windows-only, so the row appears only where the tray
    # is actually available.
    if TRAY_SUPPORTED:
        general_rows.append(
            row(ft.Icons.MINIMIZE, "Close to tray", r.close_to_tray_sw,
                desc="Closing the window hides the app to the system tray "
                     "instead of quitting. Right-click the tray icon to Quit."))
    # Launch-on-startup is cross-platform (Windows/macOS/Linux).
    if autostart.is_supported():
        general_rows.append(
            row(ft.Icons.LAUNCH, "Launch on startup", r.autostart_sw,
                desc="Automatically start Klipsch Remote when you sign in."))
    general = grouped(*general_rows)
    power = grouped(
        row(ft.Icons.POWER_SETTINGS_NEW, "Auto Standby", r.standby_sw,
            desc="Let the speaker sleep automatically after a period of silence."),
    )
    # Subwoofer: the level slider + two toggle rows. When no sub is connected
    # the WHOLE card is disabled (non-interactive) and dimmed to Material's
    # 38% disabled-content opacity — a single uniform greyed-out group, the
    # standard Material way to show an inactive section — rather than dimming
    # each control on its own. The state comes from the cached status read
    # (applied just below + in _reflect_sub_detected); the "Not detected"
    # label lives up on the section title.
    sub_level_block = ft.Container(
        ft.Column(
            [ft.Row([ft.Text("Sub Level", expand=True),
                     r.sub_level_value_text]),
             ft.Row([r.sub_mute_btn, r.sub_level_slider],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER)],
            spacing=4, tight=True),
        padding=ft.Padding.symmetric(vertical=8, horizontal=16))
    r.sub_card = ft.Card(
        ft.Column(
            [sub_level_block,
             ft.Divider(height=1, thickness=1),
             row(ft.Icons.SWAP_VERT, "Phase Invert", r.subinvert_sw)],
            spacing=0, tight=True),
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        # Smooth fade when the group enables/disables (Material motion).
        animate_opacity=ft.Animation(150, ft.AnimationCurve.EASE_IN_OUT))
    # Apply the cached detection state (from the last status read) right away,
    # so the tab opens already correct — no BLE read, no visible re-detect.
    r.sub_card.disabled = r._sub_detected is not True
    r.sub_card.opacity = 1.0 if r._sub_detected is True else 0.38
    # Section title with the detection status trailing it.
    sub_section_header = ft.Container(
        ft.Row([ft.Text("Subwoofer", weight=ft.FontWeight.BOLD, size=14,
                        color=ft.Colors.ON_SURFACE_VARIANT),
                r.sub_section_status],
               spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.Padding.only(left=16, top=16, bottom=4))
    # Factory Reset — destructive, so it's rendered in the error colour (red
    # icon/label/chevron) and gated behind a Yes/No confirmation dialog,
    # mirroring the original app's Settings > Product entry.
    factory_row = ft.Container(
        ft.Row([ft.Icon(ft.Icons.RESTART_ALT, size=20, color=ft.Colors.ERROR),
                ft.Column(
                    [ft.Text("Factory Reset", color=ft.Colors.ERROR),
                     ft.Text("Erase all settings and restart the speaker.",
                             size=11, color=ft.Colors.ON_SURFACE_VARIANT)],
                    spacing=2, tight=True, expand=True),
                ft.Icon(ft.Icons.CHEVRON_RIGHT, size=22, color=ft.Colors.ERROR)],
               vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=16),
        on_click=r._on_factory_reset, ink=True,
        padding=ft.Padding.symmetric(vertical=12, horizontal=16))
    # About — opens a read-only page with everything the speaker reports
    # (firmware / serial / hardware & software revision / …).
    about_row = row(ft.Icons.INFO_OUTLINE, "About",
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, size=22),
                    desc="Firmware version, serial number and device details.",
                    on_click=r._on_open_about)
    product = grouped(about_row, factory_row)
    # Source code — opens the public repository in the browser. The trailing
    # open-in-new glyph (rather than a chevron) signals it leaves the app.
    repo_row = row(ft.Icons.CODE, "Source code",
                   ft.Icon(ft.Icons.OPEN_IN_NEW, size=20,
                           color=ft.Colors.ON_SURFACE_VARIANT),
                   desc="View the project on GitHub.",
                   on_click=r._on_open_repo)
    about_app = grouped(repo_row)
    # Disconnect — the deliberate way to drop the BLE link. A full-width filled
    # button in the error colour at the very foot of Settings, mirroring the
    # connect screen's primary button. (The remote no longer carries a top-bar
    # back-arrow for this; it was too easy to hit when reaching for "back".)
    disconnect_btn = ft.Container(
        ft.Row([ft.FilledButton(
            "Disconnect", icon=ft.Icons.LINK_OFF, on_click=r._on_disconnect,
            expand=True, height=46,
            style=ft.ButtonStyle(bgcolor=ft.Colors.ERROR,
                                 color=ft.Colors.ON_ERROR))]),
        padding=ft.Padding.only(top=12))

    header = ft.Row(
        [ft.IconButton(ft.Icons.ARROW_BACK_IOS_NEW, tooltip="Back",
                       on_click=r._on_close_settings),
         ft.Container(ft.Text("Settings", size=22, weight=ft.FontWeight.BOLD),
                      expand=True, alignment=ft.Alignment.CENTER),
         # Spacer matching the back button so the title stays centred.
         ft.Container(width=48)],
        vertical_alignment=ft.CrossAxisAlignment.CENTER)
    # Material center-aligned top app bar: 4dp horizontal inset (the
    # IconButton's own padding lands the glyph optically at ~16, on the card
    # rail) and a balanced 8dp vertical band that clears the OS title bar
    # above and separates from the content below.
    header_bar = ft.Container(
        header, padding=ft.Padding.symmetric(horizontal=4, vertical=8))

    body = ft.Column(
        [section("General"), general,
         sub_section_header, r.sub_card,
         section("Power Management"), power,
         section("Product"), product,
         section("About this app"), about_app,
         disconnect_btn],
        spacing=8, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    scroller = ft.Column(
        [ft.Container(body, padding=ft.Padding.only(
            left=16, right=16, top=4, bottom=20))],
        scroll=ft.ScrollMode.AUTO, expand=True)
    return [header_bar, scroller]


def about_controls(r: KlipschRemote) -> list[ft.Control]:
    """The About page: every read-only field the speaker reports, each a Material
    list item (icon · label · value). Values are filled from a ``device_info()``
    read after the page opens (see ``KlipschRemote._load_device_info``); they show
    a placeholder dash until then."""
    def info_row(icon: str, label: str, value: ft.Control) -> ft.Container:
        return ft.Container(
            ft.Row([ft.Icon(icon, size=20),
                    ft.Text(label, expand=True),
                    value],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=16),
            padding=ft.Padding.symmetric(vertical=12, horizontal=16))

    kids: list[ft.Control] = []
    for i, (icon, label, _attr) in enumerate(ABOUT_FIELDS):
        if i:
            kids.append(ft.Divider(height=1, thickness=1))
        kids.append(info_row(icon, label, r.about_values[label]))
    card = ft.Card(ft.Column(kids, spacing=0, tight=True),
                   clip_behavior=ft.ClipBehavior.ANTI_ALIAS)

    header = ft.Row(
        [ft.IconButton(ft.Icons.ARROW_BACK_IOS_NEW, tooltip="Back",
                       on_click=r._on_close_about),
         ft.Container(ft.Text("About", size=22, weight=ft.FontWeight.BOLD),
                      expand=True, alignment=ft.Alignment.CENTER),
         ft.Container(width=48)],
        vertical_alignment=ft.CrossAxisAlignment.CENTER)
    header_bar = ft.Container(
        header, padding=ft.Padding.symmetric(horizontal=4, vertical=8))

    body = ft.Column(
        [ft.Container(r.about_status,
                      padding=ft.Padding.only(left=16, top=8, bottom=4)),
         card],
        spacing=8, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    scroller = ft.Column(
        [ft.Container(body, padding=ft.Padding.only(
            left=16, right=16, top=4, bottom=20))],
        scroll=ft.ScrollMode.AUTO, expand=True)
    return [header_bar, scroller]
