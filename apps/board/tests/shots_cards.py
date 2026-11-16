"""Zrzuty PNG Tablicy z kartami z inwentarza (PT-505; nie jest modułem zestawu testów: nazwa bez `test_`).

`python3 -m unittest apps.board.tests.shots_cards` zapisuje zrzuty widoku Tablica (dane `inventoryfix`: zwarte karty
w Gotowe, czekająca, zaznaczona, każdy znacznik i przycisk) w dwóch rozmiarach treści widoku: 1180×1000 (okno
1380×1000 z panelem bocznym 200 px) i 1180×620 (najwęższe okno Tide 1380×620). Kolumny są przewijane krokami, żeby
zrzuty objęły wszystkie karty. Etykieta (przed/po) z pliku `shots-label.txt` w katalogu wyjściowym kroku.
Zawsze QT_QPA_PLATFORM=offscreen (ProbeCase): żadnych okien na pulpicie.
"""
import os
import unittest

from . import REPO
from .inventoryfix import inventory_data
from .test_view import ProbeCase

STEP_OUT = REPO + ".step/out"
SIZES = ((1180, 1000), (1180, 620))

SCENARIO = r"""
SHOTS = %(dir)r
LABEL = %(label)r
SIZES = %(sizes)r
wait(600)
js("page.selectedId = 'K-5'")
for width, height in SIZES:
    window.resize(width, height)
    wait(500)
    js("page.columnItems().forEach(c => { c.flick.contentY = 0 })")
    shots = []
    for step in range(5):
        wait(250)
        path = "%%s/%%s-%%dx%%d-%%d.png" %% (SHOTS, LABEL, width, height, step + 1)
        window.screen().grabWindow(window.winId()).save(path)
        shots.append(path)
        more = js("page.columnItems().some(c => c.flick.contentY + c.flick.height < c.flick.contentHeight - 4)")
        if not more:
            break
        js("page.columnItems().forEach(c => { c.flick.contentY = Math.min(c.flick.contentY + c.flick.height - 60, "
           "Math.max(0, c.flick.contentHeight - c.flick.height)) })")
    out["%%dx%%d" %% (width, height)] = shots
"""


class CardShots(ProbeCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.data = inventory_data(os.path.join(cls.tmp, "inv"))

    def test_write_shots(self):
        folder = os.path.join(STEP_OUT, "shots") if os.path.isdir(STEP_OUT) else os.path.join(REPO, "build", "card-shots")
        os.makedirs(folder, exist_ok=True)
        label_file = os.path.join(STEP_OUT, "shots-label.txt")
        label = "run"
        if os.path.exists(label_file):
            with open(label_file, encoding="utf-8") as fh:
                label = fh.read().strip() or "run"
        out = self.probe(SCENARIO % {"dir": folder, "label": label, "sizes": SIZES}, data=self.data)
        for paths in out.values():
            for path in paths:
                self.assertGreater(os.path.getsize(path), 5000, path)


if __name__ == "__main__":
    unittest.main()
