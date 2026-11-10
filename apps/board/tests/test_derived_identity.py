"""Tożsamość wiązań pochodnych i pojedyncze parsowanie migawki (PT-326, karta K-92).

Wiązania pochodne BoardPage (`lanes`, `youtrackNotes`, `panelCard`, `fullCard`, `errorLines`) dostają nową wartość
tylko wtedy, gdy zmieniła się ich treść; wiersze widoku „Wiersz na projekt” zachowują delegaty, a odznaka w backendzie
nie parsuje tej samej migawki drugi raz. Obserwacja: sygnały `...Changed`, licznik delegatów BoardCard
(`measurekit.read_counter`) i to, czy delegat wiersza (objectName `boardLane-<klucz>`) żyje dalej.
"""
import importlib.util
import json
import os
import shutil
import unittest

from . import APP_DIR
from .test_view import ProbeCase

HAS_QT = bool(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"))

SPIES = """
import measurekit
from PyQt6 import sip
from PyQt6.QtCore import QObject, pyqtSlot
class Spy(QObject):
    hits = 0
    @pyqtSlot()
    def hit(self):
        self.hits += 1
spies = {}
def spy(name):
    s = Spy()
    spies[name] = s
    engine.rootContext().setContextProperty("spy_" + name, s)
    js(f"page.{name}Changed.connect(spy_{name}.hit)")
page_obj = js("page")
counter = lambda: measurekit.read_counter(engine, page_obj)
hits = lambda name: spies[name].hits
"""


@unittest.skipUnless(HAS_QT, "brak Qt Quick")
class LanesIdentityTest(ProbeCase):
    def test_lanes_keep_rows_and_delegates_for_unchanged_snapshots(self):
        out = self.probe(SPIES + """
js("page.layoutMode = 'lanes'")
wait(300)
snap = json.loads(backend.snapshotJson)
spy("lanes")
keys = js("page.lanes.map(l => l.key)")
engine.rootContext().setContextProperty("lanesFlick", obj("boardLanes"))
rowItems = "lanesFlick.contentItem.children[0].children.filter(c => String(c.objectName).indexOf('boardLane-') === 0)"
out["laneObjects"] = js(rowItems + ".length")
engine.rootContext().setContextProperty("savedRows", js(rowItems))
rowsKept = lambda: js("savedRows.length > 0 && savedRows.every(s => " + rowItems + ".indexOf(s) >= 0)")
out["keys"] = keys
out["rowsFound"] = out["laneObjects"] == len(keys)
out["laneCards"] = js("page.lanes.map(l => l.cards.length)")
before = counter()
set_snapshot(snap)
set_snapshot(snap)
out["identicalDelegates"] = counter() - before
out["identicalLanesChanged"] = hits("lanes")
# zmiana jednego pola jednej karty: nowa tablica `lanes`, wiersze i delegaty zostają
before = counter()
snap["cards"][0]["title"] = "zmieniony tytuł"
set_snapshot(snap)
out["fieldDelegates"] = counter() - before
out["fieldLanesChanged"] = hits("lanes")
out["rowsAlive"] = rowsKept()
out["title"] = js("page.cards.find(c => c.id === '%s').title" % snap["cards"][0]["id"])
# ta sama kolejność kart po przemieszaniu innego wiersza nie dotyka reszty; nowy projekt dokłada wiersz
extra = json.loads(json.dumps(snap["cards"][0]))
extra.update(id="K-9999", title="nowy projekt", project="zzz-nowy", projectName="Zzz nowy")
snap["cards"].append(extra)
before = counter()
set_snapshot(snap)
out["newLaneDelegates"] = counter() - before
out["newLaneKeys"] = js("page.lanes.map(l => l.key)")
out["rowsAliveAfterNew"] = rowsKept()
laneNames = lambda: js(rowItems + ".map(c => c.objectName)")
out["newRowExists"] = "boardLane-zzz-nowy" in laneNames()
# znika cała grupa: pozostałe wiersze żyją, usunięty znika
snap["cards"].pop()
set_snapshot(snap)
out["afterRemove"] = js("page.lanes.map(l => l.key)")
out["removedGone"] = "boardLane-zzz-nowy" not in laneNames()
out["rowsAliveAfterRemove"] = rowsKept()
""")
        self.assertTrue(out["rowsFound"], out["keys"])
        self.assertGreaterEqual(len(out["keys"]), 3)
        self.assertEqual(out["identicalDelegates"], 0)
        self.assertEqual(out["identicalLanesChanged"], 0, "ta sama migawka dwa razy nie zmienia `lanes`")
        self.assertEqual(out["fieldDelegates"], 0, "zmiana pola jednej karty: 0 nowych delegatów BoardCard")
        self.assertGreaterEqual(out["fieldLanesChanged"], 1, "kontrola: zmieniona karta zmienia `lanes`")
        self.assertTrue(out["rowsAlive"], "nowa tablica lanes nie niszczy wierszy")
        self.assertEqual(out["title"], "zmieniony tytuł")
        # Nowa karta ma jeden delegat w ukrytym widoku „Razem” i jeden w nowym wierszu, nic ponadto.
        self.assertEqual(out["newLaneDelegates"], 2)
        self.assertEqual(out["newLaneKeys"], out["keys"] + ["zzz-nowy"])
        self.assertTrue(out["rowsAliveAfterNew"])
        self.assertTrue(out["newRowExists"])
        self.assertEqual(out["afterRemove"], out["keys"])
        self.assertTrue(out["removedGone"])
        self.assertTrue(out["rowsAliveAfterRemove"])

    def test_lanes_follow_filters_and_return_to_together(self):
        out = self.probe(SPIES + """
js("page.layoutMode = 'lanes'")
wait(300)
out["all"] = js("page.lanes.map(l => l.key + ':' + l.cards.length)")
js("page.tagFilter = 'pilne'")
wait(200)
out["tag"] = js("page.lanes.map(l => l.key + ':' + l.cards.length)")
out["tagCards"] = js("page.cards.map(c => c.id)")
js("page.tagFilter = ''")
wait(200)
out["restored"] = js("page.lanes.map(l => l.key + ':' + l.cards.length)")
js("page.query = 'zzz-nic-nie-pasuje'")
wait(200)
out["emptyLanes"] = js("page.lanes.length")
engine.rootContext().setContextProperty("lanesFlick", obj("boardLanes"))
out["emptyRows"] = js("lanesFlick.contentItem.children[0].children.filter(c => String(c.objectName).indexOf('boardLane-') === 0).length")

js("page.query = ''")
wait(200)
js("page.layoutMode = 'together'")
wait(200)
out["togetherLanes"] = js("page.lanes.length")
out["togetherCards"] = js("page.cards.length")
js("page.layoutMode = 'lanes'")
wait(200)
out["back"] = js("page.lanes.map(l => l.key + ':' + l.cards.length)")
""" % ([]))
        self.assertEqual(out["tagCards"], ["K-12", "K-7"])
        self.assertEqual(sum(int(x.split(":")[1]) for x in out["tag"]), 2)
        self.assertEqual(out["restored"], out["all"])
        self.assertEqual(out["emptyLanes"], 0)
        self.assertEqual(out["togetherLanes"], 0, "w „Razem” wierszy się nie liczy")
        self.assertGreater(out["togetherCards"], 0)
        self.assertEqual(out["back"], out["all"])

    def test_edge_snapshots_in_lanes_mode(self):
        out = self.probe(SPIES + """
js("page.layoutMode = 'lanes'")
wait(300)
snap = json.loads(backend.snapshotJson)
empty = json.loads(json.dumps(snap)); empty["cards"] = []
set_snapshot(empty)
out["emptyLanes"] = js("page.lanes.length")
none = json.loads(json.dumps(snap)); none["exists"] = False; none["cards"] = []
set_snapshot(none)
out["exists"] = js("page.exists")
js("page.available = false")
wait(100)
js("page.available = true")
set_snapshot(snap)
out["existsAgain"] = js("page.exists")
out["lanesAgain"] = js("page.lanes.map(l => l.cards.length)")
engine.rootContext().setContextProperty("lanesFlick", obj("boardLanes"))
out["rowsAgain"] = js("lanesFlick.contentItem.children[0].children.filter(c => String(c.objectName).indexOf('boardLane-') === 0).length")
out["laneCount"] = len(out["lanesAgain"])
# burza migawek: stan zgodny z ostatnią
for n in range(5):
    snap["cards"][1]["title"] = f"burza {n}"
    set_snapshot(snap)
out["storm"] = js("page.cards.find(c => c.id === '%s').title" % snap["cards"][1]["id"])
""")
        self.assertEqual(out["emptyLanes"], 0)
        self.assertFalse(out["exists"])
        self.assertTrue(out["existsAgain"])
        self.assertEqual(out["rowsAgain"], out["laneCount"])
        self.assertGreater(out["laneCount"], 0)
        self.assertEqual(out["storm"], "burza 4")


@unittest.skipUnless(HAS_QT, "brak Qt Quick")
class DerivedBindingsIdentityTest(ProbeCase):
    def test_panel_card_notes_and_errors_keep_identity(self):
        out = self.probe(SPIES + """
wait(300)
snap = json.loads(backend.snapshotJson)
snap["youtrack"] = {"enabled": True, "available": False, "conflicts": 1, "newIssues": 0, "pending": 2}
snap["errors"] = [{"path": "/x/a.md", "message": "zły plik"}]
set_snapshot(snap)
click(card("K-12"))
out["panel"] = js("page.panelOpen && page.panelCardId")
for name in ("panelCard", "youtrackNotes", "errorLines", "cards", "allCards", "columnSpecs"):
    spy(name)
out["notes"] = js("page.youtrackNotes.map(n => n.text)")
set_snapshot(snap)
set_snapshot(snap)
out["identical"] = {n: hits(n) for n in spies}
other = next(c for c in snap["cards"] if c["id"] == "K-14")
other["title"] += " (zmiana)"
set_snapshot(snap)
out["otherCard"] = {n: hits(n) for n in spies}
snap["youtrack"]["pending"] = 3
set_snapshot(snap)
out["notesChanged"] = hits("youtrackNotes")
mine = next(c for c in snap["cards"] if c["id"] == "K-12")
mine["title"] = "panelowa zmiana"
set_snapshot(snap)
out["panelChanged"] = hits("panelCard")
out["panelTitle"] = js("page.panelCard.title")
out["cardsIsAllCards"] = js("page.cards === page.allCards")
js("page.tagFilter = 'pilne'")
wait(100)
out["filteredDiffers"] = js("page.cards.length < page.allCards.length")
""")
        self.assertEqual(out["panel"], "K-12")
        self.assertEqual(out["notes"], ["1 konflikt z YouTrackiem", "YouTrack niedostępny · 2 czeka"])
        ident = out["identical"]
        for name in ("panelCard", "youtrackNotes", "errorLines", "columnSpecs"):
            self.assertEqual(ident[name], 0, f"{name}: ta sama migawka dwa razy")
        self.assertEqual(out["otherCard"]["panelCard"], 0, "zmiana innej karty nie rusza karty panelu")
        self.assertEqual(out["otherCard"]["youtrackNotes"], 0)
        self.assertEqual(out["otherCard"]["errorLines"], 0)
        self.assertGreaterEqual(out["notesChanged"], 1, "zmieniona treść notek wchodzi")
        self.assertGreaterEqual(out["panelChanged"], 1, "zmieniona karta panelu wchodzi")
        self.assertEqual(out["panelTitle"], "panelowa zmiana")
        self.assertTrue(out["cardsIsAllCards"], "bez filtrów jedno przeliczenie Logic.visible")
        self.assertTrue(out["filteredDiffers"])

    def test_full_card_and_optimistic_move_follow_the_snapshot(self):
        out = self.probe(SPIES + """
wait(300)
snap = json.loads(backend.snapshotJson)
mine0 = next(c for c in snap["cards"] if c["id"] == "K-12")
mine0["derived"] = dict(mine0.get("derived") or {}, phases=[{"id": "p", "label": "Plan", "state": "done"}])
set_snapshot(snap)
js("page.openFull('K-12')")
wait(200)
out["fullId"] = js("page.fullCardId")
spy("fullCard")
set_snapshot(snap)
out["identical"] = hits("fullCard")
mine = next(c for c in snap["cards"] if c["id"] == "K-12")
mine["title"] = "pełny widok"
set_snapshot(snap)
out["fullTitle"] = js("page.fullCard.title")
out["changed"] = hits("fullCard")
# optimistic: przeniesienie w locie, potem migawka je zdejmuje
js("page.optimistic = [{id: 'K-15', column: 'todo', before: '', tag: 't'}]")
wait(100)
out["optimisticColumn"] = js("page.cards.find(c => c.id === 'K-15').boardColumn")
set_snapshot(snap)
out["optimisticCleared"] = js("page.optimistic.length")
out["afterColumn"] = js("page.cards.find(c => c.id === 'K-15').boardColumn")
out["snapColumn"] = next(c for c in snap["cards"] if c["id"] == "K-15").get("boardColumn", "")
""")
        self.assertEqual(out["fullId"], "K-12")
        self.assertEqual(out["identical"], 0)
        self.assertEqual(out["fullTitle"], "pełny widok")
        self.assertGreaterEqual(out["changed"], 1)
        self.assertEqual(out["optimisticColumn"], "todo")
        self.assertEqual(out["optimisticCleared"], 0)
        self.assertEqual(out["afterColumn"], out["snapColumn"] or out["afterColumn"])


class BackendParseOnceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        spec = importlib.util.spec_from_file_location("tide_board_backend_parse", os.path.join(APP_DIR, "backend.py"))
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def snapshot(self, waiting=1) -> str:
        cards = [{"id": f"K-{i}", "column": "waiting" if i <= waiting else "todo"} for i in range(1, 4)]
        return json.dumps({"exists": True, "cards": cards})

    def test_badge_does_not_parse_the_same_snapshot_twice(self):
        backend = self.module.create(None, None)
        text = self.snapshot()
        backend._got_snapshot(True, text)
        self.assertEqual(backend.parse_count, 1, "nowa migawka: jedno parsowanie")
        self.assertEqual((backend.badgeText, backend.badgeTone), ("● 1", "waiting"))
        for _ in range(3):
            backend._got_snapshot(True, text)         # identyczna: odfiltrowana, bez parsowania
        backend._set_available(False)
        backend._set_available(True)                  # przełączenie dostępności używa tej samej sparsowanej migawki
        self.assertEqual(backend.parse_count, 1)
        self.assertEqual(backend.badgeText, "● 1")

    def test_badge_value_follows_availability_and_snapshot(self):
        backend = self.module.create(None, None)
        backend._got_snapshot(True, self.snapshot(waiting=2))
        self.assertEqual(backend.badgeText, "● 2")
        backend._got_snapshot(False, None)
        self.assertEqual((backend.badgeText, backend.badgeTone), ("", "muted"))
        backend._got_snapshot(True, self.snapshot(waiting=0))
        self.assertEqual((backend.badgeText, backend.badgeTone), ("3", "muted"))
        self.assertEqual(backend.parse_count, 2, "po jednym parsowaniu na zmienioną migawkę")
        backend._got_snapshot(True, "to nie jest JSON")
        self.assertEqual((backend.badgeText, backend.badgeTone), ("", "muted"))

    def test_preview_backend_parses_once(self):
        backend = self.module.create(None, {"exists": True, "cards": [{"id": "K-1", "column": "backlog"}]})
        self.assertEqual(backend.parse_count, 1)
        self.assertEqual(backend.badgeText, "1")


if __name__ == "__main__":
    unittest.main()
