"""Tokens.qml z palety-nakładki Streamer: kolory marki, kształty i wymiary z Mist."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def tokens(palette: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["/usr/bin/python3", "widgets/stage.py", "widgets/work", f"{tmp}/work", palette],
                       cwd=ROOT, check=True, capture_output=True)
        return Path(f"{tmp}/work/contents/ui/tide/Tokens.qml").read_text()


class TokensVariantTest(unittest.TestCase):
    def test_streamer_tokens_have_every_mist_property(self):
        mist = tokens("theme/palette/mist.json")
        kamp = tokens("theme/palette/streamer.json")
        names = lambda text: [line.split(":")[0] for line in text.splitlines() if "property" in line]  # noqa: E731
        self.assertEqual(names(kamp), names(mist))
        self.assertIn('readonly property string name: "Streamer"', kamp)
        self.assertIn('readonly property color accent: "#c5a463"', kamp)
        self.assertIn('readonly property color textPrimary: "#f1e9d3"', kamp)
        self.assertIn("readonly property int radiusCard: 18", kamp)


if __name__ == "__main__":
    unittest.main()
