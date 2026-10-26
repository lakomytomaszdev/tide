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

    def test_splash_bar_is_thin_and_background_dimmed(self):
        splash = PALETTE["splash"]
        self.assertLessEqual(splash["barHeight"], 6)  # „cienki pasek”, jak mierniki (4–6 px)
        self.assertLess(splash["barHeight"], splash["barWidth"])
        self.assertGreater(splash["dim"], 0)
        self.assertLess(splash["dim"], 1)
        self.assertGreater(splash["pulseMs"], 0)

    def test_wallpaper_variant_is_known(self):
        self.assertIn(PALETTE["wallpaper"].get("variant", "blobs"), ("blobs", "cat"))
        # Plamy zostają w palecie jako działająca alternatywa.
        self.assertTrue(PALETTE["wallpaper"]["blobs"])

    def test_lock_cat_timings_and_splash_fade(self):
        # Kot na ekranie blokady: budzenie wyraźnie widoczne (ok. 0,6–1,2 s), zasypianie wolniejsze
        # i łagodniejsze (do 2,5 s), drzemka po kilku sekundach bezczynności (2–6 s).
        lock = PALETTE["lockscreen"]
        self.assertGreaterEqual(lock["wakeMs"], 600)
        self.assertLessEqual(lock["wakeMs"], 1200)
        self.assertGreater(lock["sleepMs"], lock["wakeMs"])
        self.assertLessEqual(lock["sleepMs"], 2500)
        self.assertGreaterEqual(lock["dozeAfterMs"], 2000)
        self.assertLessEqual(lock["dozeAfterMs"], 6000)
        # Wejście ekranu ładowania krótkie.
        self.assertGreater(PALETTE["splash"]["fadeMs"], 0)
        self.assertLessEqual(PALETTE["splash"]["fadeMs"], 550)

    def test_shadow_is_soft(self):
        self.assertGreater(PALETTE["shadow"]["size"], 0)
        self.assertLessEqual(PALETTE["shadow"]["opacity"], 0.5)


if __name__ == "__main__":
    unittest.main()
