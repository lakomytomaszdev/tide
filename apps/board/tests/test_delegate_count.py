"""Licznik delegatów BoardCard (PT-323, karta K-92): deterministyczny, bez pomiaru CPU.

Sonda (probe.py) podaje migawki wprost do QML (`Scripted.set_snapshot` zawsze emituje snapshotChanged); w produkcji
identyczną migawkę odfiltrowuje już `Backend._got_snapshot`. Testy formułują zachowanie docelowe:
- identyczna migawka nie tworzy nowych delegatów — na stanie „przed” już tak jest (pomiar bazowy w notce K-92 #1:
  0 utworzeń), więc test pilnuje, żeby tak zostało,
- zmiana jednej karty nie tworzy nowych delegatów (karta zmienia się w miejscu) — na stanie „przed” kolumna buduje
  swoje karty od nowa, więc to oczekiwany błąd, który zdejmuje ticket #2 karty K-92 (przyrostowy model kolumn).
Liczba utworzeń „przed” jest pomiarem bazowym w notce (apps/board/tests/measure_board.py), nie wartością oczekiwaną.
"""
import os
import shutil
import unittest

from .test_view import ProbeCase


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class DelegateCountTest(ProbeCase):
    def counts(self) -> dict:
        return self.probe("""
import measurekit
page_obj = js("page")
counter = lambda: measurekit.read_counter(engine, page_obj)
wait(500)
snap = json.loads(backend.snapshotJson)
out["cards"] = len(snap["cards"])
out["start"] = counter()
set_snapshot(snap)
out["afterFirstIdentical"] = counter()
set_snapshot(snap)
out["afterSecondIdentical"] = counter()
snap["cards"][0]["title"] += " (zmiana)"
set_snapshot(snap)
out["afterTitleChange"] = counter()
""")

    def test_counter_sees_initial_and_rebuilt_delegates(self):
        out = self.counts()
        self.assertEqual(out["start"], out["cards"], "po załadowaniu: jeden delegat na kartę")
        self.assertGreater(out["afterTitleChange"], out["afterSecondIdentical"], "licznik widzi przebudowę kolumny")

    def test_identical_snapshots_do_not_recreate_delegates(self):
        out = self.counts()
        self.assertEqual(out["afterFirstIdentical"] - out["start"], 0)
        self.assertEqual(out["afterSecondIdentical"] - out["afterFirstIdentical"], 0)

    # Zdejmuje #2 karty K-92 (przyrostowy model kolumn): usunąć `expectedFailure` razem z tym komentarzem.
    @unittest.expectedFailure
    def test_changing_one_card_does_not_recreate_delegates(self):
        out = self.counts()
        self.assertEqual(out["afterTitleChange"] - out["afterSecondIdentical"], 0)


if __name__ == "__main__":
    unittest.main()
