"""Generate the PWA icons and logo images from assets/logo-source.jpg.

Run once after changing the logo (needs Pillow, a dev-only dependency):

    pip install pillow
    python tools/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "assets" / "logo-source.jpg"
OUT = ROOT / "static" / "icons"

# Regions of the source image (1408x768), measured from the picture:
TILE = (454, 124, 954, 628)  # the white rounded app tile
SYMBOL_ROWS = (182, 500)  # document + sync arrows
WORDMARK_ROWS = (516, 573)  # "SyllabusSync"
WHITE = (255, 255, 255)


def ink_box(im: Image.Image, rows: tuple[int, int], pad: int) -> tuple[int, int, int, int]:
    """Bounding box of non-white pixels inside the tile for the given rows."""
    px = im.load()
    x0, _, x1, _ = TILE
    xs = [
        x
        for x in range(x0 + 30, x1 - 30)
        if any(sum(px[x, y]) / 3 < 225 for y in range(rows[0], rows[1], 2))
    ]
    return (xs[0] - pad, rows[0] - pad, xs[-1] + pad, rows[1] + pad)


def whiten(img: Image.Image, threshold: int = 238) -> Image.Image:
    """Snap the tile's near-white background (JPEG noise, faint gradient) to pure white."""
    img = img.convert("RGB")
    px = img.load()
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = px[x, y]
            if min(r, g, b) >= threshold:
                px[x, y] = WHITE
    return img


def square(img: Image.Image, size: int, content_ratio: float, bg=WHITE) -> Image.Image:
    """Centre img on a square canvas, scaled so its longer side is content_ratio of size."""
    canvas = Image.new("RGBA" if bg is None else "RGB", (size, size), bg or (0, 0, 0, 0))
    scale = size * content_ratio / max(img.size)
    resized = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    canvas.paste(resized, ((size - resized.width) // 2, (size - resized.height) // 2))
    return canvas


def rounded(img: Image.Image, radius_ratio: float = 0.225) -> Image.Image:
    """Give a square icon transparent rounded corners (for 'any' purpose icons and the UI)."""
    size = img.width
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size * 4 - 1, size * 4 - 1), radius=int(size * 4 * radius_ratio), fill=255
    )
    mask = mask.resize((size, size), Image.LANCZOS)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    src = Image.open(SRC).convert("RGB")

    symbol = whiten(src.crop(ink_box(src, SYMBOL_ROWS, pad=6)))
    lockup = whiten(src.crop((TILE[0] + 30, SYMBOL_ROWS[0] - 10, TILE[2] - 30, WORDMARK_ROWS[1] + 14)))

    # App icons: the symbol on a white tile (the wordmark is unreadable at icon sizes,
    # and the device shows the app name under the icon anyway).
    for size in (192, 512):
        rounded(square(symbol, size, 0.72)).save(OUT / f"icon-{size}.png", optimize=True)
        # Maskable: full-bleed white, symbol inside the 80% safe zone.
        square(symbol, size, 0.58).save(OUT / f"maskable-{size}.png", optimize=True)
    square(symbol, 180, 0.70).save(OUT / "apple-touch-icon.png", optimize=True)  # iOS rounds it itself
    rounded(square(symbol, 64, 0.80)).save(OUT / "favicon-64.png", optimize=True)
    rounded(square(symbol, 32, 0.84)).save(OUT / "favicon-32.png", optimize=True)

    # Images used inside the UI.
    mark = square(symbol, 96, 0.92)
    rounded(mark, 0.24).save(OUT / "logo-mark.png", optimize=True)
    lockup.thumbnail((640, 640), Image.LANCZOS)
    lockup.save(OUT / "logo-lockup.png", optimize=True)

    for p in sorted(OUT.iterdir()):
        with Image.open(p) as im:
            print(f"{p.name:22} {im.size[0]}x{im.size[1]}  {p.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
