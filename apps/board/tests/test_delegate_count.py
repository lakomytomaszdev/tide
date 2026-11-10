"""Licznik delegatów BoardCard (PT-323, karta K-92): deterministyczny, bez pomiaru CPU.

Sonda (probe.py) podaje migawki wprost do QML (`Scripted.set_snapshot` zawsze emituje snapshotChanged); w produkcji
identyczną migawkę odfiltrowuje już `Backend._got_snapshot`. Testy formułują zachowanie docelowe:
- identyczna migawka nie tworzy nowych delegatów — na stanie „przed” już tak jest (pomiar bazowy w notce K-92 #1:
  0 utworzeń), więc test pilnuje, żeby tak zostało,
- zmiana jednej karty nie tworzy nowych delegatów (karta zmienia się w miejscu) — przed PT-324 kolumna budowała
  swoje karty od nowa; od przyrostowego modelu kolumn (BoardColumn.syncCards) test przechodzi.
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
extra = json.loads(json.dumps(snap["cards"][0]))
extra["id"] = "K-9999"
extra["title"] = "nowa karta"
snap["cards"].append(extra)
set_snapshot(snap)
out["afterNewCard"] = counter()
""")

    def test_counter_sees_initial_and_new_delegates(self):
        # Asercje prawdziwe przed i po #2: nie zakładają przebudowy kolumny przy zmianie jednej karty.
        out = self.counts()
        self.assertEqual(out["start"], out["cards"], "po załadowaniu: jeden delegat na kartę")
        self.assertGreaterEqual(out["afterNewCard"] - out["afterTitleChange"], 1, "nowa karta daje co najmniej 1 delegat")

    def test_identical_snapshots_do_not_recreate_delegates(self):
        out = self.counts()
        self.assertEqual(out["afterFirstIdentical"] - out["start"], 0)
        self.assertEqual(out["afterSecondIdentical"] - out["afterFirstIdentical"], 0)

    def test_changing_one_card_does_not_recreate_delegates(self):
        out = self.counts()
        self.assertEqual(out["afterTitleChange"] - out["afterSecondIdentical"], 0)

    def test_field_change_is_visible_and_only_touched_delegates_are_created(self):
        out = self.probe("""
import measurekit
page_obj = js("page")
counter = lambda: measurekit.read_counter(engine, page_obj)
wait(500)
snap = json.loads(backend.snapshotJson)
ids = [c["id"] for c in snap["cards"] if c.get("boardColumn", c["column"]) != "archived"]
first = ids[0]
title = lambda i: js(f"page.findCard('{i}').card.title")
col_of = lambda i: js(f"page.columnItems().findIndex(c => c.cardItem('{i}') !== null)")
# 1. zmiana pola
before = counter()
snap["cards"][0]["title"] = "NOWY TYTUŁ"
set_snapshot(snap)
out["fieldDelta"] = counter() - before
out["fieldTitle"] = title(snap["cards"][0]["id"])
# 2. dodanie, potem usunięcie przez archiwizację
before = counter()
extra = json.loads(json.dumps(snap["cards"][0]))
extra["id"] = "K-9999"
extra["title"] = "dodana"
snap["cards"].append(extra)
set_snapshot(snap)
out["addDelta"] = counter() - before
out["added"] = title("K-9999")
before = counter()
snap["cards"][-1]["column"] = "archived"
snap["cards"][-1].pop("boardColumn", None)
set_snapshot(snap)
out["archiveDelta"] = counter() - before
out["archivedGone"] = js("page.findCard('K-9999') === null")
# 3. przeniesienie między kolumnami razem ze zmianą pola
target = snap["cards"][0]
old_col = col_of(target["id"])
target["column"] = "done"
target.pop("boardColumn", None)
target["derived"] = None
target["title"] = "przeniesiona i zmieniona"
before = counter()
set_snapshot(snap)
out["moveDelta"] = counter() - before
out["movedTitle"] = title(target["id"])
out["movedColumn"] = col_of(target["id"])
out["movedFrom"] = old_col
out["copies"] = js(f"page.columnItems().filter(c => c.cardItem('{target['id']}') !== null).length")
# 4. zmiana kolejności w kolumnie
col = [c for c in snap["cards"] if c["column"] == "backlog"]
order_before = js("page.columnItems()[0].cards.map(c => c.id)")
snap["cards"].remove(col[0]); snap["cards"].append(col[0])
set_snapshot(snap)
out["order"] = js("page.columnItems()[0].cards.map(c => c.id)")
out["orderModel"] = [js(f"page.columnItems()[0].cardItem('{i}') !== null") for i in out["order"]]
out["itemOrder"] = js("(function () { const col = page.columnItems()[0]; const out = []; "
                      "for (let i = 0; i < col.cards.length; i++) out.push(col.indexOfCard(col.cards[i].id)); return out })()")
""")
        self.assertEqual(out["fieldDelta"], 0)
        self.assertEqual(out["fieldTitle"], "NOWY TYTUŁ")
        self.assertEqual(out["addDelta"], 1)
        self.assertEqual(out["added"], "dodana")
        self.assertEqual(out["archiveDelta"], 0)
        self.assertTrue(out["archivedGone"])
        self.assertEqual(out["moveDelta"], 1, "tylko delegat przeniesionej karty")
        self.assertEqual(out["movedTitle"], "przeniesiona i zmieniona")
        self.assertNotEqual(out["movedColumn"], out["movedFrom"])
        self.assertEqual(out["copies"], 1, "bez duchów i duplikatów")
        self.assertTrue(all(out["orderModel"]))
        self.assertEqual(out["itemOrder"], list(range(len(out["order"]))), "delegaty w kolejności migawki")

    def test_flow_and_tickets_changes_and_edge_cases(self):
        out = self.probe("""
import measurekit
page_obj = js("page")
counter = lambda: measurekit.read_counter(engine, page_obj)
wait(500)
snap = json.loads(backend.snapshotJson)
card0 = snap["cards"][0]
cid = card0["id"]
derived = lambda: js(f"JSON.stringify(page.findCard('{cid}').c.derived)")
card0["derived"] = {"column": card0.get("boardColumn", card0["column"]), "flow": {"state": "running"},
                    "tickets": [{"id": "PT-1", "state": "gotowe"}, {"id": "PT-2", "state": "todo"}]}
set_snapshot(snap)
out["withFlow"] = json.loads(derived())
card0["derived"]["tickets"] = card0["derived"]["tickets"][:1]
set_snapshot(snap)
out["fewerTickets"] = len(json.loads(derived())["tickets"])
card0["derived"] = None
set_snapshot(snap)
out["noFlow"] = derived()
# burza migawek: zostaje ostatnia
for n in range(4):
    snap["cards"][1]["title"] = f"burza {n}"
    set_snapshot(snap)
out["storm"] = js(f"page.findCard('{snap['cards'][1]['id']}').card.title")
# pusta migawka, exists false, usługa niedostępna i powrót
full = json.loads(json.dumps(snap))
empty = json.loads(json.dumps(snap)); empty["cards"] = []
set_snapshot(empty)
out["emptyItems"] = js("page.columnItems().reduce((n, c) => n + c.cards.length, 0)")
set_snapshot(full)
out["backItems"] = js("page.columnItems().reduce((n, c) => n + c.cards.length, 0)")
none = json.loads(json.dumps(snap)); none["exists"] = False; none["cards"] = []
set_snapshot(none)
out["notExists"] = js("page.exists")
set_snapshot(full)
out["existsAgain"] = js("page.exists")
out["specsAfter"] = js("page.columnSpecs.length")
""")
        self.assertEqual(out["withFlow"]["flow"], {"state": "running"})
        self.assertEqual(out["fewerTickets"], 1)
        self.assertIn(out["noFlow"], (None, "null"))
        self.assertEqual(out["storm"], "burza 3")
        self.assertEqual(out["emptyItems"], 0)
        self.assertGreater(out["backItems"], 0)
        self.assertFalse(out["notExists"])
        self.assertTrue(out["existsAgain"])
        self.assertEqual(out["specsAfter"], 6)

    def test_snapshot_mid_drag_keeps_gesture_and_drop(self):
        # Historia 7: nowa migawka (zmiana INNEJ karty) w połowie przeciągania nie przerywa gestu, a puszczenie
        # daje MoveCard na cel, bez duplikatu przeciąganej karty.
        out = self.probe("""
wait(300)
snap = json.loads(backend.snapshotJson)
start, end = card("K-15"), column("todo", fy=0.75)
QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
wait(30)
steps = 12
for i in range(1, steps + 1):
    QTest.mouseMove(window, QPoint(start.x() + (end.x() - start.x()) * i // steps, start.y() + (end.y() - start.y()) * i // steps))
    wait(15)
    if i == 6:
        out["draggingBefore"] = js("page.columnItems()[0].dragging")
        other = next(c for c in snap["cards"] if c["id"] == "K-14")
        other["title"] = "zmiana w trakcie gestu"
        backend.set_snapshot(snap)
        wait(200)
        out["draggingAfter"] = js("page.columnItems()[0].dragging")
wait(80)
QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
wait(150)
out["drop"] = calls()[-1] if calls() else None
out["copies"] = js("page.columnItems().filter(c => c.cardItem('K-15') !== null).length")
out["title"] = js("page.findCard('K-14').card.title")
""")
        self.assertTrue(out["draggingBefore"])
        self.assertTrue(out["draggingAfter"], "migawka nie przerywa gestu")
        self.assertEqual(out["drop"], ["MoveCard", "K-15", "todo", -1])
        self.assertEqual(out["copies"], 1, "bez duplikatu")
        self.assertEqual(out["title"], "zmiana w trakcie gestu")

    def test_refresh_keeps_selection_focus_scroll_and_unchanged_cards_get_no_signal(self):
        out = self.probe("""
wait(300)
snap = json.loads(backend.snapshotJson)
click(card("K-12"))
out["panelBefore"] = js("page.panelOpen && page.panelCardId")
engine.rootContext().setContextProperty("probeTarget", obj("cardTitle"))
js("probeTarget.forceActiveFocus()")
out["fieldFocusBefore"] = obj("cardTitle").property("activeFocus")
js("page.columnItems()[0].flick.contentY = 7")
wait(50)
out["scrollBefore"] = js("page.columnItems()[0].flick.contentY")
from PyQt6.QtCore import QObject, pyqtSlot
class Spy(QObject):
    hits = 0
    @pyqtSlot()
    def hit(self):
        self.hits += 1
spy = Spy()
engine.rootContext().setContextProperty("spy", spy)
js("page.findCard('K-11').cardChanged.connect(spy.hit)")
changed = Spy()
engine.rootContext().setContextProperty("spyChanged", changed)
js("page.findCard('K-14').cardChanged.connect(spyChanged.hit)")  # kontrola: spy naprawdę łapie zmiany
out["selectedBefore"] = js("page.selectedId")
# odświeżenie: identyczna migawka, potem zmiana innej karty
set_snapshot(snap)
other = next(c for c in snap["cards"] if c["id"] == "K-14")
other["title"] += " (zmiana)"
set_snapshot(snap)
out["panel"] = js("page.panelOpen && page.panelCardId")
out["selected"] = js("page.selectedId")
out["fieldFocus"] = obj("cardTitle").property("activeFocus")
out["scroll"] = js("page.columnItems()[0].flick.contentY")
out["signals"] = spy.hits
out["changedSignals"] = changed.hits
""")
        self.assertEqual(out["panelBefore"], "K-12")
        self.assertEqual(out["panel"], "K-12", "panel karty zostaje otwarty")
        self.assertEqual(out["selected"], out["selectedBefore"], "zaznaczenie zostaje")
        self.assertEqual(out["fieldFocus"], out["fieldFocusBefore"], "fokus pola zostaje")
        self.assertEqual(out["scroll"], out["scrollBefore"], "przewinięcie kolumny zostaje")
        self.assertEqual(out["signals"], 0, "niezmieniona karta nie dostaje cardChanged")
        self.assertGreaterEqual(out["changedSignals"], 1, "zmieniona karta dostaje cardChanged")

    def test_service_unavailable_and_back(self):
        out = self.probe("""
wait(300)
snap = json.loads(backend.snapshotJson)
n = lambda: js("page.columnItems().reduce((n, c) => n + c.cards.length, 0)")
out["before"] = n()
js("page.available = false")
wait(100)
out["during"] = n()
js("page.available = true")
set_snapshot(snap)
out["after"] = n()
""")
        self.assertGreater(out["before"], 0)
        self.assertEqual(out["after"], out["before"], "powrót usługi przywraca te same karty")


if __name__ == "__main__":
    unittest.main()
