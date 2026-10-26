"""Generuje logo Tide („trzy fale”) z palety wariantu.

Użycie: python3 generate.py <paleta.json> <katalog wyjściowy>

Powstają trzy wersje:
- tide-logo.svg: pełna, do dużych rozmiarów (ekran logowania, blokady, ładowania),
- tide-logo-small.svg: uproszczona na pasek (do ~24 px), grubsze i spokojniejsze fale,
- tide-logo-symbolic.svg: jednokolorowa, przefarbowywana przez Plasmę jak ikony symboliczne.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Kolejność fal od góry: teal, lawenda, brzoskwinia — trzy „strumienie” pracy.
WAVES = ("teal", "lavender", "peach")


def wave(y: float, small: bool) -> str:
    if small:
        # Bez kafelka fale wypełniają całą ikonę — w 18 px liczy się każdy piksel.
        return f"M7 {y}c8.5-8 16.5-8 25 0s16.5 8 25 0"
    return f"M15 {y}c4-4 8-4 12 0s8 4 12 0s7-4 10 0"


def svg(palette: dict, small: bool = False, symbolic: bool = False) -> str:
    small = small or symbolic
    rows = (14, 32, 50) if small else (24, 33, 42)
    stroke = 9 if small else 4.5
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">']
    if symbolic:
        # Klasa ColorScheme-Text pozwala Plasmie przefarbować ikonę na kolor tekstu motywu.
        parts.append('<style id="current-color-scheme">.ColorScheme-Text{color:#eceff1;}</style>')
        for y in rows:
            parts.append(f'<path class="ColorScheme-Text" d="{wave(y, True)}" fill="none" '
                         f'stroke="currentColor" stroke-width="{stroke}" stroke-linecap="round"/>')
    else:
        if not small:
            tile = palette["background"]["card"]
            parts.append(f'<rect x="4" y="4" width="56" height="56" rx="20" fill="{tile}"/>')
        for y, name in zip(rows, WAVES):
            color = palette["pastel"][name]
            parts.append(f'<path d="{wave(y, small)}" fill="none" stroke="{color}" '
                         f'stroke-width="{stroke}" stroke-linecap="round"/>')
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def layers(palette: dict) -> dict[str, str]:
    """Pełne logo rozłożone na warstwy: kafelek i każda fala osobno (ten sam viewBox).

    Plymouth nie umie przyciemnić fragmentu obrazu, więc żeby fale „oddychały” po kolei,
    każda musi być osobnym obrazem. Nałożone na siebie warstwy dają dokładnie svg(palette).
    """
    full = svg(palette).splitlines()
    head, tile, waves, tail = full[0], full[1], full[2:-1], full[-1]
    out = {"tile": "\n".join([head, tile, tail]) + "\n"}
    for name, path in zip(WAVES, waves):
        out[name] = "\n".join([head, path, tail]) + "\n"
    return out


def main() -> None:
    palette = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    (out / "tide-logo.svg").write_text(svg(palette))
    (out / "tide-logo-small.svg").write_text(svg(palette, small=True))
    (out / "tide-logo-symbolic.svg").write_text(svg(palette, symbolic=True))


if __name__ == "__main__":
    main()
