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
        self.assertIn("[Colors:Window]\nBackgroundAlternate=27,55,64\nBackgroundNormal=16,46,56\n", text)

    def test_text_is_readable_on_every_background(self):
        for name, values in scheme(load("streamer")).items():
            if not name.startswith("Colors:"):
                continue
            with self.subTest(group=name):
                self.assertGreaterEqual(contrast(parse(values["ForegroundNormal"]), parse(values["BackgroundNormal"])), 4.5)


if __name__ == "__main__":
    unittest.main()
