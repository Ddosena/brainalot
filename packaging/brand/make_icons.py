"""Draws the Brainalot icon (a geometric «b» with a spark) at every size the products need.

Run with any Python that has Pillow: python packaging/brand/make_icons.py
Writes extension/icons/icon-{16,32,48,128}.png and packaging/brand/brainalot.ico.
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
TEAL, INK, SPARK = (20, 107, 97, 255), (255, 255, 255, 255), (255, 176, 59, 255)


def draw(size: int) -> Image.Image:
    scale = 8  # supersample, then shrink: smooth edges at 16 px
    s = size * scale
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.22), fill=TEAL)
    # «b»: a stem and a bowl (ring). Heavier strokes at small sizes stay readable.
    stroke = s * (0.15 if size <= 20 else 0.125)
    left, top, bottom = s * 0.25, s * 0.16, s * 0.82
    d.rounded_rectangle((left, top, left + stroke, bottom), radius=int(stroke / 2), fill=INK)
    bowl = s * 0.46
    cx, cy = left + stroke / 2 + bowl / 2 - stroke / 2, bottom - bowl / 2
    d.ellipse((cx - bowl / 2 + stroke / 2, cy - bowl / 2, cx + bowl / 2 + stroke / 2, cy + bowl / 2), fill=INK)
    inner = bowl - 2 * stroke
    d.ellipse((cx - inner / 2 + stroke / 2, cy - inner / 2, cx + inner / 2 + stroke / 2, cy + inner / 2), fill=TEAL)
    # The spark: the idea that comes back.
    r = s * (0.11 if size <= 20 else 0.095)
    sx, sy = s * 0.75, s * 0.25
    d.ellipse((sx - r, sy - r, sx + r, sy + r), fill=SPARK)
    return image.resize((size, size), Image.LANCZOS)


def main() -> None:
    icons = ROOT / "extension" / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 48, 128):
        draw(size).save(icons / f"icon-{size}.png", optimize=True)
    big = draw(256)
    big.save(ROOT / "packaging" / "brand" / "brainalot.ico",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    # A contact sheet for a quick look.
    sheet = Image.new("RGBA", (16 + 32 + 48 + 128 + 60, 140), (245, 247, 245, 255))
    x = 10
    for size in (16, 32, 48, 128):
        sheet.paste(draw(size), (x, 130 - size), draw(size))
        x += size + 10
    sheet.save(ROOT / "packaging" / "brand" / "preview.png")


if __name__ == "__main__":
    main()
