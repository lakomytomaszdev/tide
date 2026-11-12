"""Generuje motyw KSyntaxHighlighting („Tide”) z palety wariantu — dla podświetlania składni w widoku diffa (K-36, PT-351).

Użycie: python3 generate.py <paleta.json|nazwa> <wyjście.theme>

Motyw to plik JSON czytany przez `Repository` modułu KSyntaxHighlighting z katalogu danych
(`$XDG_DATA_HOME/org.kde.syntax-highlighting/themes/`). Kolory tekstu stylów pochodzą z palety (teksty i pastele);
każdy jest sprawdzany na trzech tłach linii diffu (okno, dodana, usunięta — tint tokenu z alfą jak w DiffView)
i w razie potrzeby przesuwany w stronę tekstu podstawowego, aż kontrast WCAG 2.x osiągnie 4,5:1. Style nie mają tła
formatu, więc tło dodanej/usuniętej linii zostaje widoczne pod każdym tokenem.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from theme.colors.generate import Rgb, contrast, hex_rgb, mix  # noqa: E402
from theme import palette as palettes  # noqa: E402

THEME_NAME = "Tide"
MIN_CONTRAST = 4.5
TINT_ALPHA = 0.13   # jak DiffView.tintAlpha (test pilnuje zgodności)

# Styl modułu → źródło koloru w palecie: "text.<klucz>" albo nazwa pastelu.
STYLES = {
    "Normal": "text.primary", "Others": "teal", "Operator": "text.secondary",
    "Keyword": "lavender", "ControlFlow": "lavender", "Import": "lavender", "Preprocessor": "lavender",
    "Function": "sky", "Extension": "sky", "Variable": "text.primary", "Attribute": "teal", "BuiltIn": "teal",
    "DataType": "butter",
    "DecVal": "peach", "BaseN": "peach", "Float": "peach", "Constant": "peach",
    "Char": "mint", "SpecialChar": "mint", "String": "mint", "VerbatimString": "mint", "SpecialString": "mint",
    "Comment": "text.muted", "Documentation": "text.muted", "CommentVar": "text.muted", "Annotation": "text.muted",
    "RegionMarker": "text.secondary", "Information": "sky", "Warning": "peach", "Alert": "rose", "Error": "rose",
}


def source_color(palette: dict, source: str) -> Rgb:
    if source.startswith("text."):
        return hex_rgb(palette["text"][source[5:]])
    return hex_rgb(palette["pastel"][source])


def diff_backgrounds(palette: dict) -> list[Rgb]:
    """Trzy tła linii diffu: okno (kontekst), dodana i usunięta (token palety z alfą tintu na oknie)."""
    window = hex_rgb(palette["background"]["window"])
    tones = [hex_rgb(palette["pastel"].get(palette["semantic"][kind], palette["semantic"][kind]))
             for kind in ("added", "removed")]
    return [window, *(mix(window, tone, TINT_ALPHA) for tone in tones)]


def readable(color: Rgb, backgrounds: list[Rgb], fallback: Rgb) -> Rgb:
    """Najbliższy `color` kolor na odcinku do `fallback`, który ma kontrast >= 4,5:1 na każdym tle."""
    for step in range(101):
        candidate = mix(color, fallback, step / 100)
        if all(contrast(candidate, bg) >= MIN_CONTRAST for bg in backgrounds):
            return candidate
    return fallback


def hex_color(color: Rgb) -> str:
    return "#%02x%02x%02x" % color


def theme(palette: dict) -> dict:
    backgrounds = diff_backgrounds(palette)
    primary = hex_rgb(palette["text"]["primary"])
    styles = {}
    for style, source in STYLES.items():
        color = hex_color(readable(source_color(palette, source), backgrounds, primary))
        styles[style] = {"text-color": color, "selected-text-color": color}
    return {"metadata": {"name": THEME_NAME, "revision": 1}, "text-styles": styles}


def render(palette: dict) -> str:
    return json.dumps(theme(palette), indent=2) + "\n"


def main() -> None:
    palette = palettes.load(sys.argv[1])
    out = Path(sys.argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(palette))


if __name__ == "__main__":
    main()
