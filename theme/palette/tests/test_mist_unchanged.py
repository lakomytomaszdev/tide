"""Mist daje te same pliki co przed wariantami palety (wymaganie nadrzędne K-29: praca bez zmian).

Wszystkie generatory, które czytają paletę wariantu, składają z mist.json pliki do katalogu
tymczasowego, a ich sumy SHA-256 muszą się zgadzać z zapisanymi w mist-golden.json. Sumy
zapisano z gałęzi master przed PT-34. Zamierzona zmiana wyglądu Mist: `make mist-golden`
i commit nowego pliku sum razem ze zmianą (różnicę widać wtedy w diffie).
"""
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
import sys  # noqa: E402

sys.path.insert(0, str(ROOT))
from theme.palette import outputs  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "mist-golden.json"


class MistUnchangedTest(unittest.TestCase):
    def test_mist_outputs_match_the_recorded_sums(self):
        expected = json.loads(GOLDEN.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            outputs.build("mist", Path(tmp))
            actual = outputs.manifest(Path(tmp))
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(k for k in set(expected) & set(actual) if expected[k] != actual[k])
        self.assertEqual((missing, extra, changed), ([], [], []),
                         "Pliki z palety Mist różnią się od zapisanych (make mist-golden, jeśli to zamierzone).")

    def test_golden_covers_every_component(self):
        parts = {name.split("/")[0] for name in json.loads(GOLDEN.read_text())}
        self.assertEqual(parts, set(outputs.COMPONENTS))


if __name__ == "__main__":
    unittest.main()
