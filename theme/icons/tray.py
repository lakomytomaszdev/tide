"""Ikony zasobnika górnego paska w stylu Tide (decyzja 95): kafelki jak glify docka.

Każda ikona to zaokrąglony kafelek w ciemnym odcieniu pastelu (ten sam wzór co w docku: pastel × tint
+ lift na kanał) ze znakiem w tym pastelu. Znak pochodzi z `theme/icons/tray/` (kreska na siatce 22 px,
elementy z klasami schematu `ColorScheme-*` i `currentColor`) albo z logo Simple Icons (ChatGPT, Discord,
OBS — dla nich ikony `tide-tray-*` wystawia lustro tide-tray). Stan widać w formie kafelka:
`live` — jaśniejszy kafelek z obwódką (słucha albo nagrywa), `bright` — sam jaśniejszy kafelek
(połączone), `badge` — kropka na rogu.

Kolory są wpisane z palety w arkusz o własnym id, nie `current-color-scheme`: KIconLoader przebarwia
tylko ten drugi, a kafelek ma zostać w pastelu niezależnie od koloru tekstu schematu.

`tray.json` mówi, pod jakimi nazwami ikon leży każdy kafelek. Nakładka podmienia wyłącznie obrazki
pod istniejącymi nazwami — nie dodaje elementów zasobnika ani widżetów, więc nie może ich zdublować.

Użycie: python3 tray.py preview [--out PLIK.png]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HERE = Path(__file__).resolve().parent
GLYPHS = HERE / "tray"
CONFIG = HERE / "tray.json"
PALETTE = ROOT / "theme" / "palette" / "mist.json"
DIRECTORY = "status/symbolic"
STYLE_ID = "tide-tray"
SCHEME_STYLE_ID = "current-color-scheme"
SIZE = 22
OPEN_TAG = re.compile(r"<svg\b[^>]*>")
CLOSE_TAG = re.compile(r"</svg>\s*$")
TITLE = re.compile(r"<title>.*?</title>", re.S)
SCHEME_CLASSES = ("Text", "Highlight", "NegativeText", "NeutralText", "PositiveText")
# Kropka na rogu kafelka i szczelina wokół niej (w jednostkach siatki 22).
BADGE = {"cx": 18.2, "cy": 3.8, "r": 2.7, "gap": 1.4}
DEFAULT_TILE = {"glyph": 0.62, "stroke": 1.8, "liveTint": 0.32, "radius": 0.26}


class TrayError(Exception):
    """Błąd w tray.json albo w źródle znaku."""


def load(path: Path = CONFIG) -> dict:
    data = json.loads(path.read_text())
    data["tile"] = {**DEFAULT_TILE, **data.get("tile", {})}
    data.setdefault("mirror", [])
    return data


def load_config(path: Path = CONFIG) -> dict[str, dict]:
    return load(path)["icons"]


def all_names(config: dict[str, dict]) -> list[str]:
    return [name for entry in config.values() for name in entry["names"]]


# ---------------------------------------------------------------- kolory

def color(palette: dict, ref: str) -> str:
    """Pastel po nazwie albo token `text.*`, `accent`."""
    if ref in palette["pastel"]:
        return palette["pastel"][ref]
    if ref.startswith("text."):
        return palette["text"][ref.split(".", 1)[1]]
    if ref == "accent":
        return palette["accent"]
    raise TrayError(f"nieznany kolor {ref!r}")


def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def tile_color(pastel: str, tint: float, palette_tile: dict) -> str:
    """Ciemny odcień pastelu: ten sam wzór co kafelki docka (glyphs.tile_color)."""
    lift = palette_tile.get("lift", 24)
    return "#" + "".join(f"{min(255, int(c * tint + lift)):02x}" for c in hex_rgb(pastel))


def scheme_colors(palette: dict, text: str) -> dict[str, str]:
    """Kolory klas znaku: tekst w pastelu kafelka, stany z palety jak w TideMist.colors."""
    pastel, semantic = palette["pastel"], palette["semantic"]
    return {
        "Text": text,
        "Highlight": palette["accent"],
        "NegativeText": pastel[semantic["negative"]],
        "NeutralText": pastel[semantic["neutral"]],
        "PositiveText": pastel[semantic["positive"]],
    }


def stylesheet(colors: dict[str, str]) -> str:
    rules = " ".join(f".ColorScheme-{k} {{ color:{v}; }}" for k, v in colors.items())
    return f'<style id="{STYLE_ID}" type="text/css">{rules}</style>'


# ---------------------------------------------------------------- znaki

def inner(source: str) -> str:
    """Zawartość <svg>…</svg> bez znacznika i tytułu."""
    match = OPEN_TAG.search(source)
    if match is None:
        raise TrayError("znak bez znacznika <svg>")
    return CLOSE_TAG.sub("", TITLE.sub("", source[match.end():])).strip()


def view_box(source: str) -> float:
    match = re.search(r'viewBox="0 0 ([\d.]+) [\d.]+"', source)
    return float(match.group(1)) if match else SIZE


def glyph_art(entry: dict, glyphs: Path, simple) -> tuple[str, float, bool]:
    """(zawartość znaku, rozmiar jego siatki, czy to logo wypełnione) dla wpisu tray.json."""
    if "glyph" in entry:
        source = (glyphs / f"{entry['glyph']}.svg").read_text()
        return inner(source), view_box(source), False
    if "simpleIcons" in entry:
        if simple is None:
            raise TrayError(f"logo simpleIcons:{entry['simpleIcons']} bez źródła Simple Icons")
        source = Path(simple.path(entry["simpleIcons"])).read_text()
        return inner(source), view_box(source), True
    raise TrayError("wpis bez glyph i simpleIcons")


def tile_svg(entry: dict, palette: dict, tile: dict, glyphs: Path = GLYPHS, simple=None,
             palette_tile: dict | None = None) -> str:
    """Kafelek z jednym znakiem w stylu docka."""
    palette_tile = palette_tile or {}
    pastel = color(palette, entry["color"])
    live = bool(entry.get("live"))
    bright = live or bool(entry.get("bright"))
    base_tint = palette_tile.get("tint", 0.16)
    fill = tile_color(pastel, tile["liveTint"] if bright else base_tint, palette_tile)
    art, grid, filled = glyph_art(entry, glyphs, simple)
    if not filled:
        art = art.replace('stroke-width="1.5"', f'stroke-width="{tile["stroke"]}"')
    size = SIZE * tile["glyph"] * (0.92 if filled else 1.0)
    offset = (SIZE - size) / 2
    scale = size / grid
    paint = f' fill="{pastel}"' if filled else ""
    radius = SIZE * tile["radius"]

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" width="{SIZE}" height="{SIZE}">',
             stylesheet(scheme_colors(palette, pastel))]
    badge = entry.get("badge")
    if badge:
        b = BADGE
        parts.append(f'<mask id="badge-gap"><rect width="{SIZE}" height="{SIZE}" fill="white"/>'
                     f'<circle cx="{b["cx"]}" cy="{b["cy"]}" r="{b["r"] + b["gap"]}" fill="black"/></mask>')
        parts.append('<g mask="url(#badge-gap)">')
    parts.append(f'<rect width="{SIZE}" height="{SIZE}" rx="{radius:.2f}" fill="{fill}"/>')
    if live:
        parts.append(f'<rect x="0.6" y="0.6" width="{SIZE - 1.2}" height="{SIZE - 1.2}" rx="{radius - 0.6:.2f}" '
                     f'fill="none" stroke="{pastel}" stroke-width="1.2"/>')
    parts.append(f'<g transform="translate({offset:.3f} {offset:.3f}) scale({scale:.4f})"{paint}>{art}</g>')
    if badge:
        parts.append("</g>")
        parts.append(f'<circle cx="{BADGE["cx"]}" cy="{BADGE["cy"]}" r="{BADGE["r"]}" fill="{color(palette, badge)}"/>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def icons(palette: dict, data: dict | None = None, glyphs: Path = GLYPHS, simple=None,
          palette_tile: dict | None = None, warn=None) -> dict[str, str]:
    """Nazwa pliku ikony → SVG kafelka; każdy kafelek pod każdą swoją nazwą.

    Logo, którego nie da się pobrać (brak sieci przy pierwszej budowie), pomijamy z ostrzeżeniem:
    lustro tide-tray pokaże wtedy oryginalny obrazek aplikacji.
    """
    data = load() if data is None else data
    out: dict[str, str] = {}
    for key, entry in data["icons"].items():
        try:
            svg = tile_svg(entry, palette, data["tile"], glyphs, simple, palette_tile)
        except Exception as error:  # noqa: BLE001 — brak logo nie może zatrzymać całej nakładki
            if "simpleIcons" not in entry:
                raise
            if warn:
                warn(f"zasobnik: {key} bez kafelka ({error})")
            continue
        for name in entry["names"]:
            if f"{name}.svg" in out:
                raise TrayError(f"nazwa {name} przypisana do dwóch kafelków")
            out[f"{name}.svg"] = svg
    return out


# ---------------------------------------------------------------- podgląd

def render(svg: str, size: int):
    """Rysuje SVG wtyczką obrazów QtSvg, czyli tym samym silnikiem, którego używa Plasma."""
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QSize
    from PyQt6.QtGui import QImageReader

    buffer = QBuffer()
    buffer.setData(QByteArray(svg.encode()))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, QByteArray(b"svg"))
    reader.setScaledSize(QSize(size, size))
    image = reader.read()
    if image.isNull():
        raise ValueError(f"QtSvg nie wczytał kafelka: {reader.errorString()}")
    return image


def preview(out: Path, palette: dict, simple=None, palette_tile: dict | None = None, scale: int = 4) -> Path:
    """Arkusz: każdy kafelek powiększony i w rozmiarze paska, na tle panelu."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

    app = QGuiApplication.instance() or QGuiApplication(["tray-preview"])  # noqa: F841
    data = load()
    keys = list(data["icons"])
    cell_w, cell_h, cols = 150, SIZE * scale + 64, 7
    rows = (len(keys) + cols - 1) // cols
    sheet = QImage(cols * cell_w, rows * cell_h, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(QColor(palette["background"]["panel"]))
    painter = QPainter(sheet)
    painter.setFont(QFont(palette["fonts"]["ui"], 8))
    painter.setPen(QColor(palette["text"]["muted"]))
    for i, key in enumerate(keys):
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        try:
            svg = tile_svg(data["icons"][key], palette, data["tile"], GLYPHS, simple, palette_tile)
        except TrayError:
            continue
        painter.drawImage(x + 12, y + 12, render(svg, SIZE * scale))
        painter.drawImage(x + 12 + SIZE * scale + 14, y + 12 + SIZE * scale - 24, render(svg, 24))
        painter.drawText(x + 12, y + SIZE * scale + 20, cell_w - 16, 40,
                         int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap), key)
    painter.end()
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(str(out))
    return out


def main(argv: list[str] | None = None) -> int:
    from theme.icons import glyphs
    from theme.icons.tide_icons import cache_home

    parser = argparse.ArgumentParser(description="Ikony zasobnika Tide")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preview", help="arkusz kafelków zasobnika (QtSvg)")
    p.add_argument("--out", default=str(ROOT / "build" / "icons" / "tray-preview.png"))
    args = parser.parse_args(argv)
    palette = json.loads(PALETTE.read_text())
    config = glyphs.load_config()
    print(preview(Path(args.out), palette, glyphs.SimpleIcons(config, cache_home()), config["tile"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
