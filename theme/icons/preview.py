"""Arkusz podglądu stonowanych glifów obok obecnych, kolorowych ikon (make icons-preview).

Użycie: python3 preview.py [--base MOTYW] [--out PLIK.png]

Nic nie instaluje: czyta motyw bazowy i aplikacje tak jak `tide-icons build`
i zapisuje jeden obraz PNG (domyślnie build/icons/glyphs-preview.png).
U góry dwa paski jak dock (obecne ikony i glify), niżej karta na aplikację:
oryginał, glif w dużym rozmiarze, w rozmiarze docka i menu oraz źródło znaku.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from theme.icons import desktop, glyphs  # noqa: E402
from theme.icons.audit import muted_key, plan  # noqa: E402
from theme.icons.catalog import Xdg, icon_name  # noqa: E402
from theme.icons.overlay import glyph_svg, rasterize, tile_png  # noqa: E402
from theme.icons.tide_icons import PALETTE, base_resolver, base_theme, cache_home  # noqa: E402

DOCK_ICON = 48
MENU_ICON = 22
BIG = 112
CARD_W, CARD_H = 420, 200
COLUMNS = 4
PAD = 28


def font(size: int, weight: str = "Regular"):
    """Geist z fontconfig; bez niej czcionka zastępcza PIL."""
    if shutil.which("fc-match"):
        path = subprocess.run(["fc-match", "-f", "%{file}", f"Geist:style={weight}"],
                              capture_output=True, text=True).stdout
        if path and Path(path).is_file():
            return ImageFont.truetype(path, size)
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def hex_rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    return (*glyphs.hex_rgb(value), alpha)


def image_from(path: Path) -> Image.Image:
    return rasterize(path, 512).convert("RGBA")


def original(glyph: glyphs.Glyph, resolver, base_plan, palette, apps=()) -> Image.Image | None:
    """Ikona, którą aplikacja ma bez glifu: z motywu, alias, kafelek albo glif-litera."""
    # Aplikacja, której plik .desktop nazywa się jak glif, ale dziś ma inną ikonę
    # (np. tide-worktrees.desktop z Icon=vcs-branch sprzed zmiany nazwy ikony).
    names = list(glyph.names)
    for app in apps:
        if app.id.removesuffix(".desktop") in glyph.names and app.icon and not app.icon.startswith("/"):
            names.append(icon_name(app.icon))
    for name in names:
        path = resolver.themed(name)
        if path is not None:
            return image_from(path)
        item = base_plan.icons.get(name)
        if item is None:
            continue
        if item.kind == "alias":
            return image_from(item.source)
        if item.kind == "tile":
            return Image.open(io.BytesIO(tile_png(item.source))).convert("RGBA")
        with tempfile.NamedTemporaryFile(suffix=".svg") as tmp:
            Path(tmp.name).write_text(glyph_svg(item, palette))
            return image_from(Path(tmp.name))
    # Aplikacja spoza planu (np. Toolbox pod inną nazwą) — szukamy po wzorcach w planie.
    by_name = {name: glyph for name in glyph.names}
    for item in base_plan.icons.values():
        if item.kind == "alias" and item.target in glyph.names:
            return image_from(item.source)
        if item.kind == "tile" and muted_key(item.name, resolver, by_name):
            return Image.open(io.BytesIO(tile_png(item.source))).convert("RGBA")
    # Na końcu własna ikona aplikacji spoza motywu (hicolor, pixmaps).
    for name in names:
        own = resolver.own(name)
        if own is not None:
            return image_from(own) if own.suffix == ".svg" else Image.open(own).convert("RGBA")
    return None


def fit(image: Image.Image, size: int) -> Image.Image:
    return image.resize((size, size), Image.LANCZOS)


def dock_strip(icons: list[Image.Image | None], palette: dict, width: int) -> Image.Image:
    """Pasek jak dock Tide: tło panelu, promień docka z palety."""
    gap = 12
    height = palette["size"]["dock"]
    strip = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    inner = len(icons) * (DOCK_ICON + gap) + gap
    left = (width - inner) // 2
    ImageDraw.Draw(strip).rounded_rectangle((left, 0, left + inner, height - 1), radius=palette["radius"]["dock"],
                                            fill=hex_rgba(palette["background"]["panel"]))
    x = left + gap
    for icon in icons:
        if icon is not None:
            strip.alpha_composite(fit(icon, DOCK_ICON), (x, (height - DOCK_ICON) // 2))
        x += DOCK_ICON + gap
    return strip


def sheet(entries: list[tuple[glyphs.Glyph, Image.Image | None]], palette: dict) -> Image.Image:
    rows = (len(entries) + COLUMNS - 1) // COLUMNS
    width = PAD * 2 + COLUMNS * CARD_W + (COLUMNS - 1) * 16
    dock_h = palette["size"]["dock"]
    # Paski jak dock mieszczą ograniczoną liczbę ikon — dłuższa lista idzie w kilku parach pasków.
    per_strip = max(1, (width - 2 * PAD - 12) // (DOCK_ICON + 12) - 2)
    chunks = [entries[i:i + per_strip] for i in range(0, len(entries), per_strip)] or [[]]
    top = PAD + 40 + len(chunks) * 2 * (dock_h + 34) + (len(chunks) - 1) * 12
    height = top + rows * (CARD_H + 16) + PAD
    bg = palette["background"]
    text = palette["text"]
    out = Image.new("RGBA", (width, height), hex_rgba(bg["desktop"]))
    draw = ImageDraw.Draw(out)
    draw.text((PAD, PAD), f"Stonowane glify Tide Mist — podgląd (#8), aplikacji: {len(entries)}",
              font=font(22, "SemiBold"), fill=hex_rgba(text["primary"]))

    y = PAD + 40
    for chunk in chunks:
        for label, icons in (("Obecne ikony", [orig for _, orig in chunk]),
                             ("Glify", [glyphs.render(g, 256) for g, _ in chunk])):
            draw.text((PAD, y), label, font=font(14), fill=hex_rgba(text["muted"]))
            out.alpha_composite(dock_strip(icons, palette, width - 2 * PAD), (PAD, y + 22))
            y += dock_h + 34
        y += 12

    small = font(12)
    for index, (glyph, orig) in enumerate(entries):
        col, row = index % COLUMNS, index // COLUMNS
        x0, y0 = PAD + col * (CARD_W + 16), top + row * (CARD_H + 16)
        draw.rounded_rectangle((x0, y0, x0 + CARD_W, y0 + CARD_H), radius=palette["radius"]["card"],
                               fill=hex_rgba(bg["card"]))
        tile = glyphs.render(glyph, 256)
        if orig is not None:
            out.alpha_composite(fit(orig, BIG), (x0 + 18, y0 + 18))
        else:
            draw.text((x0 + 40, y0 + 64), "brak", font=small, fill=hex_rgba(text["muted"]))
        out.alpha_composite(fit(tile, BIG), (x0 + 18 + BIG + 20, y0 + 18))
        sx = x0 + 18 + 2 * BIG + 44
        out.alpha_composite(fit(tile, DOCK_ICON), (sx, y0 + 26))
        out.alpha_composite(fit(tile, MENU_ICON), (sx + DOCK_ICON + 14, y0 + 26 + (DOCK_ICON - MENU_ICON) // 2))
        draw.text((sx, y0 + 26 + DOCK_ICON + 10), "dock · menu", font=small, fill=hex_rgba(text["muted"]))
        draw.text((x0 + 18, y0 + BIG + 30), glyph.app, font=font(16, "Medium"), fill=hex_rgba(text["primary"]))
        draw.text((x0 + 18, y0 + BIG + 54), glyph.origin, font=small, fill=hex_rgba(text["muted"]))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Podgląd stonowanych glifów Tide.")
    parser.add_argument("--base", help="motyw bazowy (domyślnie jak tide-icons build)")
    parser.add_argument("--out", default=str(ROOT / "build" / "icons" / "glyphs-preview.png"))
    args = parser.parse_args(argv)

    xdg = Xdg.from_env()
    base = base_theme(xdg, args.base)
    resolver = base_resolver(xdg, base)
    palette = json.loads(PALETTE.read_text())
    resolved = glyphs.resolve(glyphs.load_config(), palette, resolver, cache_home())
    # Plan bez glifów i z oryginalnymi plikami .desktop (bez kopii od Tide): to, co jest dziś.
    apps = [d for d in desktop.source_entries(xdg).values() if d.visible(xdg)]
    base_plan = plan(apps, resolver, base)
    unique: list[glyphs.Glyph] = []  # jeden glif ma kilka nazw; Glyph nie jest hashowalny
    seen = set()
    for glyph in resolved.values():
        if id(glyph) not in seen:
            seen.add(id(glyph))
            unique.append(glyph)
    entries = [(g, original(g, resolver, base_plan, palette, apps)) for g in unique]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet(entries, palette).save(out)
    print(f"Podgląd {len(entries)} glifów na motywie {base}: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
