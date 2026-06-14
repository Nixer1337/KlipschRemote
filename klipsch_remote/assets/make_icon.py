"""Generate the Klipsch Remote app icon — an original audio-visualizer mark.

Four rounded bars (an equalizer / sound-wave motif) in the app's Google-blue
accent on a dark rounded-square tile. It deliberately echoes the app's own UI
(EQ sliders, audio theme) and is NOT Klipsch's logo, so it's safe to ship.

Run:  python klipsch_remote/assets/make_icon.py
Out:  icon.ico (multi-size) + icon.png (1024) next to this file.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

S = 1024            # master size
SS = 4              # supersample factor for smooth edges
BG_TOP = (40, 41, 44)     # #28292C — subtle top sheen
BG_BOTTOM = (27, 27, 29)  # #1B1B1D — surface_dim
ACCENT = (138, 180, 248)  # #8AB4F8 — Google blue
RADIUS = 232        # tile corner radius (squircle-ish)

# Bar layout (in master coordinates): four centred, rounded, varying-height bars.
BAR_W = 122
GAP = 58
HEIGHTS = [360, 624, 520, 300]


def _rounded_mask(size: int, radius: int) -> Image.Image:
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size - 1, size - 1], radius, fill=255)
    return m


def _gradient(size: int, top: tuple, bottom: tuple) -> Image.Image:
    g = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        g.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return g.resize((size, size))


def build(size: int = S) -> Image.Image:
    big = size * SS
    # Dark rounded tile with a faint vertical gradient for depth.
    tile = _gradient(big, BG_TOP, BG_BOTTOM).convert("RGBA")
    tile.putalpha(_rounded_mask(big, RADIUS * SS))

    draw = ImageDraw.Draw(tile)
    total = len(HEIGHTS) * BAR_W + (len(HEIGHTS) - 1) * GAP
    x = (size - total) // 2
    cy = size // 2
    for h in HEIGHTS:
        x0, x1 = x * SS, (x + BAR_W) * SS
        y0, y1 = (cy - h // 2) * SS, (cy + h // 2) * SS
        draw.rounded_rectangle([x0, y0, x1, y1], radius=(BAR_W // 2) * SS, fill=ACCENT)
        x += BAR_W + GAP

    return tile.resize((size, size), Image.LANCZOS)


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    img = build()
    img.save(os.path.join(here, "icon.png"))
    img.save(
        os.path.join(here, "icon.ico"),
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("wrote icon.png and icon.ico in", here)


if __name__ == "__main__":
    main()
