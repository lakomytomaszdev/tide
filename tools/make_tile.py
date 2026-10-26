"""Ujednolica ikonę aplikacji do okrągłego kafelka w stylu motywu McMojave-circle.

Użycie: python3 make_tile.py <wejście.png> <wyjście.png> [rozmiar]
"""
import sys
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

SS = 4  # nadpróbkowanie dla gładkich krawędzi


def circle_mask(size: int) -> Image.Image:
    big = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(big).ellipse((0, 0, size * SS - 1, size * SS - 1), fill=255)
    return big.resize((size, size), Image.LANCZOS)


def coverage(alpha: Image.Image) -> float:
    hist = alpha.histogram()
    return sum(hist[201:]) / (alpha.width * alpha.height)


def corners_clear(alpha: Image.Image) -> bool:
    """Czy narożniki są puste — koło je odsłania, zaokrąglony kwadrat nie."""
    w, h = alpha.size
    probes = [(0.12, 0.12), (0.88, 0.12), (0.12, 0.88), (0.88, 0.88)]
    return all(alpha.getpixel((min(w - 1, int(x * w)), min(h - 1, int(y * h)))) < 64 for x, y in probes)


def dominant_color(icon: Image.Image) -> tuple[int, int, int]:
    rgb = Image.new("RGB", icon.size, (0, 0, 0))
    rgb.paste(icon, mask=icon.getchannel("A"))
    stat = ImageStat.Stat(rgb, mask=icon.getchannel("A").point(lambda a: 255 if a > 200 else 0))
    return tuple(int(c) for c in stat.mean[:3])


def tile(src: Image.Image, size: int) -> Image.Image:
    icon = src.convert("RGBA")
    alpha = icon.getchannel("A")
    # Kadrujemy do pełnego kształtu, bez miękkiego cienia wokół (ikony w stylu macOS go mają).
    solid = alpha.point(lambda a: 255 if a > 200 else 0).getbbox()
    icon = icon.crop(solid or alpha.getbbox() or (0, 0, *icon.size))
    w, h = icon.size
    square = abs(w - h) <= max(w, h) * 0.06
    cov = coverage(icon.getchannel("A"))
    mask = circle_mask(size)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    if square and 0.72 <= cov <= 0.84 and corners_clear(icon.getchannel("A")):
        # Już okrągła — tylko wyrównujemy rozmiar.
        out.alpha_composite(icon.resize((size, size), Image.LANCZOS))
        return out
    if square and cov > 0.84:
        # Pełna grafika (kwadrat, zaokrąglony kwadrat) — wycinamy z niej koło.
        art = icon.resize((size, size), Image.LANCZOS)
        out.paste(art, mask=ImageChops.multiply(mask, art.getchannel("A")))
    else:
        # Przezroczysty kształt — kładziemy go na kole w przyciemnionym kolorze ikony.
        r, g, b = dominant_color(icon)
        bg = Image.new("RGBA", (size, size), (int(r * 0.35) + 18, int(g * 0.35) + 20, int(b * 0.35) + 22, 255))
        out.paste(bg, mask=mask)
        scale = size * 0.64 / max(w, h)
        inner = icon.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        out.alpha_composite(inner, ((size - inner.width) // 2, (size - inner.height) // 2))

    # Delikatna obwódka i światło z góry, jak w ikonach motywu.
    ring = Image.new("RGBA", (size * SS, size * SS), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, size * SS - 1, size * SS - 1), outline=(0, 0, 0, 70), width=SS * max(1, size // 128))
    out.alpha_composite(ring.resize((size, size), Image.LANCZOS))
    light = Image.new("L", (size, size), 0)
    ImageDraw.Draw(light).ellipse((size * 0.08, -size * 0.55, size * 0.92, size * 0.42), fill=28)
    light = ImageChops.multiply(light.filter(ImageFilter.GaussianBlur(size / 16)), mask)
    out.alpha_composite(Image.merge("RGBA", (*[Image.new("L", (size, size), 255)] * 3, light)))
    return out


if __name__ == "__main__":
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 256
    tile(Image.open(sys.argv[1]), size).save(sys.argv[2])
