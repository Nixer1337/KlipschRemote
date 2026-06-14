"""Visual identity for the Klipsch Remote UI: palette, input list, EQ presets,
and the fully-specified Material 3 dark theme.

Split out of ``app.py`` so the appearance/config constants live in one place;
nothing here depends on the rest of the app (one-way: ``app`` and ``widgets``
import from here, never the reverse).
"""

from __future__ import annotations

import flet as ft

# Physical inputs of the powered line (skip OFF — power-off is unreliable), laid
# out as a 3x2 tile grid in the original app's order, each (name, label, icon).
INPUTS: list[tuple[str, str, str]] = [
    ("tv", "TV", ft.Icons.TV),
    ("bluetooth", "Bluetooth", ft.Icons.BLUETOOTH),
    ("optical", "Optical", ft.Icons.SETTINGS_INPUT_SVIDEO),
    ("usb", "USB", ft.Icons.USB),
    ("aux", "Analog", ft.Icons.CABLE),
    ("phono", "Phono", ft.Icons.ALBUM),
]
INPUT_KEYS = {key for key, _, _ in INPUTS}

# UI-level equalizer presets (the speaker's own preset characteristic is
# unsupported on this line and errors, so a preset just sets the three bands).
# values are (bass, mid, treble) in the -10..+6 range.
CUSTOM = "Custom"
# Flat / Vocal / Bass / Rock reproduce the original Klipsch app's presets
# exactly (bass, mid, treble); "Boom" is our own all-in-on-the-low-end mode —
# bass pinned to the maximum, mid and treble pulled to the minimum.
EQ_PRESETS: dict[str, tuple[int, int, int]] = {
    "Flat": (0, 0, 0),
    "Vocal": (-3, 6, 0),
    "Bass": (6, 0, 0),
    "Rock": (3, -1, 3),
    "Boom": (6, -10, -10),
}

# Classic Google dark palette: light-blue accent on neutral grey surfaces.
SEED = "#8AB4F8"     # Google blue (dark-mode accent)
OUTLINE = "#5F6368"  # Google grey 600 — field/combobox borders (never black)
TRANSPORT = "#E8EAED"  # Google grey 200 — shared colour for transport buttons


def build_theme() -> ft.Theme:
    """The app's Material 3 dark theme (assigned to ``page.theme``)."""
    return ft.Theme(
        use_material3=True,
        # Fully-specified Google dark scheme (no seed — a seed would override
        # this and re-tint the surfaces). Values mirror Google's own dark theme:
        # #202124 neutral-grey surfaces, #8AB4F8 blue accent, #5F6368 grey
        # outlines (NOT black), #F28B82 red. Every token is set so nothing falls
        # back to a Flutter default.
        color_scheme=ft.ColorScheme(
            # Primary — Google blue accent with dark text/icons on it.
            primary="#8AB4F8",
            on_primary="#202124",
            primary_container="#0842A0",
            on_primary_container="#D3E3FD",
            inverse_primary="#1A73E8",
            # Secondary / tertiary — blue-grey companions used by chips, etc.
            secondary="#C2CCDB",
            on_secondary="#253140",
            secondary_container="#3B4858",
            on_secondary_container="#DCE6F7",
            tertiary="#A8C7FA",
            on_tertiary="#0A305E",
            tertiary_container="#284777",
            on_tertiary_container="#D3E3FD",
            # Error — Google red (dark).
            error="#F28B82",
            on_error="#601410",
            error_container="#8C1D18",
            on_error_container="#F9DEDC",
            # Neutral surfaces — Google grey family, stepping up in elevation.
            surface="#202124",
            on_surface="#E8EAED",
            on_surface_variant="#C4C7C5",
            surface_dim="#1B1B1D",
            surface_bright="#3A3B3E",
            surface_container_lowest="#161618",
            surface_container_low="#1E1F22",
            surface_container="#28292C",
            surface_container_high="#303134",
            surface_container_highest="#3C3D40",
            surface_tint="#8AB4F8",
            # Outlines — Google grey 600 / 800. This is the combobox/field border.
            outline="#5F6368",
            outline_variant="#3C4043",
            inverse_surface="#E8EAED",
            on_inverse_surface="#202124",
            shadow="#000000",
            scrim="#000000",
        ),
        # A slim, rounded Material scrollbar that floats in the content gutter
        # (the scroll viewport keeps a right inset) instead of sitting on the cards.
        scrollbar_theme=ft.ScrollbarTheme(
            thumb_visibility=True, interactive=True, thickness=8, radius=8,
            thumb_color=ft.Colors.with_opacity(0.45, ft.Colors.ON_SURFACE),
        ),
        # The volume slider keeps integer divisions (so the value snaps to whole
        # steps) but the per-division tick marks are hidden — the original app's
        # track is a clean, continuous line, not a dotted ruler.
        slider_theme=ft.SliderTheme(
            active_tick_mark_color=ft.Colors.TRANSPARENT,
            inactive_tick_mark_color=ft.Colors.TRANSPARENT,
            # Also hide ticks in the DISABLED state — Flutter draws those with a
            # separate colour, so the (divisions-based) sub slider would otherwise
            # show a dotted ruler whenever the group is greyed out (no sub).
            disabled_active_tick_mark_color=ft.Colors.TRANSPARENT,
            disabled_inactive_tick_mark_color=ft.Colors.TRANSPARENT,
        ),
    )
