"""Motyw podświetlania składni generowany z palety (K-36, PT-351, M1)."""
import json
import re
import unittest
from pathlib import Path

from theme import palette as palettes
from theme.colors.generate import contrast, hex_rgb
from theme.syntax.generate import MIN_CONTRAST, STYLES, THEME_NAME, TINT_ALPHA, diff_backgrounds, render, theme

REPO = Path(__file__).resolve().parents[3]


class SyntaxThemeTest(unittest.TestCase):
    def variants(self):
        return [(name, palettes.load(name)) for name in palettes.names()]

    def test_every_text_color_has_a_contrast_of_4_5_on_all_three_diff_backgrounds(self):
        for name, palette in self.variants():
            backgrounds = diff_backgrounds(palette)
            self.assertEqual(len(backgrounds), 3)
            for style, values in theme(palette)["text-styles"].items():
                for key in ("text-color", "selected-text-color"):
                    for bg in backgrounds:
                        with self.subTest(variant=name, style=style, key=key, bg=bg):
                            self.assertGreaterEqual(contrast(hex_rgb(values[key]), bg), MIN_CONTRAST)

    def test_styles_have_no_background_so_diff_tints_stay_visible(self):
        for _name, palette in self.variants():
            for style, values in theme(palette)["text-styles"].items():
                self.assertEqual(set(values), {"text-color", "selected-text-color"}, style)

    def test_all_module_styles_are_covered_and_colors_come_from_the_palette(self):
        palette = palettes.load("mist")
        styles = theme(palette)["text-styles"]
        self.assertEqual(len(STYLES), 31)
        self.assertEqual(set(styles), set(STYLES))
        self.assertEqual(styles["Normal"]["text-color"], palette["text"]["primary"])
        self.assertEqual(styles["Keyword"]["text-color"], palette["pastel"]["lavender"])

    def test_output_is_json_with_the_theme_name(self):
        data = json.loads(render(palettes.load("mist")))
        self.assertEqual(data["metadata"]["name"], THEME_NAME)

    def test_tint_alpha_matches_the_diff_view(self):
        source = (REPO / "apps" / "common" / "qml" / "git" / "DiffView.qml").read_text(encoding="utf-8")
        found = re.search(r"real tintAlpha:\s*([0-9.]+)", source)
        self.assertIsNotNone(found)
        self.assertEqual(float(found.group(1)), TINT_ALPHA)


if __name__ == "__main__":
    unittest.main()
