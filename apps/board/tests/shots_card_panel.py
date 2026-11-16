"""Zrzuty PNG do notki ticketu PT-504 (nie jest modułem zestawu testów: nazwa bez `test_`).

`python3 -m unittest apps.board.tests.shots_card_panel` zapisuje w build/card-panel-shots/ (katalog ignorowany przez
gita; `TIDE_CARD_SHOTS=<katalog>` zmienia miejsce) panel karty na Tablicy w trzech stanach — Czytaj, Czytaj z konfliktem
YouTracka, Edytuj — w dwóch rozmiarach: (wide) treść widoku 1180 px w oknie 1380×1000 z paskiem bocznym 200 px
i (narrow) najwęższe okno Tide 1380×620 (`minimumWidth`×`minimumHeight` z apps/tide/qml/Main.qml). Pasek boczny
udajemy marginesem prawym widoku (Tide.Sheet liczy szerokość od okna, jak w prawdziwym Tide), a zrzut przycinamy do
widoku. Zawsze QT_QPA_PLATFORM=offscreen (ProbeCase) — bez okien na pulpicie.
"""
import os
import shutil
import unittest

from . import REPO
from .test_card_panel import MODE_PY, with_description
from .test_view import ProbeCase
from .test_youtrack_view import youtrack_data

DIR = os.environ.get("TIDE_CARD_SHOTS") or os.path.join(REPO, "build", "card-panel-shots")

SCENARIO = MODE_PY + """
import os
from PyQt6 import sip
from PyQt6.QtQuick import QQuickWindow
shots = SHOTS_DIR
os.makedirs(shots, exist_ok=True)

def grab(name):
    for size, (w, h) in (("wide", (1380, 1000)), ("narrow", (1380, 620))):
        window.setWidth(w)
        window.setHeight(h)
        js("page.parent.anchors.rightMargin = 200")   # panel boczny okna Tide: widok ma 1180 px
        wait(500)
        image = sip.cast(window, QQuickWindow).grabWindow()
        image.copy(0, 0, w - 200, h).save(os.path.join(shots, name + "-" + size + ".png"))

click(card("K-7"))
until(lambda: js("page.panelOpen"))
wait(300)
grab("read-conflict")
mode("edit")
until(lambda: js("page.cardPanel.mode") == "edit")
grab("edit")
js("page.closePanel()")
window.setWidth(1380)
window.setHeight(1000)
wait(400)
click(card("K-12"))
until(lambda: js("page.panelOpen"))
wait(300)
grab("read")
out["files"] = sorted(os.listdir(shots))
"""


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class CardPanelShots(ProbeCase):
    def test_write_shots(self):
        data = with_description(youtrack_data(self.data), "K-7")
        for card in data["cards"]:
            if card["id"] == "K-12":
                card["description"] = ("## Sedno\n\nWspólny szkielet zadań importu: jeden sposób uruchamiania, "
                                       "jeden raport błędów.\n\n## Kryteria\n\n- import z pliku i z adresu\n"
                                       "- te same komunikaty błędów\n\nZob. https://example.com/spec")
        out = self.probe(SCENARIO.replace("SHOTS_DIR", repr(DIR)), data)
        self.assertEqual(len(out["files"]), 6, out)


if __name__ == "__main__":
    unittest.main()
