"""Zrzuty PNG do notki ticketu PT-501 (nie jest modułem zestawu testów: nazwa bez `test_`).

`python3 -m unittest apps.board.tests.shots_prose` uruchamia sondy test_prose_decision z zapisem zrzutów do
build/prose-shots/ (katalog ignorowany przez gita): okno decyzji i „Twój ruch” pełnej karty, każde w rozmiarze
treści 1180×1000 i najwęższego okna Tide 1380×620 bez panelu bocznego (1180×620). Zawsze QT_QPA_PLATFORM=offscreen
(ProbeCase) — bez okien na pulpicie.
"""
import os

from . import REPO
from .test_prose_decision import ProseInDecisionTest

os.environ["TIDE_PROSE_SHOTS"] = os.path.join(REPO, "build", "prose-shots")

__all__ = ["ProseInDecisionTest"]
