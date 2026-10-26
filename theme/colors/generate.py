"""Generuje schemat kolorów KDE z palety wariantu.

Użycie: python3 generate.py <paleta.json> <wyjście.colors>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

Rgb = tuple[int, int, int]


def hex_rgb(value: str) -> Rgb:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def mix(a: Rgb, b: Rgb, amount: float) -> Rgb:
    """Kolor przesunięty od `a` w stronę `b` o `amount` (0–1)."""
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


def fmt(color: Rgb) -> str:
    return ",".join(str(c) for c in color)


def foregrounds(palette: dict, normal: Rgb, inactive: Rgb, on_accent: bool = False) -> dict[str, Rgb]:
    p = {k: hex_rgb(v) for k, v in palette["pastel"].items()}
    accent = hex_rgb(palette["accent"])
    if on_accent:
        # Na tealowym zaznaczeniu pastele byłyby nieczytelne — używamy ich przyciemnionych wersji.
        dark = hex_rgb(palette["background"]["desktop"])
        tone = lambda c: mix(c, dark, 0.72)  # noqa: E731
        return {
            "ForegroundNormal": normal, "ForegroundInactive": inactive, "ForegroundActive": normal,
            "ForegroundLink": tone(p["sky"]), "ForegroundVisited": tone(p["lavender"]),
            "ForegroundNegative": tone(p["rose"]), "ForegroundNeutral": tone(p["butter"]),
            "ForegroundPositive": tone(p["mint"]),
        }
    return {
        "ForegroundNormal": normal, "ForegroundInactive": inactive, "ForegroundActive": accent,
        "ForegroundLink": accent, "ForegroundVisited": p["lavender"],
        "ForegroundNegative": p["rose"], "ForegroundNeutral": p["butter"],
        "ForegroundPositive": p["mint"],
    }


def scheme(palette: dict) -> dict[str, dict[str, str]]:
    """Grupy schematu jako {nazwa grupy: {klucz: wartość}}."""
    bg = {k: hex_rgb(v) for k, v in palette["background"].items()}
    text = {k: hex_rgb(v) for k, v in palette["text"].items()}
    accent = hex_rgb(palette["accent"])
    white = (255, 255, 255)
    deco = {"DecorationFocus": accent, "DecorationHover": mix(accent, white, 0.25)}
    fg = foregrounds(palette, text["primary"], text["muted"])

    def group(normal: Rgb, alternate: Rgb, fgs: dict[str, Rgb] = fg) -> dict[str, str]:
        values = {"BackgroundNormal": normal, "BackgroundAlternate": alternate, **deco, **fgs}
        return {k: fmt(v) for k, v in sorted(values.items())}

    selection_text = bg["desktop"]
    selection_fg = foregrounds(palette, selection_text, mix(selection_text, accent, 0.22), on_accent=True)

    groups = {
        "ColorEffects:Disabled": {
            "Color": "56,56,56", "ColorAmount": "0", "ColorEffect": "0", "ContrastAmount": "0.65",
            "ContrastEffect": "1", "IntensityAmount": "0.1", "IntensityEffect": "2",
        },
        "ColorEffects:Inactive": {
            "ChangeSelectionColor": "true", "Color": "112,111,110", "ColorAmount": "0.025",
            "ColorEffect": "2", "ContrastAmount": "0.1", "ContrastEffect": "2", "Enable": "false",
            "IntensityAmount": "0", "IntensityEffect": "0",
        },
        "Colors:Button": group(bg["card"], bg["panel"]),
        "Colors:Complementary": group(bg["desktop"], bg["window"]),
        "Colors:Header": group(bg["titlebar"], bg["window"]),
        "Colors:Header][Inactive": group(bg["window"], bg["titlebar"]),
        "Colors:Selection": group(accent, mix(accent, bg["window"], 0.55), selection_fg),
        "Colors:Tooltip": group(bg["card"], bg["window"]),
        "Colors:View": group(bg["view"], bg["window"]),
        "Colors:Window": group(bg["window"], bg["titlebar"]),
        "General": {
            "ColorScheme": f"Tide{palette['name']}",
            "Name": f"Tide {palette['name']}",
            "shadeSortColumn": "true",
        },
        "KDE": {"contrast": "4"},
        "WM": {
            "activeBackground": fmt(bg["titlebar"]), "activeBlend": fmt(text["primary"]),
            "activeForeground": fmt(text["primary"]), "inactiveBackground": fmt(bg["window"]),
            "inactiveBlend": fmt(text["muted"]), "inactiveForeground": fmt(text["muted"]),
        },
    }
    return groups


def render(groups: dict[str, dict[str, str]]) -> str:
    lines = ["# Wygenerowane z palety Tide — nie edytuj ręcznie, zmień paletę.", ""]
    for name, values in groups.items():
        lines.append(f"[{name}]")
        lines.extend(f"{k}={v}" for k, v in values.items())
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    palette = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(scheme(palette)))


if __name__ == "__main__":
    main()
