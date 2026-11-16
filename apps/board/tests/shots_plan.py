"""Zrzuty PNG do notki ticketu PT-503 (nie jest modułem zestawu testów: nazwa bez `test_`).

`TIDE_PLAN_SHOTS=<katalog> python3 -m unittest apps.board.tests.shots_plan` zapisuje zrzuty widoku karty: plan z 13 falami
(bieżąca fala, 12., przypięta) i historię z ponad 200 zdarzeniami, każde w rozmiarze treści 1180×1000 (okno 1380×1000
z panelem bocznym 200 px) i najwęższego okna Tide 1380×620 (treść 1180×620). Zawsze offscreen (ProbeCase) — bez okien
na pulpicie. Katalog wyjściowy podaje wołający (katalog kroku, nie repozytorium).
"""
import os
import unittest

from . import REPO
from .decisionfix import full_d
from .test_plan_table import STATE_PY, wave_data
from .test_view import ProbeCase

SHOT_PY = STATE_PY + r'''
import os
from PyQt6 import sip
from PyQt6.QtQuick import QQuickWindow
shots = os.environ["TIDE_PLAN_SHOTS"]

def grab(name):
    os.makedirs(shots, exist_ok=True)
    for size, (w, h) in (("wide", (1180, 1000)), ("narrow", (1180, 620))):
        window.setWidth(w)
        window.setHeight(h)
        wait(500)
        quick = sip.cast(window, QQuickWindow)
        quick.grabWindow().save(os.path.join(shots, name + "-" + size + ".png"))
    window.setWidth(1440)
    window.setHeight(900)
    wait(200)

click(card("K-8"))
wait(300)
'''


os.environ.setdefault("TIDE_PLAN_SHOTS", os.path.join(REPO, "build", "plan-shots"))
class PlanShots(ProbeCase):
    def test_plan_and_history_shots(self):
        data = wave_data(full_d(self.data), 11, history=230, old=12)
        self.probe(SHOT_PY + """
js(V + ".ticketSettingsEnabled = true")
js(V + ".selectTab('plan')")
wait(300)
grab("plan")
js(V + ".selectTab('overview')")
wait(300)
grab("overview")
js(V + ".selectTab('history')")
wait(300)
grab("history")
""", data)


if __name__ == "__main__":
    unittest.main()
