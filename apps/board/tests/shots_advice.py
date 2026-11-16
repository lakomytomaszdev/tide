"""Zrzuty PNG do notki ticketu PT-506 (nie jest modułem zestawu testów: nazwa bez `test_`).

`python3 -m unittest apps.board.tests.shots_advice` uruchamia sondy test_decision_advice z zapisem zrzutów do
build/advice-shots/ (katalog ignorowany przez gita): prośba o zgodę z „polecane” i rozwiniętym uzasadnieniem opiekuna
oraz z rozwiniętym skryptem, w oknie decyzji i w „Twój ruch” pełnej karty, każde w rozmiarze treści 1180×1000 (okno
1380×1000 z panelem bocznym 200 px) i najwęższego okna Tide 1380×620 bez panelu bocznego (1180×620). Zawsze
QT_QPA_PLATFORM=offscreen (ProbeCase) — bez okien na pulpicie.
"""
import os

from . import REPO
from .test_decision_advice import ConsentDialogTest, YourMoveTest

os.environ["TIDE_ADVICE_SHOTS"] = os.path.join(REPO, "build", "advice-shots")

__all__ = ["ConsentDialogTest", "YourMoveTest"]
