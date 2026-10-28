"""Schemat kolorów wariantu Streamer: TideStreamer z palety-nakładki, z wiersza poleceń."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from theme.colors.tests.test_generate import contrast, parse
from theme.colors.generate import scheme
from theme.palette import load

ROOT = Path(__file__).resolve().parents[3]


class StreamerSchemeTest(unittest.TestCase):
    def test_cli_accepts_the_overlay_palette_and_names_the_scheme(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "TideStreamer.colors"
            subprocess.run(["/usr/bin/python3", "theme/colors/generate.py", "theme/palette/streamer.json", str(out)],
                           cwd=ROOT, check=True)
            text = out.read_text()
        self.assertIn("ColorScheme=TideStreamer\n", text)
        self.assertIn("Name=Tide Streamer\n", text)
        self.assertIn("[Colors:Window]\nBackgroundAlternate=17,49,54\nBackgroundNormal=16,46,56\n", text)

    def test_every_foreground_is_readable_on_its_backgrounds(self):
        # Wszystkie pary Foreground*/Background* schematu ≥ 4,5:1, poza ForegroundInactive (tekst
        # celowo przygaszony) i tłem nieaktywnego zaznaczenia — w Mist też są niżej.
        for name, values in scheme(load("streamer")).items():
            if not name.startswith("Colors:"):
                continue
            backgrounds = ["BackgroundNormal"] + ([] if name == "Colors:Selection" else ["BackgroundAlternate"])
            for bg in backgrounds:
                for fg in (k for k in values if k.startswith("Foreground") and k != "ForegroundInactive"):
                    with self.subTest(group=name, background=bg, foreground=fg):
                        self.assertGreaterEqual(contrast(parse(values[fg]), parse(values[bg])), 4.5)

if __name__ == "__main__":
    unittest.main()
