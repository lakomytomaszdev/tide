"""Testy spójności palety: wartości, z których generatory budują kształty."""
import json
import unittest
from pathlib import Path

PALETTE = json.loads((Path(__file__).resolve().parents[1] / "mist.json").read_text())


class PaletteTest(unittest.TestCase):
    def test_radii_are_positive_whole_pixels(self):
        for name in ("small", "bar", "card", "window", "dock"):
            with self.subTest(radius=name):
                self.assertIsInstance(PALETTE["radius"][name], int)
                self.assertGreater(PALETTE["radius"][name], 0)

    def test_bar_corners_fit_both_bars(self):
        # Ramka panelu nie może być niższa niż dwa narożniki (FrameSvg nie zmniejsza narożników).
        for size in ("bar", "barSlim"):
            with self.subTest(size=size):
                self.assertGreaterEqual(PALETTE["size"][size], 2 * PALETTE["radius"]["bar"])

    def test_opacities_are_fractions_and_blur_is_more_transparent(self):
        opacity = PALETTE["opacity"]
        for name, value in opacity.items():
            with self.subTest(opacity=name):
                self.assertGreater(value, 0)
                self.assertLessEqual(value, 1)
        self.assertLess(opacity["panel"], opacity["withoutBlur"])
        self.assertLess(opacity["popup"], opacity["withoutBlur"])
        self.assertLess(opacity["border"], 0.2)

    def test_shadow_is_soft(self):
        self.assertGreater(PALETTE["shadow"]["size"], 0)
        self.assertLessEqual(PALETTE["shadow"]["opacity"], 0.5)


if __name__ == "__main__":
    unittest.main()
