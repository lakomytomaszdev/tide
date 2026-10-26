"""Testy logo generowanego z palety."""
import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from theme.logo.generate import WAVES, svg

PALETTE = json.loads((Path(__file__).resolve().parents[2] / "palette" / "mist.json").read_text())
NS = "{http://www.w3.org/2000/svg}"


def strokes(document):
    return [p.get("stroke") for p in ET.fromstring(document).iter(f"{NS}path")]


class LogoTest(unittest.TestCase):
    def test_waves_use_palette_pastels_in_order(self):
        expected = [PALETTE["pastel"][name] for name in WAVES]
        self.assertEqual(strokes(svg(PALETTE)), expected)
        self.assertEqual(strokes(svg(PALETTE, small=True)), expected)

    def test_full_logo_sits_on_a_card_tile(self):
        rect = ET.fromstring(svg(PALETTE)).find(f"{NS}rect")
        self.assertEqual(rect.get("fill"), PALETTE["background"]["card"])

    def test_small_logo_has_no_tile(self):
        self.assertIsNone(ET.fromstring(svg(PALETTE, small=True)).find(f"{NS}rect"))

    def test_symbolic_logo_follows_the_theme_text_color(self):
        paths = list(ET.fromstring(svg(PALETTE, symbolic=True)).iter(f"{NS}path"))
        self.assertEqual(len(paths), 3)
        self.assertTrue(all(p.get("class") == "ColorScheme-Text" for p in paths))


if __name__ == "__main__":
    unittest.main()
