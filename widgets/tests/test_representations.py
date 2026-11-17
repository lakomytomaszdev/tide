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


# --- Tide public copy: skipped tests (fix-tests.py) ---
import unittest as _pub_unittest


def _pub_skip(reason, targets):
    g = globals()
    for name in targets:
        cls, _, meth = name.partition('.')
        klass = g.get(cls)
        if klass is None:
            continue
        if not meth:
            g[cls] = _pub_unittest.skip(reason)(klass)
        elif hasattr(klass, meth):
            setattr(klass, meth, _pub_unittest.skip(reason)(getattr(klass, meth)))


_pub_skip('public copy: the personal theme and Stream widgets are not published', [
    'RepresentationTest.test_compact_applets_have_full_representation',
])
# --- end of public copy block ---
