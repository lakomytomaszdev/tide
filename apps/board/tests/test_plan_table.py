"""Plan karty jako tabela i historia w stałym bloku (K-128, PT-503, makieta S-9 „Plan karty” C i „Historia karty” A).

- czysta funkcja wyboru bieżącej fali (`ticket.js`, `currentWave`) — test tabelaryczny w QJSEngine, bez okna,
- sonda offscreen na danych syntetycznych (plan z 13 falami, historia z 5 i 200 zdarzeniami) w oknach 1180 px i
  najwęższym: pełne tytuły po jednym w wierszu, numer ticketu bez nakładania, bieżąca fala przypięta z plakietką
  „teraz”, brak przypiętego bloku po zakończeniu planu, stała wysokość strony i bloku historii, „Pokaż starsze”
  w bloku.
"""
import json
import os
import re
import shutil
import time
import unittest

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtQml import QJSEngine

_APP = None

from .decisionfix import full_d
from .test_card_full_view import STATE_PY as FULL_STATE_PY
from .test_view import ProbeCase

TICKET_JS = os.path.join(os.path.dirname(__file__), "..", "..", "common", "qml", "card", "ticket.js")

STATE_PY = FULL_STATE_PY + r'''
V = "page.cardFullView"

def items(name, root=V):
    return "(" + ALL + ")(" + root + ", '" + name + "')"

def rect(it_expr):
    """[x, y, szerokość, wysokość] elementu we współrzędnych okna."""
    return json.loads(js("(function () { const it = " + it_expr + "; if (!it) return 'null'; "
                         "const p = it.mapToItem(null, 0, 0); "
                         "return JSON.stringify([p.x, p.y, it.width, it.height]) })()"))

PART_NAMES = ["fullPlanWave", "fullPlanNow", "fullPlanTicket", "fullPlanTitleColumn", "fullTicketStatus",
              "fullPlanPeople", "fullTicketSettings"]

def parts():
    """Dla każdego wiersza prostokąty jego elementów (okno): [nazwa, x, y, szerokość, wysokość] albo null."""
    return json.loads(js("(function () { const all = " + ALL + "; const names = " + json.dumps(PART_NAMES) + "; "
        "return JSON.stringify(all(" + V + ", 'fullTicket').map(t => ({ id: t.ticketId, parts: names.map(n => { "
        "const it = all(t, n)[0]; if (!it) return null; const p = it.mapToItem(null, 0, 0); "
        "return [n, p.x, p.y, it.width, it.height] }) }))) })()"))

def count(name):
    return js(items(name) + ".length")

def rows():
    return json.loads(js("JSON.stringify(" + items("fullTicket") + ".map(t => ({ id: t.ticketId, wave: t.wave, "
        "now: " + items("fullPlanNow", "t") + ".length > 0, current: t.current })))"))
'''


# Każdy scenariusz zaczyna od otwartej pełnej karty K-8.
OPENED = STATE_PY + 'click(card("K-8"))\nwait(250)\n'


def js_functions() -> QJSEngine:
    """Funkcje ticket.js w silniku JS (plik biblioteki QML bez linii `.pragma`)."""
    with open(TICKET_JS, encoding="utf-8") as fh:
        source = re.sub(r"^\.pragma library\s*$", "", fh.read(), flags=re.M)
    global _APP
    _APP = QCoreApplication.instance() or QCoreApplication([])
    engine = QJSEngine()
    result = engine.evaluate(source)
    assert not result.isError(), result.toString()
    return engine


def ticket(ident, state, status=None):
    return {"id": ident, "state": state, "status": status or {"gotowe": "merged", "odrzucone": "rejected"}.get(state, "running")}


class CurrentWaveTest(unittest.TestCase):
    """Wybór bieżącej fali to czysta funkcja: najniższa fala z ticketem innym niż gotowe/odrzucone."""

    @classmethod
    def setUpClass(cls):
        cls.engine = js_functions()

    def current(self, waves, tickets):
        call = self.engine.evaluate("(function (w, t) { return currentWave(JSON.parse(w), JSON.parse(t)) })")
        return int(call.call([json.dumps(waves), json.dumps(tickets)]).toInt())

    def test_table(self):
        waves = [["A", "B"], ["C"], ["D", "E"]]
        cases = [
            ("ticket w toku w pierwszej fali",
             [ticket("A", "w-toku"), ticket("B", "todo"), ticket("C", "todo"), ticket("D", "todo"), ticket("E", "todo")], 0),
            ("pierwsza fala gotowa, druga czeka na decyzję",
             [ticket("A", "gotowe"), ticket("B", "gotowe"), ticket("C", "czeka"), ticket("D", "todo"), ticket("E", "todo")], 1),
            ("w fali jeden gotowy i jeden w review — fala bieżąca",
             [ticket("A", "gotowe"), ticket("B", "review"), ticket("C", "todo"), ticket("D", "todo"), ticket("E", "todo")], 0),
            ("wszystkie gotowe — nic przypiętego",
             [ticket(i, "gotowe") for i in "ABCDE"], -1),
            ("odrzucone liczą się jak zakończone",
             [ticket("A", "odrzucone"), ticket("B", "gotowe"), ticket("C", "odrzucone"), ticket("D", "w-toku"),
              ticket("E", "gotowe")], 2),
            ("same odrzucone i gotowe — nic przypiętego",
             [ticket("A", "odrzucone"), ticket("B", "gotowe"), ticket("C", "odrzucone"), ticket("D", "gotowe"),
              ticket("E", "odrzucone")], -1),
            ("stan z samego `status` (bez `state`)",
             [{"id": "A", "status": "merged"}, {"id": "B", "status": "rejected"}, {"id": "C", "status": "queued"},
              {"id": "D", "status": "queued"}, {"id": "E", "status": "queued"}], 1),
            ("ticket spoza karty nie czyni fali bieżącą",
             [ticket("A", "gotowe"), ticket("B", "gotowe"), ticket("D", "todo"), ticket("E", "todo")], 2),
        ]
        for name, tickets, expected in cases:
            with self.subTest(name):
                self.assertEqual(self.current(waves, tickets), expected)

    def test_without_plan(self):
        self.assertEqual(self.current([], []), -1)
        self.assertEqual(self.current([[]], []), -1)


def wave_data(data: dict, done_through: int, waves: int = 13, per_wave: int = 2, history: int = 5, old: int = 0,
              long_titles: bool = True) -> dict:
    """K-8 z planem z `waves` falami po `per_wave` ticketów; fale do `done_through` włącznie gotowe, następna w toku,
    kolejne czekają. Historia: `history` bieżących zdarzeń i `old` wcześniejszych."""
    data = json.loads(json.dumps(data))
    card = next(c for c in data["cards"] if c["id"] == "K-8")
    derived = card["derived"]
    flow = derived["flow"]
    tickets, plan_rows, plan_waves = [], [], []
    for w in range(1, waves + 1):
        ids = []
        for n in range(per_wave):
            ident = f"PT-{w * 10 + n}"
            ids.append(ident)
            state = "gotowe" if w <= done_through else "w-toku" if w == done_through + 1 and n == 0 else "todo"
            status = "merged" if state == "gotowe" else "running" if state == "w-toku" else "queued"
            title = f"Fala {w}, ticket {n + 1}: " + ("bardzo długi tytuł, który musi się zawinąć w kilku liniach " * 2
                                                     if long_titles and n == 0 else "krótki tytuł")
            tickets.append({"id": ident, "ticket": ident, "title": title.strip(), "state": state, "status": status,
                            "statusLabel": {"merged": "scalony", "running": "pracuje", "queued": "w kolejce"}[status],
                            "wave": w, "executor": "Sonnet", "reviewer": "Opus", "waitingOn": None})
            plan_rows.append({"id": ident, "title": title.strip(), "note": "uwaga do " + ident if n == 0 else ""})
        plan_waves.append(ids)
    derived["tickets"] = tickets
    flow["plan"] = {**(flow.get("plan") or {}), "rows": plan_rows, "waves": plan_waves}
    flow["waveLabel"] = f"fala {min(done_through + 1, waves)} z {waves}"
    flow["decisions"] = []

    def entry(i, is_old):
        return {"id": f"h{i}", "ts": int(time.time()) - i * 90, "kind": "step", "kindLabel": "krok", "actor": "tide",
                "text": f"Zdarzenie numer {i}: krok zakończył się wynikiem „gotowe”, zapisano zmiany w gałęzi ticketu.",
                "old": is_old, "step": f"S-{i}", "ticket": "", "attempts": [], "canResume": False}
    flow["history"] = [entry(i, i >= history) for i in range(history + old)][::-1]
    return data


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class PlanTableTest(ProbeCase):
    def data13(self, done_through=3, **kw):
        return wave_data(full_d(self.data), done_through, **kw)

    def test_thirteen_waves_show_whole_titles_one_ticket_per_row(self):
        out = self.probe(OPENED + """
window.setWidth(1228)   # tabela na zakładce Plan ma wtedy 1180 px
wait(100)
js(V + ".ticketSettingsEnabled = true")
js(V + ".selectTab('plan')")
wait(250)
out["table"] = rect(items("fullPlanTable") + "[0]")
out["rows"] = json.loads(js("JSON.stringify(" + items("fullTicket") + ".map(t => { const ti = " + items("fullPlanTitle", "t") + "[0]; "
    "const p = t.mapToItem(null, 0, 0); return { id: t.ticketId, y: p.y, h: t.height, "
    "title: ti.text, truncated: ti.truncated, elide: ti.elide, fits: ti.height >= ti.contentHeight - 1, "
    "lines: ti.lineCount } }))"))
""", self.data13())
        self.assertGreaterEqual(out["table"][2], 1180 - 1)
        rows = out["rows"]
        self.assertEqual(len(rows), 26, "każdy z 13 × 2 ticketów w jednym wierszu")
        self.assertEqual(len({r["id"] for r in rows}), 26)
        self.assertEqual(len({round(r["y"]) for r in rows}), 26, "wiersze nie leżą jeden na drugim")
        for row in rows:
            self.assertFalse(row["truncated"], row)
            self.assertEqual(row["elide"], 3, "tytuł bez wielokropka")
            self.assertTrue(row["fits"], row)
            self.assertTrue(row["title"].startswith("Fala "))
        self.assertTrue(any(r["lines"] > 1 for r in rows), "długie tytuły zawijają się, nie są ucinane")

    def test_ticket_number_overlaps_nothing_in_wide_and_narrow_windows(self):
        for width, label in ((1228, "1180 px"), (700, "najwęższe")):
            with self.subTest(label):
                out = self.probe(OPENED + f"""
window.setWidth({width})
wait(100)
js(V + ".ticketSettingsEnabled = true")
js(V + ".selectTab('plan')")
wait(250)
out["table"] = rect(items("fullPlanTable") + "[0]")
out["parts"] = parts()
""", self.data13(long_titles=True))
                parts_by_row = out["parts"]
                self.assertEqual(len(parts_by_row), 26)
                for row in parts_by_row:
                    boxes = {p[0]: p[1:] for p in row["parts"] if p}
                    self.assertIn("fullPlanTicket", boxes)
                    ticket_box = boxes["fullPlanTicket"]
                    for name, other in boxes.items():
                        if name == "fullPlanTicket":
                            continue
                        apart = (ticket_box[0] + ticket_box[2] <= other[0] + 0.5 or other[0] + other[2] <= ticket_box[0] + 0.5
                                 or ticket_box[1] + ticket_box[3] <= other[1] + 0.5 or other[1] + other[3] <= ticket_box[1] + 0.5)
                        self.assertTrue(apart, f"{row['id']}: numer nachodzi na {name} ({ticket_box} vs {other})")
                if width < 720 + 48:
                    self.assertTrue(out["table"][2] < 720, "okno węższe niż tabela")

    def test_current_wave_is_pinned_with_a_now_badge_and_visible_without_scrolling(self):
        out = self.probe(OPENED + """
js(V + ".ticketSettingsEnabled = true")
js(V + ".selectTab('plan')")
wait(250)
out["rows"] = rows()
out["table"] = rect("(" + ALL + ")(" + V + ", 'fullPlanTable')[0]")
out["pinned"] = rect("(" + ALL + ")(" + V + ", 'fullPlanPinned')[0]")
out["scroll"] = [js("(" + ALL + ")(" + V + ", 'fullPlanBody')[0].contentY"), js("(" + ALL + ")(" + V + ", 'fullPlanPinned')[0].contentY")]
out["pinnedRows"] = json.loads(js("JSON.stringify(" + items("fullTicket", "(" + ALL + ")(" + V + ", 'fullPlanPinned')[0]") + ".map(t => { "
    "const p = t.mapToItem(null, 0, 0); return [t.ticketId, p.y, p.y + t.height] }))"))
""", self.data13(done_through=11))
        by_id = {r["id"]: r for r in out["rows"]}
        wave12 = {"PT-120", "PT-121"}
        self.assertEqual({i for i, r in by_id.items() if r["now"]}, wave12, "plakietka „teraz” tylko przy fali 12")
        self.assertEqual({i for i, r in by_id.items() if r["current"]}, wave12)
        self.assertEqual({row[0] for row in out["pinnedRows"]}, wave12, "przypięty blok ma wiersze fali 12")
        self.assertEqual(len(out["rows"]), 26, "wiersze przypięte nie powtarzają się niżej")
        self.assertEqual(out["scroll"], [0, 0], "wiersze widoczne bez przewijania tabeli")
        table_bottom = out["table"][1] + out["table"][3]
        for _id, top, bottom in out["pinnedRows"]:
            self.assertGreaterEqual(top, out["table"][1])
            self.assertLessEqual(bottom, table_bottom)
        self.assertLessEqual(out["pinned"][3], out["table"][3] * 0.4 + 1, "najwyżej 40% wysokości tabeli")

    def test_pinned_block_scrolls_on_its_own_above_forty_percent(self):
        out = self.probe(OPENED + """
js(V + ".selectTab('overview')")
wait(250)
out["table"] = rect("(" + ALL + ")(" + V + ", 'fullPlanTable')[0]")
pinned = "(" + ALL + ")(" + V + ", 'fullPlanPinned')[0]"
out["pinned"] = rect(pinned)
out["content"] = js(pinned + ".contentHeight")
""", self.data13(done_through=0, per_wave=8))
        self.assertLessEqual(out["table"][3], 420 + 1, "w Przeglądzie tabela ma najwyżej 420 px")
        self.assertLessEqual(out["pinned"][3], out["table"][3] * 0.4 + 1)
        self.assertGreater(out["content"], out["pinned"][3], "przypięty blok przewija się sam")

    def test_finished_plan_has_no_pinned_block_and_starts_from_the_first_wave(self):
        out = self.probe(OPENED + """
js(V + ".selectTab('plan')")
wait(250)
out["rows"] = rows()
out["pinned"] = count("fullPlanPinned")
out["now"] = count("fullPlanNow")
first = json.loads(js("JSON.stringify(" + items("fullTicket") + ".map(t => [t.ticketId, t.mapToItem(null, 0, 0).y]))"))
out["order"] = [i for i, _y in sorted(first, key=lambda r: r[1])][:2]
""", self.data13(done_through=13))
        self.assertEqual(out["pinned"], 0, "po skończeniu wszystkich fal nic nie jest przypięte")
        self.assertEqual(out["now"], 0)
        self.assertTrue(all(not r["current"] for r in out["rows"]))
        self.assertEqual(out["order"], ["PT-10", "PT-11"], "tabela zaczyna od pierwszej fali")

    def test_ticket_settings_only_on_the_board(self):
        out = self.probe(OPENED + """
js(V + ".selectTab('plan')")
js(V + ".ticketSettingsEnabled = false")
wait(150)
out["sessions"] = count("fullTicketSettings")
js(V + ".ticketSettingsEnabled = true")
wait(150)
out["board"] = count("fullTicketSettings")
""", self.data13(done_through=1, waves=3))
        self.assertEqual(out["sessions"], 0, "w Sesjach ustawień ticketu nie ma")
        self.assertEqual(out["board"], 6, "na Tablicy ⋯ przy każdym wierszu")


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class HistoryBlockTest(ProbeCase):
    def block(self, history, old=0):
        return wave_data(full_d(self.data), 3, waves=4, history=history, old=old)

    def measure(self, history, old=0, tab="overview", expand=False):
        return self.probe(OPENED + f"""
js(V + ".selectTab('{tab}')")
wait(250)
flick = "(" + ALL + ")(" + V + ", 'fullScroll')[0]"
frame = "(" + ALL + ")(" + V + ", 'fullHistoryFrame')[0]"
lst = "(" + ALL + ")(" + V + ", 'fullHistoryList')[0]"
bar = ("(function (l) {{ return l.children.find(c => typeof c.scrollable === 'boolean') }})(" + lst + ")")
def snap():
    return {{"page": js(flick + ".contentHeight"), "frame": js(frame + ".height"), "list": js(lst + ".contentHeight"),
            "lines": count("fullHistoryLine"), "bar": js("(function () {{ const b = " + bar + "; return !!b && b.visible && b.scrollable }})()"),
            "open": js(V + ".oldOpen"), "count": js("(" + ALL + ")(" + V + ", 'fullHistoryCount')[0].text"), "viewport": js(flick + ".height")}}
out["before"] = snap()
{"js(lst + '.positionViewAtEnd()')" if expand else ""}
wait(250)
{"js(lst + '.positionViewAtEnd()')" if expand else ""}
wait(150)
{"js('(function () { const f = ' + flick + '; f.contentY = ' + frame + '.mapToItem(f.contentItem, 0, 0).y - 20 })()')" if expand else ""}
wait(150)
{"click(named('fullHistoryOld'))" if expand else ""}
wait(200)
out["after"] = snap()
""", self.block(history, old))

    def test_page_height_does_not_depend_on_the_number_of_events(self):
        few = self.measure(5)["before"]
        many = self.measure(200)["before"]
        self.assertEqual(few["page"], many["page"], "wysokość strony ta sama przy 5 i 200 zdarzeniach")
        self.assertEqual(few["frame"], 340)
        self.assertEqual(many["frame"], 340)
        self.assertEqual(few["lines"], 5, few)
        self.assertLess(many["lines"], 60, "lista wirtualizuje wiersze, nie tworzy 200 naraz")
        self.assertFalse(few["bar"], "5 zdarzeń mieści się bez paska")
        self.assertTrue(many["bar"], "przy 200 zdarzeniach widoczny pasek przewijania")
        self.assertGreater(many["list"], few["list"])
        self.assertIn("5 zdarzeń", few["count"])
        self.assertIn("200 zdarzeń", many["count"])

    def test_show_older_expands_inside_the_block_without_changing_the_page(self):
        out = self.measure(40, old=30, expand=True)
        before, after = out["before"], out["after"]
        self.assertFalse(before["open"])
        self.assertTrue(after["open"], out)
        self.assertEqual(after["page"], before["page"], "strona nie zmienia wysokości")
        self.assertEqual(after["frame"], 340)
        self.assertGreater(after["list"], before["list"], "starsze zdarzenia doszły wewnątrz bloku")
        self.assertIn("70 zdarzeń", after["count"])

    def test_history_tab_fills_the_whole_height(self):
        out = self.measure(200, tab="history")
        snap = out["before"]
        self.assertAlmostEqual(snap["frame"], snap["viewport"] - 40, delta=2)
        self.assertLessEqual(snap["page"], snap["viewport"] + 1, "strona w zakładce Historia nie przewija się")
        self.assertTrue(snap["bar"])


if __name__ == "__main__":
    unittest.main()
