"""Aplety z własnym compactRepresentation mają też fullRepresentation.

Bez fullRepresentation libplasma bierze za pełną reprezentację sam korzeń apletu (PlasmoidItem), a w panelu, gdy
aplet jest zwinięty, chowa ją — razem z kafelkiem, który w niej siedzi. Kafelek „Kamera” (PT-82) był przez to
w konfiguracji docka Stream, ale się nie rysował (podgląd scripts/stream_preview.sh, plasmashell 6.8). Błędu nie
widać ani w qml6, ani w plasmashell.log, więc pilnuje go ten test.
"""
import os
import unittest

from . import WIDGETS_DIR


def applets():
    for dirpath, _dirs, names in os.walk(WIDGETS_DIR):
        if "main.qml" in names and dirpath.endswith(os.path.join("contents", "ui")):
            path = os.path.join(dirpath, "main.qml")
            with open(path, encoding="utf-8") as fh:
                yield os.path.relpath(path, WIDGETS_DIR), fh.read()


class RepresentationTest(unittest.TestCase):
    def test_compact_applets_have_full_representation(self):
        checked = 0
        for name, text in applets():
            if "compactRepresentation:" not in text:
                continue
            checked += 1
            with self.subTest(name):
                self.assertIn("fullRepresentation:", text,
                              "bez fullRepresentation panel chowa korzeń apletu razem z kafelkiem")
        self.assertGreater(checked, 10)


if __name__ == "__main__":
    unittest.main()
