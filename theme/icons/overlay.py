"""Generuje nakładkę ikon „Tide” z planu i palety.

Nakładka to zwykły motyw ikon, który dziedziczy po motywie bazowym, więc
zawiera tylko to, czego bazie brakuje albo co robi źle, i to, co ma
wyglądać inaczej niż w bazie:
- stonowane glify: aplikacje z docka jako znak w pastelu na ciemnym kafelku (glyphs.py),
- aliasy: kopie ikon motywu pod nazwami, których motyw nie zna (Toolbox JetBrains, `code`),
- kafelki: oryginalne ikony aplikacji spoza motywu w okręgu jak McMojave-circle,
- glify: proste kafelki w kolorach palety dla aplikacji bez żadnej ikony,
- foldery: ikony miejsc motywu bazowego przefarbowane na teal z palety.
Kopiujemy pliki zamiast dowiązań, żeby nakładka działała też we Flatpaku.
"""
from __future__ import annotations

import colorsys
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from theme.icons import glyphs  # noqa: E402
from theme.icons.audit import MANIFEST, OVERLAY, IconPlan, Plan, Resolver  # noqa: E402

TILE_SIZE = 256
# Klucze [Icon Theme] motywu bazowego, które przepisujemy, żeby Plasma nie zmieniła rozmiarów ikon.
KEPT_KEYS = re.compile(r"^(DisplayDepth|.*Overlay|.*Default|.*Sizes|FollowsColorScheme)$")
DIRECTORIES = {
    "apps/scalable": {"Size": "64", "Context": "Applications", "MinSize": "16", "MaxSize": "512", "Type": "Scalable"},
    # Kafelki są bitmapami 256 px, ale katalog musi pasować do każdego rozmiaru, inaczej Plasma
    # weźmie dokładnie pasującą bitmapę z hicolor zamiast naszego kafelka.
    "apps/256": {"Size": "256", "Context": "Applications", "MinSize": "16", "MaxSize": "512", "Type": "Scalable"},
    "places/scalable": {"Size": "64", "Context": "Places", "MinSize": "32", "MaxSize": "512", "Type": "Scalable"},
}
COLOR_WORDS = {"black", "blue", "brown", "cyan", "green", "grey", "gray", "magenta", "orange", "pink",
               "purple", "red", "violet", "yellow", "white", "teal", "custom", "nordic", "indigo"}
HEX = re.compile(r"#([0-9a-fA-F]{6})\b")


# ---------------------------------------------------------------- kolory

def hex_to_rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def mix(a: str, b: str, amount: float) -> str:
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex(tuple(x + (y - x) * amount for x, y in zip(ra, rb)))


def hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 1.0
    return min(d, 1.0 - d)


class Recolor:
    """Przenosi kolory folderu motywu bazowego na kolor z palety, zachowując ich cieniowanie."""

    def __init__(self, folder_colors: list[str], target: str):
        hls = [colorsys.rgb_to_hls(*hex_to_rgb(c)) for c in folder_colors]
        hls = [c for c in hls if c[2] > 0.25] or hls
        self.hue = sum(c[0] for c in hls) / len(hls)
        self.light = sum(c[1] for c in hls) / len(hls)
        self.sat = sum(c[2] for c in hls) / len(hls)
        self.target = colorsys.rgb_to_hls(*hex_to_rgb(target))

    def color(self, value: str) -> str | None:
        h, l, s = colorsys.rgb_to_hls(*hex_to_rgb(value))
        if s <= 0.25 or hue_distance(h, self.hue) > 30 / 360:
            return None  # szarości, biel i obce kolory (emblematy) zostają
        th, tl, ts = self.target
        new = colorsys.hls_to_rgb(th, min(0.95, max(0.08, l + tl - self.light)), min(1.0, s * ts / self.sat))
        return rgb_to_hex(new)

    def svg(self, text: str) -> str | None:
        changed = False

        def swap(match: re.Match) -> str:
            nonlocal changed
            new = self.color(match.group(0))
            if new is None:
                return match.group(0)
            changed = True
            return new

        out = HEX.sub(swap, text)
        return out if changed else None


# ---------------------------------------------------------------- kafelki i glify

def rasterize(source: Path, size: int = 512):
    from PIL import Image
    if source.suffix == ".svg":
        png = subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), "-a", str(source)],
                             check=True, capture_output=True).stdout
        return Image.open(io.BytesIO(png))
    return Image.open(source)


def tile_png(source: Path) -> bytes:
    from tools.make_tile import tile
    out = io.BytesIO()
    tile(rasterize(source), TILE_SIZE).save(out, format="PNG")
    return out.getvalue()


GLYPHS = {
    # Dymek rozmowy z trzema kropkami — neutralny znak „czatu”, bez cudzych logotypów.
    "chat": ('<path fill="{ink}" d="M19 18h26a7 7 0 0 1 7 7v14a7 7 0 0 1-7 7H31l-9 7v-7h-3a7 7 0 0 1-7-7V25'
             'a7 7 0 0 1 7-7z"/>'
             '<circle cx="23" cy="32" r="3" fill="{color}"/><circle cx="32" cy="32" r="3" fill="{color}"/>'
             '<circle cx="41" cy="32" r="3" fill="{color}"/>'),
    # Otwarta książka — słownik.
    "book": ('<path fill="{ink}" d="M32 22c-5-4-12-5-19-4v27c7-1 14 0 19 4c5-4 12-5 19-4V18c-7-1-14 0-19 4z"/>'
             '<path d="M32 23v24" stroke="{color}" stroke-width="2.5" stroke-linecap="round"/>'),
    "letter": ('<text x="32" y="43" text-anchor="middle" font-family="{font}, sans-serif" font-size="30" '
               'font-weight="600" fill="{ink}">{letter}</text>'),
}


def glyph_color(item: IconPlan, palette: dict) -> str:
    pastels = palette["pastel"]
    if item.color in pastels:
        return pastels[item.color]
    names = sorted(pastels)
    return pastels[names[sum(item.name.encode()) % len(names)]]


def glyph_svg(item: IconPlan, palette: dict) -> str:
    color = glyph_color(item, palette)
    letter = next((c for c in item.label or item.name if c.isalnum()), "?").upper()
    body = GLYPHS.get(item.glyph, GLYPHS["letter"]).format(
        ink=palette["background"]["window"], color=color, letter=letter, font=palette["fonts"]["ui"])
    return "\n".join([
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">',
        '<defs><linearGradient id="tide-glyph" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{mix(color, "#ffffff", 0.25)}"/>'
        f'<stop offset="1" stop-color="{color}"/></linearGradient></defs>',
        '<circle cx="32" cy="32" r="32" fill="url(#tide-glyph)"/>',
        body,
        "</svg>",
    ]) + "\n"


# ---------------------------------------------------------------- foldery

def folder_icons(base: Resolver, palette: dict) -> dict[str, str]:
    """Nazwa pliku → przefarbowane SVG dla ikon miejsc z motywu bazowego."""
    theme = next((t for t in base.chain if base.in_family(t)), None)
    if theme is None:
        return {}
    folder = theme.find("folder", contexts=("Places",))
    if folder is None or folder.suffix != ".svg":
        return {}
    colors = [f"#{c}" for c in HEX.findall(folder.read_text())]
    if not colors:
        return {}  # folder rysowany kolorem tekstu (symboliczny) — nie ma czego przefarbować
    recolor = Recolor(colors, palette["pastel"]["teal"])
    out: dict[str, str] = {}
    for subdir, info in theme.subdirs.items():
        # Ikony symboliczne przefarbowuje sama Plasma kolorem tekstu — zostawiamy je motywowi.
        if info.get("Context") != "Places" or info.get("Type") != "Scalable" or "symbolic" in subdir:
            continue
        for root in theme.roots:
            directory = root / subdir
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.svg")):
                words = set(re.split(r"[-_.]", path.stem.lower()))
                if path.name in out or words & COLOR_WORDS or "symbolic" in words or not path.exists():
                    continue
                svg = recolor.svg(path.read_text(errors="replace"))
                if svg is not None:
                    out[path.name] = svg
    return out


# ---------------------------------------------------------------- motyw

def index_theme(plan: Plan, base: Resolver) -> str:
    theme = next((t for t in base.chain if t.name == plan.base), None)
    lines = [
        "[Icon Theme]",
        f"Name={OVERLAY}",
        f"Comment=Nakładka Tide na {plan.base}: stonowane glify, aliasy, kafelki aplikacji i foldery w kolorach palety",
        f"Inherits={plan.base},breeze-dark,hicolor",
        "Example=folder",
    ]
    if theme is not None:
        lines += [f"{k}={v}" for k, v in theme.index.items() if KEPT_KEYS.match(k)]
    lines.append(f"Directories={','.join(DIRECTORIES)}")
    for name, info in DIRECTORIES.items():
        lines += ["", f"[{name}]", *(f"{k}={v}" for k, v in info.items())]
    return "\n".join(lines) + "\n"


def build(plan: Plan, base: Resolver, palette: dict, out: Path) -> dict:
    """Buduje nakładkę w katalogu obok i podmienia `out` w całości. Zwraca manifest."""
    staging = out.with_name(f".{out.name}.new")
    if staging.exists():
        shutil.rmtree(staging)
    for directory in DIRECTORIES:
        (staging / directory).mkdir(parents=True)

    manifest = {"base": plan.base, "icons": {}, "folders": 0}
    for item in sorted(plan.icons.values(), key=lambda i: i.name):
        if item.kind == "alias":
            suffix = item.source.suffix
            target = staging / ("apps/scalable" if suffix == ".svg" else "apps/256") / f"{item.name}{suffix}"
            shutil.copyfile(item.source, target)
        elif item.kind == "tile":
            (staging / "apps/256" / f"{item.name}.png").write_bytes(tile_png(item.source))
        elif item.kind == "muted":
            (staging / "apps/256" / f"{item.name}.png").write_bytes(glyphs.png(item.art, TILE_SIZE))
        else:
            (staging / "apps/scalable" / f"{item.name}.svg").write_text(glyph_svg(item, palette))
        manifest["icons"][item.name] = item.manifest()

    for name, svg in folder_icons(base, palette).items():
        (staging / "places/scalable" / name).write_text(svg)
        manifest["folders"] += 1

    (staging / "index.theme").write_text(index_theme(plan, base))
    (staging / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")

    if out.exists():
        shutil.rmtree(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(out)
    return manifest
