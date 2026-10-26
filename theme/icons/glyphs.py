"""Stonowane glify aplikacji z docka (#8, decyzja 14).

Każda aplikacja z `glyphs.json` dostaje kafelek: zaokrąglony kwadrat w
ciemnym odcieniu pastelu z palety i jednokolorowy znak aplikacji w tym
pastelu. Znak pochodzi z jednego z trzech źródeł, sprawdzanych po kolei:
- `theme`: ikona symboliczna z łańcucha motywu bazowego (albo z Breeze),
- `simpleIcons`: monochromatyczne logo z Simple Icons (CC0), pobierane przy
  budowaniu do pamięci podręcznej i sprawdzane sumą SHA-256 zapisaną w repozytorium,
- `file`: prosty, ręcznie narysowany znak z katalogu `glyphs/` obok.

Pobrane pliki SVG to dane z zewnątrz: tylko rysuje je rsvg-convert, a z
wyniku bierzemy samą maskę przezroczystości. Kolory znaku nie mają znaczenia.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from theme.icons.catalog import load_theme

CONFIG = Path(__file__).resolve().with_name("glyphs.json")
LOCAL_DIR = Path(__file__).resolve().with_name("glyphs")
FALLBACK_THEMES = ("breeze-dark", "breeze")  # znaki symboliczne Plasmy, gdy baza ich nie ma
MAX_DOWNLOAD = 64 * 1024  # logo Simple Icons ma 1–3 KB; większy plik to na pewno nie ono
RENDER_PX = 1024  # rozdzielczość, w której rysujemy znak przed zmniejszeniem
SS = 4  # nadpróbkowanie zaokrąglonych rogów


class GlyphError(Exception):
    """Źródło znaku jest niedostępne albo niezaufane — aplikacja zostaje przy swojej ikonie."""


@dataclass
class Glyph:
    app: str
    names: list[str]
    sources: list[dict]
    color: str  # #rrggbb z palety
    tile: dict = field(default_factory=dict)
    path: Path | None = None  # rozwiązane źródło znaku
    origin: str = ""  # opis źródła do manifestu, np. simpleIcons:openai@13.21.0
    mask: Any = field(default=None, repr=False)  # maska znaku narysowana raz, przy rozwiązywaniu


# ---------------------------------------------------------------- konfiguracja

def load_config(path: Path | None = None) -> dict:
    """Plik glifów; TIDE_GLYPHS podmienia go (testy, własne listy)."""
    path = path or Path(os.environ.get("TIDE_GLYPHS") or CONFIG)
    config = json.loads(path.read_text())
    config.setdefault("apps", [])
    config.setdefault("tile", {})
    config.setdefault("simpleIcons", {})
    config["_dir"] = str(path.parent)
    return config


def palette_color(palette: dict, ref: str) -> str:
    """`teal` to pastel z palety, `text.secondary` to ścieżka w palecie."""
    if ref.startswith("#"):
        raise ValueError(f"Kolor glifu musi pochodzić z palety, a nie być wpisany na sztywno: {ref}")
    value = palette["pastel"] if "." not in ref else palette
    for part in ([ref] if "." not in ref else ref.split(".")):
        value = value[part]
    if isinstance(value, str) and value in palette["pastel"]:
        value = palette["pastel"][value]  # np. semantic.positive → mint
    return value


def entries(config: dict, palette: dict) -> list[Glyph]:
    tile = {"radius": 0.26, "glyph": 0.46, "tint": 0.16, "lift": 24, **config["tile"]}
    out = []
    for app in config["apps"]:
        sources = app["glyph"] if isinstance(app["glyph"], list) else [app["glyph"]]
        # `scale` wyrównuje wagę optyczną: pełne kwadraty (JetBrains) wyglądają na większe niż znaki z kreski.
        own = {**tile, "glyph": app["scale"]} if "scale" in app else tile
        out.append(Glyph(app["app"], list(app["names"]), sources, palette_color(palette, app["color"]), own))
    return out


# ---------------------------------------------------------------- źródła

class SimpleIcons:
    """Logo z Simple Icons w przypiętej wersji, pobierane do pamięci podręcznej."""

    def __init__(self, config: dict, cache_home: Path):
        spec = config["simpleIcons"]
        self.version = spec.get("version", "")
        self.url = os.environ.get("TIDE_SIMPLE_ICONS_URL") or spec.get("url", "")
        self.pins: dict[str, str] = spec.get("sha256", {})
        self.cache = cache_home / "tide" / "simple-icons" / self.version

    def path(self, slug: str) -> Path:
        if not slug.replace("-", "").isalnum():
            raise GlyphError(f"niepoprawna nazwa logo Simple Icons: {slug}")
        cached = self.cache / f"{slug}.svg"
        pin = self.pins.get(slug)
        if pin is None:
            raise GlyphError(f"brak sumy SHA-256 dla simpleIcons:{slug} w glyphs.json "
                             f"(tide-icons pin-glyphs dopisze ją po sprawdzeniu)")
        if cached.is_file() and sha256(cached.read_bytes()) == pin:
            return cached
        data = self.download(slug)
        if sha256(data) != pin:
            raise GlyphError(f"simpleIcons:{slug} ma inną sumę SHA-256 niż zapisana w glyphs.json — pominięte")
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(cached)
        return cached

    def download(self, slug: str) -> bytes:
        url = self.url.format(version=self.version, slug=slug)
        if not url.startswith(("https://", "file://")):
            raise GlyphError(f"adres Simple Icons musi być https: {url}")
        try:
            with urllib.request.urlopen(url, timeout=15) as response:
                data = response.read(MAX_DOWNLOAD + 1)
        except OSError as error:
            raise GlyphError(f"nie udało się pobrać simpleIcons:{slug} ({error})") from error
        if len(data) > MAX_DOWNLOAD or not data.lstrip().startswith(b"<svg"):
            raise GlyphError(f"simpleIcons:{slug} nie wygląda na logo SVG — pominięte")
        return data


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def theme_glyph(name: str, resolver) -> Path | None:
    """Znak symboliczny z łańcucha motywu bazowego, a na końcu z Breeze."""
    from theme.icons.audit import OVERLAY
    for theme in resolver.chain:
        if theme.name == OVERLAY:
            continue
        path = theme.find(name)
        if path is not None and path.suffix == ".svg":
            return path
    paths = resolver.xdg.icon_paths()
    for fallback in FALLBACK_THEMES:
        theme = load_theme(fallback, paths)
        path = theme.find(name) if theme else None
        if path is not None and path.suffix == ".svg":
            return path
    return None


def locate(source: dict, resolver, simple: SimpleIcons, local_dir: Path) -> tuple[Path, str]:
    if "theme" in source:
        path = theme_glyph(source["theme"], resolver)
        if path is None:
            raise GlyphError(f"motyw nie ma ikony {source['theme']}")
        return path, f"theme:{source['theme']}"
    if "simpleIcons" in source:
        slug = source["simpleIcons"]
        return simple.path(slug), f"simpleIcons:{slug}@{simple.version}"
    if "file" in source:
        path = (local_dir / source["file"]).resolve()
        if not path.is_relative_to(local_dir.resolve()) or not path.is_file():
            raise GlyphError(f"brak pliku glifu {source['file']}")
        return path, f"file:{source['file']}"
    raise GlyphError(f"nieznane źródło glifu: {source}")


def resolve(config: dict, palette: dict, resolver, cache_home: Path, warn=None) -> dict[str, Glyph]:
    """Nazwa ikony → glif z rozwiązanym źródłem. Aplikacje bez źródła pomijamy z ostrzeżeniem."""
    warn = warn or (lambda message: print(f"tide-icons: {message}", file=sys.stderr))
    simple = SimpleIcons(config, cache_home)
    local_dir = Path(config.get("_dir", CONFIG.parent)) / "glyphs"
    if not local_dir.is_dir():
        local_dir = LOCAL_DIR
    out: dict[str, Glyph] = {}
    for glyph in entries(config, palette):
        errors = []
        for source in glyph.sources:
            try:
                path, origin = locate(source, resolver, simple, local_dir)
                glyph.mask = glyph_mask(path)  # od razu sprawdzamy, czy da się go narysować
                glyph.path, glyph.origin = path, origin
                break
            except GlyphError as error:
                errors.append(str(error))
        if glyph.path is None:
            warn(f"{glyph.app} zostaje przy swojej ikonie: {'; '.join(errors)}")
            continue
        for name in glyph.names:
            out.setdefault(name, glyph)
    return out


def standalone(name: str, config: dict, palette: dict) -> Glyph:
    """Glif pod nazwą `name` narysowany bez motywu bazowego i bez sieci — tylko z ręcznego znaku (`file`).

    Służy ikonom zapasowym własnych aplikacji Tide w hicolor (np. Tide Worktrees):
    działają także bez nakładki, a nakładka i tak je zasłania tym samym glifem.
    """
    local_dir = Path(config.get("_dir", CONFIG.parent)) / "glyphs"
    if not local_dir.is_dir():
        local_dir = LOCAL_DIR
    for glyph in entries(config, palette):
        if name not in glyph.names:
            continue
        for source in glyph.sources:
            if "file" in source:
                path, origin = locate(source, None, None, local_dir)
                glyph.path, glyph.origin, glyph.mask = path, origin, glyph_mask(path)
                return glyph
        raise GlyphError(f"{glyph.app} nie ma ręcznego znaku (file) — bez motywu nie da się go narysować")
    raise GlyphError(f"brak glifu o nazwie {name} w glyphs.json")


# ---------------------------------------------------------------- rysowanie

def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def tile_color(pastel: str, tile: dict) -> tuple[int, int, int]:
    """Tło kafelka: ciemny odcień pastelu (pastel × tint + lift na kanał)."""
    return tuple(min(255, int(c * tile["tint"] + tile["lift"])) for c in hex_rgb(pastel))


def glyph_mask(source: Path):
    """Maska znaku (kanał alfa) przycięta do jego obrysu. Kolory źródła pomijamy."""
    from PIL import Image
    try:
        png = subprocess.run(["rsvg-convert", "-w", str(RENDER_PX), "-h", str(RENDER_PX), "-a", str(source)],
                             check=True, capture_output=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise GlyphError(f"rsvg-convert nie narysował {source.name}") from error
    alpha = Image.open(io.BytesIO(png)).convert("RGBA").getchannel("A")
    box = alpha.getbbox()
    if box is None:
        raise GlyphError(f"{source.name} jest pusty")
    return alpha.crop(box)


def rounded_mask(size: int, radius: float):
    from PIL import Image, ImageDraw
    big = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(big).rounded_rectangle((0, 0, size * SS - 1, size * SS - 1), radius=radius * SS, fill=255)
    return big.resize((size, size), Image.LANCZOS)


def render(glyph: Glyph, size: int):
    """Kafelek glifu jako obraz RGBA `size`×`size`."""
    from PIL import Image
    tile = glyph.tile
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(Image.new("RGBA", (size, size), (*tile_color(glyph.color, tile), 255)),
              mask=rounded_mask(size, size * tile["radius"]))
    mask = glyph.mask if glyph.mask is not None else glyph_mask(glyph.path)
    scale = size * tile["glyph"] / max(mask.size)
    mask = mask.resize((max(1, round(mask.width * scale)), max(1, round(mask.height * scale))), Image.LANCZOS)
    ink = Image.new("RGBA", mask.size, (*hex_rgb(glyph.color), 255))
    ink.putalpha(mask)
    out.alpha_composite(ink, ((size - mask.width) // 2, (size - mask.height) // 2))
    return out


def png(glyph: Glyph, size: int) -> bytes:
    data = io.BytesIO()
    render(glyph, size).save(data, format="PNG")
    return data.getvalue()


# ---------------------------------------------------------------- przypinanie

def pin(path: Path, cache_home: Path) -> list[str]:
    """Pobiera logo Simple Icons bez zapisanej sumy i dopisuje ją do pliku glifów."""
    config = json.loads(path.read_text())
    spec = config.setdefault("simpleIcons", {})
    pins = spec.setdefault("sha256", {})
    simple = SimpleIcons({"simpleIcons": spec}, cache_home)
    added = []
    for app in config.get("apps", []):
        sources = app["glyph"] if isinstance(app["glyph"], list) else [app["glyph"]]
        for source in sources:
            slug = source.get("simpleIcons")
            if slug and slug not in pins:
                pins[slug] = sha256(simple.download(slug))
                added.append(slug)
    if added:
        spec["sha256"] = dict(sorted(pins.items()))
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return added
