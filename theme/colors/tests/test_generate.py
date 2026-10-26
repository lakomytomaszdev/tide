"""Testy schematu kolorów generowanego z palety."""
import json
import unittest
from pathlib import Path

from theme.colors.generate import hex_rgb, scheme

PALETTE = json.loads((Path(__file__).resolve().parents[2] / "palette" / "mist.json").read_text())


def luminance(rgb):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def parse(value):
    return tuple(int(x) for x in value.split(","))


class ColorSchemeTest(unittest.TestCase):
    def setUp(self):
        self.groups = scheme(PALETTE)
        self.color_groups = {k: v for k, v in self.groups.items() if k.startswith("Colors:")}

    def test_text_is_readable_on_every_background(self):
        readable = ("ForegroundNormal", "ForegroundInactive", "ForegroundLink", "ForegroundNegative",
                    "ForegroundNeutral", "ForegroundPositive", "ForegroundVisited")
        for name, values in self.color_groups.items():
            for bg_key in ("BackgroundNormal", "BackgroundAlternate"):
                if name == "Colors:Selection" and bg_key == "BackgroundAlternate":
                    continue  # tło nieaktywnego zaznaczenia sprawdza osobny test
                for fg_key in readable:
                    with self.subTest(group=name, bg=bg_key, fg=fg_key):
                        ratio = contrast(parse(values[fg_key]), parse(values[bg_key]))
                        self.assertGreaterEqual(ratio, 4.5, f"{ratio:.2f}")

    def test_inactive_selection_keeps_primary_text_readable(self):
        values = self.color_groups["Colors:Selection"]
        primary = hex_rgb(PALETTE["text"]["primary"])
        self.assertGreaterEqual(contrast(primary, parse(values["BackgroundAlternate"])), 4.5)

    def test_colors_come_from_the_palette(self):
        window = self.color_groups["Colors:Window"]
        self.assertEqual(parse(window["BackgroundNormal"]), hex_rgb(PALETTE["background"]["window"]))
        self.assertEqual(parse(window["ForegroundNormal"]), hex_rgb(PALETTE["text"]["primary"]))
        self.assertEqual(parse(self.color_groups["Colors:Selection"]["BackgroundNormal"]), hex_rgb(PALETTE["accent"]))
        self.assertEqual(parse(window["ForegroundNegative"]), hex_rgb(PALETTE["pastel"]["rose"]))

    def test_scheme_is_named_after_the_variant(self):
        self.assertEqual(self.groups["General"]["ColorScheme"], "TideMist")


if __name__ == "__main__":
    unittest.main()
