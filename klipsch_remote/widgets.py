"""Custom Flet widgets that Material doesn't provide out of the box.

Self-contained controls (no dependency on the app's state) — they take their
data and callbacks at construction and expose a ``.control`` to mount.
"""

from __future__ import annotations

import flet as ft

from .theme import SEED


class VSlider:
    """A vertical slider (Flet's Material slider is horizontal-only).

    Renders a dim track, an accent fill from the bottom, and a draggable thumb in
    a fixed Stack; tap/drag map the pointer's local *y* to an integer in
    ``[lo, hi]``. ``on_commit(value)`` fires on tap and drag-end only, so the BLE
    write doesn't run on every pixel of a drag.
    """

    THUMB = 22
    TRACK_W = 6
    # Value indicator (the bubble that rides the thumb while dragging). Mirrors
    # the built-in Material Slider's `label=` paddle: primary fill, on-primary
    # labelMedium text, shown only during interaction.
    BUBBLE_W = 36
    BUBBLE_H = 24
    BUBBLE_GAP = 8

    def __init__(self, *, lo: int, hi: int, on_commit, height: int = 200,
                 width: int = 48) -> None:
        self.lo, self.hi, self.on_commit = lo, hi, on_commit
        self.height, self.width = height, width
        self.value = lo
        cx = width / 2
        self._track = ft.Container(
            width=self.TRACK_W, height=height, border_radius=self.TRACK_W / 2,
            bgcolor=ft.Colors.with_opacity(0.22, ft.Colors.ON_SURFACE),
            left=cx - self.TRACK_W / 2, top=0)
        self._fill = ft.Container(
            width=self.TRACK_W, height=0, border_radius=self.TRACK_W / 2,
            bgcolor=SEED, left=cx - self.TRACK_W / 2, bottom=0)
        self._thumb = ft.Container(
            width=self.THUMB, height=self.THUMB, border_radius=self.THUMB / 2,
            bgcolor=SEED,
            shadow=ft.BoxShadow(blur_radius=4, color=ft.Colors.with_opacity(0.4, ft.Colors.BLACK)),
            left=cx - self.THUMB / 2, top=0)
        self._bubble_text = ft.Text(
            "", size=12, weight=ft.FontWeight.W_500, color="#202124",  # on_primary
            text_align=ft.TextAlign.CENTER)
        self._bubble = ft.Container(
            content=self._bubble_text, bgcolor=SEED,
            width=self.BUBBLE_W, height=self.BUBBLE_H, border_radius=10,
            alignment=ft.Alignment.CENTER, visible=False,
            left=cx - self.BUBBLE_W / 2, top=0,
            shadow=ft.BoxShadow(blur_radius=8, color=ft.Colors.with_opacity(0.4, ft.Colors.BLACK)))
        self.control = ft.GestureDetector(
            # clip NONE so the bubble can float above the track without being cut.
            content=ft.Stack([self._track, self._fill, self._thumb, self._bubble],
                             width=width, height=height,
                             clip_behavior=ft.ClipBehavior.NONE),
            on_tap_down=self._on_tap, on_tap_up=self._on_release,
            on_pan_start=self._on_drag, on_pan_update=self._on_drag,
            on_pan_end=self._on_end,
        )
        self.set_value(lo, update=False)

    @property
    def _travel(self) -> float:
        return self.height - self.THUMB

    def set_value(self, value: float, *, update: bool = True) -> None:
        self.value = max(self.lo, min(self.hi, round(value)))
        f = (self.value - self.lo) / (self.hi - self.lo)
        self._thumb.top = (1 - f) * self._travel
        self._fill.height = self.height - self._thumb.top - self.THUMB / 2
        # Ride the bubble just above the thumb and show the signed level.
        self._bubble_text.value = f"{self.value:+d}" if self.value else "0"
        self._bubble.top = self._thumb.top - self.BUBBLE_H - self.BUBBLE_GAP
        if update:
            self._thumb.update()
            self._fill.update()
            self._bubble.update()

    def _value_from_y(self, y: float) -> int:
        f = 1 - (y - self.THUMB / 2) / self._travel
        f = max(0.0, min(1.0, f))
        return round(self.lo + f * (self.hi - self.lo))

    def _set_bubble(self, visible: bool) -> None:
        self._bubble.visible = visible
        self._bubble.update()

    def _on_tap(self, e: ft.TapEvent) -> None:
        self.set_value(self._value_from_y(e.local_position.y))
        self._set_bubble(True)
        self.on_commit(self.value)

    def _on_release(self, e: ft.TapEvent) -> None:
        self._set_bubble(False)

    def _on_drag(self, e) -> None:
        self.set_value(self._value_from_y(e.local_position.y))
        self._set_bubble(True)

    def _on_end(self, e: ft.DragEndEvent) -> None:
        self._set_bubble(False)
        self.on_commit(self.value)
