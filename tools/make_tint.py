"""Stonowana wersja ikony: kafelek w kolorze palety i ikona przefarbowana na jeden pastel.

Użycie: python3 make_tint.py <wejście.png> <wyjście.png> <pastel #rrggbb> [rozmiar]
"""
import sys
from PIL import Image, ImageChops, ImageDraw, ImageOps


def rounded_mask(size: int, radius: float) -> Image.Image:
    ss = 4
    big = Image.new("L", (size * ss, size * ss), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, size * ss - 1, size * ss - 1), radius=radius * ss, fill=255)
    return big.resize((size, size), Image.LANCZOS)


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def tint(src: Image.Image, pastel: str, size: int) -> Image.Image:
    color = hex_rgb(pastel)
    tile_bg = tuple(int(c * 0.16 + 24) for c in color)
    icon = src.convert("RGBA")
    icon = icon.crop(icon.getchannel("A").getbbox() or (0, 0, *icon.size))
    scale = size * 0.58 / max(icon.size)
    icon = icon.resize((max(1, round(icon.width * scale)), max(1, round(icon.height * scale))), Image.LANCZOS)

    # Jasność ikony staje się odcieniem pastelu: ciemne partie bliżej tła, jasne pełnym kolorem.
    gray = ImageOps.autocontrast(icon.convert("L"), cutoff=2)
    colored = ImageOps.colorize(gray, black=tuple(int(c * 0.55) for c in color), white=tuple(min(255, int(c * 1.08)) for c in color))
    colored.putalpha(icon.getchannel("A"))

    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(Image.new("RGBA", (size, size), (*tile_bg, 255)), mask=rounded_mask(size, size * 0.26))
    out.alpha_composite(colored, ((size - colored.width) // 2, (size - colored.height) // 2))
    return out


if __name__ == "__main__":
    size = int(sys.argv[4]) if len(sys.argv) > 4 else 256
    tint(Image.open(sys.argv[1]), sys.argv[3], size).save(sys.argv[2])
