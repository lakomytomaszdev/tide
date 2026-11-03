"""Pełna karta (PT-128): wspólny komponent apps/common/qml/card/CardFullView.qml w widoku Tablica.

- fixture’y scenariuszy C i D makiety (decisionfix.py) liczone przez usługę: fazy, fale, stany ticketów,
- sonda offscreen: pełna karta otwiera się z „Otwórz całą kartę”, z karty spoza „Czeka na mnie” i z ustawienia
  „Pełna karta”; Esc i „← Tablica” wracają; pasek 7 faz; „Twój ruch” z decyzją rozstrzyganą w miejscu (scenariusz C)
  albo jednym zdaniem (scenariusz D); fale ticketów z ludzkimi stanami; „Teraz w tle” tylko z trwającymi krokami
  i „Wejdź”; błąd usługi przywraca decyzję; karta bez dziennika nadal otwiera panel karty.
"""
import json
import os
import shutil
import unittest

from .decisionfix import FINDINGS_C, derived, fold, full_c, full_d, journal_c, journal_d
from .test_decision_dialog import STATE_PY as DIALOG_STATE_PY
from .test_view import ProbeCase

# Stan pełnej karty z drzewa QML (tylko widoczne elementy, w kolejności na ekranie).
STATE_PY = DIALOG_STATE_PY + r'''
def full():
    return json.loads(js("(function () { const v = page.cardFullView; const all = " + ALL + "; "
        "const one = n => all(v, n)[0] || null; "
        "const x = it => it.mapToItem(v, 0, 0).x; const y = it => it.mapToItem(v, 0, 0).y; "
        "const byX = list => list.slice().sort((a, b) => x(a) - x(b)); "
        "const byY = list => list.slice().sort((a, b) => y(a) - y(b)); "
        "const opts = byY(all(v, 'decisionOption')); "
        "return JSON.stringify({ open: page.fullOpen && v.visible, card: page.fullCardId, panel: page.panelOpen, "
        "  dialog: page.decisionOpen, "
        "  title: one('fullTitle') ? one('fullTitle').text : null, "
        "  phases: byX(all(v, 'fullPhase')).map(p => [p.phaseId, p.phaseState, p.caption]), "
        "  headline: one('fullHeadline') ? one('fullHeadline').text : null, "
        "  kind: one('fullMoveKind') ? one('fullMoveKind').children[0].text : null, "
        "  who: one('fullMoveWho') ? one('fullMoveWho').text : null, "
        "  happened: one('decisionHappened') ? one('decisionHappened').text : null, "
        "  options: opts.map(o => [o.title, o.recommended, o.checked]), "
        "  findings: byY(all(v, 'decisionFinding')).map(f => f.checked), "
        "  error: one('decisionError') ? one('decisionError').text : '', "
        "  choose: one('fullChoose') ? one('fullChoose').enabled : null, "
        "  waves: byX(all(v, 'fullWave')).map(w => byY(all(w, 'fullTicket')).map(t => [t.ticketId, t.statusLabel])), "
        "  planEmpty: !!one('fullPlanEmpty'), "
        "  now: byY(all(v, 'fullNow')).map(s => [s.stepId, s.label, all(s, 'fullNowEnter').length]), "
        "  nowEmpty: one('fullNowEmpty') ? one('fullNowEmpty').text : null }) })()"))

def full_option(i):
    return at("(function () { const v = page.cardFullView; const all = " + ALL + "; "
              "return all(v, 'decisionOption').sort((a, b) => a.mapToItem(v, 0, 0).y - b.mapToItem(v, 0, 0).y)["
              + str(i) + "] })()")
'''

PHASE_IDS = ["order", "planning", "plan-review", "plan-approval", "tickets", "closing", "main-merge"]


class FixtureTest(unittest.TestCase):
    """Fixture’y scenariuszy makiety to dane usługi, nie ręcznie wpisane pola."""

    def test_scenario_c_waits_for_plan_approval(self):
        out = derived(fold(journal_c()), {"column": "todo"})
        self.assertEqual(out["column"], "waiting")
        self.assertEqual([(p["id"], p["state"]) for p in out["phases"]],
                         list(zip(PHASE_IDS, ["done", "done", "done", "you", "todo", "todo", "todo"])))
        self.assertEqual({t["statusLabel"] for t in out["tickets"]}, {"w planie"})
        self.assertEqual(out["flow"]["running"], [])
        self.assertEqual([d["kind"] for d in out["flow"]["decisions"]], ["plan-approval"])

    def test_scenario_d_second_wave_runs(self):
        out = derived(fold(journal_d()), {"column": "todo"})
        self.assertEqual(out["column"], "progress")
        self.assertEqual([(p["id"], p["state"]) for p in out["phases"]],
                         list(zip(PHASE_IDS, ["done", "done", "done", "done", "now", "todo", "todo"])))
        self.assertEqual(out["flow"]["waveLabel"], "fala 2 z 4")
        self.assertEqual(out["flow"]["decisions"], [])
        self.assertEqual([s["id"] for s in out["flow"]["running"]], ["K-8-ticket-2-0", "K-8-review-3-1", "K-8-fix-4-1"])


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class FullViewTest(ProbeCase):
    def test_scenario_d_card_outside_waiting_opens_full_card_with_calm_move(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["opened"] = full()
click(at("(" + ALL + ")(page.cardFullView, 'fullNowEnter')[0]"))
out["enter"] = calls()
key(Qt.Key.Key_Escape)
out["afterEsc"] = [js("page.fullOpen"), js("page.selectedId")]
click(card("K-8"))
click(named("fullBack"))
out["afterBack"] = js("page.fullOpen")
""", full_d(self.data))
        state = out["opened"]
        self.assertTrue(state["open"], "karta z „W toku” otwiera pełną kartę")
        self.assertEqual((state["card"], state["panel"], state["dialog"]), ("K-8", False, False))
        self.assertEqual([tuple(p[:2]) for p in state["phases"]],
                         list(zip(PHASE_IDS, ["done", "done", "done", "done", "now", "todo", "todo"])))
        self.assertEqual(state["phases"][4][2].split(" · ")[-1], "fala 2 z 4")
        self.assertTrue(state["phases"][3][2].startswith("ty · "), "zatwierdzenie planu — faza użytkownika")
        self.assertTrue(state["phases"][1][2].startswith("Opus · "), "planowanie z wykonawcą i czasem")
        self.assertTrue(state["headline"].startswith("3 kroki pracują w tle (fala 2 z 4)"), state["headline"])
        self.assertIsNone(state["kind"], "bez decyzji nie ma plakietki ani możliwości")
        self.assertEqual(state["options"], [])
        self.assertEqual(state["waves"], [
            [["#1", "scalony"]],
            [["#2", "pracuje"], ["#3", "review"], ["#4", "poprawki"]],
            [["PT-42", "czeka na #3"], ["#5", "czeka na #4"], ["#7", "czeka na #4"], ["#8", "czeka na #4"]],
            [["#6", "czeka na #5"]]])
        self.assertEqual([s[:2] for s in state["now"]],
                         [["K-8-ticket-2-0", "#2 · ticket · Opus"], ["K-8-review-3-1", "#3 · review · Opus"],
                          ["K-8-fix-4-1", "#4 · poprawki · Opus"]], "tylko trwające kroki")
        self.assertEqual([s[2] for s in state["now"]], [1, 1, 1], "„Wejdź” przy trwających krokach")
        self.assertIsNone(state["nowEmpty"])
        self.assertEqual(out["enter"], [["EnterStep", "K-8", "K-8-ticket-2-0"]])
        self.assertEqual(out["afterEsc"], [False, "K-8"], "Esc wraca do Tablicy")
        self.assertFalse(out["afterBack"], "„← Tablica” wraca do Tablicy")

    def test_scenario_c_open_whole_card_resolves_the_decision_in_place(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["dialog"] = [js("page.decisionOpen"), js("page.fullOpen")]
click(named("decisionFullCard"))
out["opened"] = full()
key(Qt.Key.Key_Return)
out["enterWithout"] = len(calls())
click(full_option(0))
out["picked"] = full()
click(named("fullChoose"))
out["sent"] = calls()
wait(80)
out["after"] = full()
""", full_c(self.data))
        self.assertEqual(out["dialog"], [True, False], "domyślnie klik w „Czeka na mnie” otwiera okno decyzji")
        state = out["opened"]
        self.assertTrue(state["open"], "„Otwórz całą kartę” otwiera pełną kartę")
        self.assertFalse(state["dialog"])
        self.assertEqual([tuple(p[:2]) for p in state["phases"]],
                         list(zip(PHASE_IDS, ["done", "done", "done", "you", "todo", "todo", "todo"])))
        self.assertIn("prosi o zmiany", state["phases"][2][2])
        self.assertEqual(state["kind"], "Plan do zatwierdzenia")
        self.assertEqual(state["who"], "K-8 · Opus")
        self.assertIn("Plan ma 9 ticketów", state["happened"])
        self.assertEqual([(o[0], o[1], o[2]) for o in state["options"]],
                         [("Odeślij uwagi do poprawy", True, False),
                          ("Zatwierdź plan i rozpocznij implementację", False, False)],
                         "kolejność i polecona z usługi, nic nie zaznaczone")
        self.assertEqual(state["findings"], [True, True, False])
        self.assertFalse(state["choose"])
        self.assertIsNone(state["headline"])
        self.assertEqual({label for wave in state["waves"] for _id, label in wave}, {"w planie"})
        self.assertEqual(sum(len(w) for w in state["waves"]), 9)
        self.assertEqual(state["now"], [])
        self.assertIn("czeka na Twoją decyzję", state["nowEmpty"])
        self.assertEqual(out["enterWithout"], 0, "Enter bez zaznaczenia nic nie wysyła")
        self.assertEqual([o[2] for o in out["picked"]["options"]], [True, False])
        self.assertTrue(out["picked"]["choose"])
        self.assertEqual(out["sent"][0][:2], ["EditCardPlan", "K-8"])
        params = json.loads(out["sent"][0][2])
        self.assertEqual(params["action"], "return")
        self.assertEqual([f["text"] for f in params["findings"]], [f["text"] for f in FINDINGS_C[:2]])
        self.assertTrue(out["after"]["open"], "pełna karta zostaje otwarta po odpowiedzi")
        self.assertIsNone(out["after"]["kind"], "odpowiedziana decyzja znika do nowej migawki")

    def test_full_card_setting_opens_full_card_for_waiting_cards_and_notifications(self):
        data = full_c(self.data)
        data["cardOpen"] = "full"
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["click"] = [js("page.fullOpen"), js("page.decisionOpen"), js("page.panelOpen")]
key(Qt.Key.Key_Escape)
js("page.parent.showItem('k-8')")
wait(150)
out["notification"] = [js("page.fullOpen"), js("page.decisionOpen"), js("page.fullCardId")]
""", data)
        self.assertEqual(out["click"], [True, False, False], "ustawienie „Pełna karta” otwiera pełną kartę")
        self.assertEqual(out["notification"], [True, False, "K-8"], "powiadomienie idzie tą samą trasą")

    def test_service_error_brings_the_decision_back_with_reason(self):
        data = full_c(self.data)
        data["cardOpen"] = "full"
        out = self.probe(STATE_PY + """
replies.append({"ok": False, "error": "busy", "message": "plan jeszcze się zapisuje"})
click(card("K-8"))
click(full_option(1))
key(Qt.Key.Key_Return)
wait(150)
out["state"] = full()
out["calls"] = [c[0] for c in calls()]
""", data)
        self.assertEqual(out["calls"], ["ApproveCardPlan"])
        self.assertEqual(out["state"]["kind"], "Plan do zatwierdzenia", "decyzja wraca")
        self.assertIn("plan jeszcze się zapisuje", out["state"]["error"])

    def test_panel_button_and_cards_without_journal(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
click(named("fullDescription"))
out["panel"] = [js("page.fullOpen"), js("page.panelOpen"), js("page.panelCardId")]
js("page.closePanel()")
js("page.fullOpen = false")
click(card("K-9"))
out["noJournal"] = [js("page.fullOpen"), js("page.panelOpen"), js("page.panelCardId")]
""", full_d(self.data))
        self.assertEqual(out["panel"], [True, True, "K-8"], "„Edytuj opis” otwiera szufladę opisu nad pełną kartą (PT-134)")
        self.assertEqual(out["noJournal"], [False, True, "K-9"], "karta bez dziennika — panel karty")


if __name__ == "__main__":
    unittest.main()
