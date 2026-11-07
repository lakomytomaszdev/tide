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
from datetime import datetime, timedelta

from .decisionfix import (FINDINGS_C, derived, fold, full_c, full_card, full_d, full_e, journal_c, journal_d,
                         journal_e)
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
        "  nowEmpty: one('fullNowEmpty') ? one('fullNowEmpty').text : null, "
        "  limit: { section: !!one('fullLimitSection') && one('fullLimitSection').visible, "
        "    stopped: byY(all(v, 'fullStopped')).map(s => [s.stepId, s.text]), "
        "    others: one('fullOtherProviders') && one('fullOtherProviders').visible ? one('fullOtherProviders').text : null, "
        "    cost: one('fullTransferCost') && one('fullTransferCost').visible ? one('fullTransferCost').text : null, "
        "    button: one('fullTransfer') && one('fullTransfer').visible ? one('fullTransfer').text : null, "
        "    hidden: one('fullTransferHidden') && one('fullTransferHidden').visible ? one('fullTransferHidden').text : null } }) })()"))

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


    def test_scenario_e_limit_keeps_the_card_in_progress_without_a_decision(self):
        out = derived(fold(journal_e()), {"column": "todo"})
        self.assertEqual(out["column"], "progress")
        self.assertIn("czeka na limit do 16:30", out["note"])
        self.assertEqual(out["flow"]["decisions"], [])
        self.assertEqual([(s["ticket"], s["wipFiles"]) for s in out["flow"]["stoppedByLimit"]],
                         [("#2", 6), ("#4", 11)])
        self.assertEqual([p["state"] for p in out["phases"]][4], "hold")


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class FullViewTest(ProbeCase):
    def test_ticket_consent_button_calls_public_operation_with_current_context(self):
        events = journal_d()
        events.append({"type": "outside.ticket-consent", "seq": 200, "ticket": "#2"})
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["visible"] = js("page.cardFullView.flow.ticketConsents[0].active")
replies.append({"ok": False, "error": "stale-context", "message": "Zlecenie zmieniło się"})
click(at("(" + ALL + ")(page.cardFullView, 'revokeTicketConsent')[0]"))
out["consentError"] = js("page.cardFullView.consentError")
click(at("(" + ALL + ")(page.cardFullView, 'revokeTicketConsent')[0]"))
out["calls"] = calls()
entry = next(c for c in data["cards"] if c["id"] == "K-8")["derived"]["flow"]["ticketConsents"][0]
entry.update(active=False, revoked=True, canRevoke=False)
set_snapshot(data)
wait(100)
out["activeAfter"] = js("page.cardFullView.flow.ticketConsents[0].active")
""", full_card(self.data, events))
        self.assertTrue(out["visible"])
        sent = [c for c in out["calls"] if c[0] == "RevokeTicketConsent"]
        self.assertEqual(len(sent), 2, out)
        self.assertEqual(sent[0][1], "K-8")
        self.assertEqual(json.loads(sent[0][2]), {"ticket": "#2", "orderSeq": events[0]["seq"], "baseVersion": 0})
        self.assertEqual(out["consentError"], "Zlecenie zmieniło się")
        self.assertFalse(out["activeAfter"], "nowa migawka odświeża panel")

    def test_talk_button_and_escalated_decision_call_the_service(self):
        from unittest import mock
        from tide_daemon.cardflow.engine import FlowEngine
        from tide_daemon.cardflow import caretaker
        events = self.caretaker_events(escalated=True)
        engine = FlowEngine.__new__(FlowEngine)
        engine.journal = mock.Mock()
        engine.journal.read.return_value = events
        engine._caretaker_setup = lambda st: caretaker.setup(st)
        engine._limit_view = lambda *args: None
        computed = engine.derived({"id": "K-8", "column": "todo"})
        self.assertIn("talk-to-caretaker", [o["id"] for d in computed["flow"]["decisions"] for o in d["options"]])
        data = full_card(self.data, events)
        data["cardOpen"] = "full"
        next(c for c in data["cards"] if c["id"] == "K-8")["derived"] = computed
        out = self.probe(STATE_PY + """
click(card("K-8"))
click(named("talkToCaretaker"))
out["button"] = calls()
js("page.cardFullView.form.pick('talk-to-caretaker')")
# Wspólny widok pokazuje też bazę karty; długa decyzja wymaga przewinięcia do przycisku.
named("fullChoose")
js('''(function () {
    let flick = probeTarget.parent;
    while (flick && flick.contentY === undefined) flick = flick.parent;
    flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height,
        probeTarget.mapToItem(flick.contentItem, 0, 0).y - flick.height / 2));
})()''')
wait(80)
click(named("fullChoose"))
out["option"] = calls()
""", data)
        self.assertEqual(out["button"][-1], ["TalkToCaretaker", "K-8", "{}"])
        self.assertEqual(out["option"][-1][0:2], ["TalkToCaretaker", "K-8"])
        self.assertEqual(json.loads(out["option"][-1][2]), {"problem": "error:K-8-PT-2-1"})

    def test_scenario_e_lists_stopped_steps_and_moves_them_to_codex_after_showing_the_cost(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["opened"] = full()
click(named("fullTransfer"))
out["calls"] = calls()
out["after"] = full()
""", full_e(self.data))
        state = out["opened"]
        self.assertTrue(state["open"])
        self.assertEqual(state["card"], "K-8")
        self.assertEqual(state["headline"], "Kroki czekają na limit dostawcy do 16:30; Tide wznowi je sam, nie musisz nic robić.")
        self.assertEqual(state["options"], [], "limit nie jest decyzją")
        limit = state["limit"]
        self.assertTrue(limit["section"])
        self.assertEqual([s[0] for s in limit["stopped"]], ["K-8-ticket-2-0", "K-8-fix-4-1"])
        self.assertEqual(limit["stopped"][0][1], "#2 · K-8-ticket-2-0 — 6 plików WIP · czeka do 16:30")
        self.assertEqual(limit["stopped"][1][1], "#4 · K-8-fix-4-1 — 11 plików WIP · czeka do 16:30")
        self.assertEqual(limit["others"], "Pozostały limit: Codex 91%")
        self.assertEqual(limit["cost"], "2 zatrzymane kroki i 17 plików WIP do przekazania; "
                                        "po przeniesieniu Codex ma jeszcze 91% limitu.")
        self.assertEqual(limit["button"], "Przenieś na Codex")
        self.assertIsNone(limit["hidden"])
        self.assertEqual(state["now"], [], "zatrzymane kroki nie są na liście trwających")
        self.assertIsNone(state["nowEmpty"], "zatrzymane kroki to nie „nic nie pracuje”")
        self.assertEqual(
            [[c[0], c[1], json.loads(c[2])] for c in out["calls"] if c[0] == "ResolveCardStep"],
            [["ResolveCardStep", "K-8", {"choice": "transfer", "step": "K-8-ticket-2-0", "executor": "Codex mocny"}],
             ["ResolveCardStep", "K-8", {"choice": "transfer", "step": "K-8-fix-4-1", "executor": "Codex mocny"}]])

    def test_scenario_e_hides_the_action_with_one_sentence_when_codex_has_no_limit(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["opened"] = full()
""", full_e(self.data, codex=4))
        limit = out["opened"]["limit"]
        self.assertTrue(limit["section"])
        self.assertIsNone(limit["button"])
        self.assertIsNone(limit["cost"])
        self.assertEqual(limit["others"], "Pozostały limit: Codex 4%")
        self.assertEqual(limit["hidden"], "Nie ma innego dostawcy z wolnym limitem, więc nie ma dokąd przenieść "
                                          "zatrzymanych kroków.")

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

    def test_history_in_human_language_with_collapsed_old_orders_and_resume_only_for_current(self):
        now = datetime.now().astimezone()
        events = []

        def add(etype, ago_hours, /, **data):
            events.append({"v": 1, "seq": len(events) + 1, "ts": (now - timedelta(hours=ago_hours)).isoformat(),
                           "card": "K-8", "type": etype, **data})

        def step(name, ticket, ago, result=None, **extra):
            add("step.started", ago, step=name, ticket=ticket, kind="ticket", executor="Sonnet", provider="anthropic",
                model="sonnet", attempt=extra.pop("attempt", 0), parentStep=extra.pop("parent", ""),
                stepDir=f"/wt/{name}.step", worktree=f"/wt/{name}", branch="ticket/x")
            if result:
                add("step.finished", ago - 0.1, step=name, ticket=ticket, result=result, **extra)

        add("card.started", 60, entry="now", executor="Sonnet", program="claude", provider="anthropic")
        step("OLD-0", "PT-1", 59, "question", question="Stare pytanie?")
        add("card.started", 30, entry="now", executor="Opus", program="claude", provider="anthropic")
        step("NEW-0", "PT-2", 29, "error", reason="exit")
        step("NEW-1", "PT-2", 28, "error", reason="exit", attempt=1, parent="NEW-0")
        step("NEW-2", "PT-2", 27, "question", question="Nowe pytanie?", attempt=2, parent="NEW-1")
        step("NEW-3", "PT-3", 5, "done")
        data = full_card(self.data, events)
        data["cardOpen"] = "full"
        out = self.probe(STATE_PY + """
click(card("K-8"))
HIST =("(function () { const v = page.cardFullView; const all = " + ALL + "; "
        "return JSON.stringify(all(v, 'fullHistoryLine').sort((a, b) => a.mapToItem(v, 0, 0).y - b.mapToItem(v, 0, 0).y)"
        ".map(l => [l.text, l.when, l.byUser, all(l, 'historyResume').filter(b => b.visible).length, l.modelData.old])) })()")
out["collapsed"] = json.loads(js(HIST))
out["oldButton"] = js("page.cardFullView.oldHistory.length")
js("page.cardFullView.oldOpen = true")
out["expanded"] = json.loads(js(HIST))
out["enterBefore"] = len(calls())
click(at("(" + ALL + ")(page.cardFullView, 'historyResume')[0]"))
out["enter"] = calls()
""", data)
        collapsed = out["collapsed"]
        self.assertTrue(all(not row[4] for row in collapsed), "zwinięte zlecenia nie są widoczne")
        texts = [row[0] for row in collapsed]
        self.assertIn("PT-2: Sonnet — 3 próby, ostatnia: pyta", texts, "próby w jednej linii")
        self.assertEqual(len([t for t in texts if t.startswith("PT-2")]), 1)
        self.assertEqual(out["oldButton"], 2, "Wcześniejsze zlecenia (N): pierwsze zlecenie i jego krok")
        by_text = {row[0]: row for row in collapsed}
        self.assertEqual(by_text["PT-2: Sonnet — 3 próby, ostatnia: pyta"][3], 1, "„Wznów rozmowę” przy pytaniu")
        self.assertEqual(by_text["PT-3: Sonnet — gotowe"][3], 0, "gotowy krok bez „Wznów rozmowę”")
        self.assertTrue(by_text["Zlecenie: karta wystartowała (Opus)"][2], "zdarzenie użytkownika oznaczone")
        self.assertFalse(by_text["PT-3: Sonnet — gotowe"][2])
        for row in collapsed:
            self.assertRegex(row[1], r"^(dziś|wczoraj) \d\d:\d\d|^\d{1,2}\.\d\d \d\d:\d\d", row)
            self.assertNotRegex(row[0] + row[1], r"\d{4}-\d\d-\d\d|T\d\d:", "brak znaczników ISO")
        self.assertTrue(any(row[1].startswith("wczoraj") for row in collapsed), "czas względny po polsku")
        expanded = {row[0]: row for row in out["expanded"]}
        old_step = expanded["PT-1: Sonnet — pyta"]
        self.assertTrue(old_step[4])
        self.assertEqual(old_step[3], 0, "wcześniejsze zlecenia są bez „Wznów rozmowę”")
        self.assertEqual(out["enter"][-1], ["EnterStep", "K-8", "NEW-2"])

    def caretaker_events(self, escalated=False):
        """Karta K-8: PT-2 padł dwa razy, opiekun (Opus) sprawdza go; z `escalated` oddaje decyzję."""
        now = datetime.now().astimezone()
        events = []

        def add(etype, ago, /, **data):
            events.append({"v": 1, "seq": len(events) + 1, "ts": (now - timedelta(minutes=ago)).isoformat(),
                           "card": "K-8", "type": etype, **data})

        def step(name, kind, ago, attempt=0, parent="", **extra):
            add("step.started", ago, step=name, ticket="PT-2", kind=kind, executor=extra.pop("executor", "Sonnet"),
                provider="anthropic", model="x", attempt=attempt, parentStep=parent,
                stepDir=f"/wt/{name}.step", worktree="/wt/pt-2", branch="ticket/pt-2", **extra)

        add("card.started", 60, entry="now", executor="Sonnet", program="claude", provider="anthropic")
        step("K-8-PT-2-1", "ticket", 50)
        add("step.finished", 49, step="K-8-PT-2-1", ticket="PT-2", result="error", reason="exit")
        add("step.retry", 48, step="K-8-PT-2-1", ticket="PT-2")
        step("K-8-PT-2-2", "ticket", 47, attempt=1, parent="K-8-PT-2-1")
        add("step.finished", 46, step="K-8-PT-2-2", ticket="PT-2", result="error", reason="exit")
        add("caretaker.started", 45, problem="error:K-8-PT-2-1", key="caretaker:error:K-8-PT-2-1:1", n=1,
            step="K-8-care-1", target="K-8-PT-2-2", root="K-8-PT-2-1", ticket="PT-2", trigger="error",
            triggers=["error"], watermark=len(events))
        step("K-8-care-1", "caretaker", 44, executor="Opus")
        if escalated:
            add("step.finished", 20, step="K-8-care-1", ticket="PT-2", result="done", reason="")
            add("caretaker.escalated", 19, problem="error:K-8-PT-2-1", step="K-8-care-1", target="K-8-PT-2-2",
                ticket="PT-2", action="escalate", summary="Test pada za każdym razem.",
                recommendation="Zmień wykonawcę.", optionId="change-executor", failed=False, error="", code="")
        return events

    def test_caretaker_work_shows_in_now_in_background_your_move_and_history(self):
        data = full_card(self.data, self.caretaker_events())
        data["cardOpen"] = "full"
        derived_flow = [c for c in data["cards"] if c["id"] == "K-8"][0]["derived"]["flow"]
        # Dane prosto z usługi: dla kroku opiekuna usługa daje canEnter=False (wejście — PT-149/PT-158).
        self.assertIs(derived_flow["running"][0]["canEnter"], False)
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["full"] = full()
""", data)
        full_state = out["full"]
        self.assertEqual(full_state["headline"], "Opiekun zajmuje się PT-2, nic nie czeka na Ciebie.")
        self.assertEqual(full_state["now"], [["K-8-care-1", "PT-2 · opiekun · Opus", 0]],
                         "„Wejdź” jest ukryte dla kroku opiekuna")

    def test_view_shows_enter_for_a_running_step_exactly_when_can_enter_is_true(self):
        # Test samego widoku: przycisk „Wejdź” wynika wyłącznie z pola canEnter (nie z rodzaju kroku).
        data = full_card(self.data, self.caretaker_events())
        data["cardOpen"] = "full"
        derived_flow = [c for c in data["cards"] if c["id"] == "K-8"][0]["derived"]["flow"]
        derived_flow["running"][0]["canEnter"] = True
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["full"] = full()
click(at("(" + ALL + ")(page.cardFullView, 'fullNowEnter')[0]"))
out["enter"] = calls()
""", data)
        self.assertEqual(out["full"]["now"], [["K-8-care-1", "PT-2 · opiekun · Opus", 1]])
        self.assertEqual(out["enter"][-1], ["EnterStep", "K-8", "K-8-care-1"])

    def test_caretaker_escalation_is_signed_opiekun_in_history(self):
        data = full_card(self.data, self.caretaker_events(escalated=True))
        data["cardOpen"] = "full"
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["lines"] = json.loads(js("(function () { const v = page.cardFullView; const all = " + ALL + "; "
    "return JSON.stringify(all(v, 'fullHistoryLine').map(l => [l.text, "
    "all(l, 'historyCaretaker').filter(t => t.visible).map(t => t.text), "
    "all(l, 'historyKind').map(t => t.text)])) })()"))
""", data)
        lines = {row[0]: row for row in out["lines"]}
        signed = lines["Opiekun: oddaje decyzję — Test pada za każdym razem."]
        self.assertEqual((signed[1], signed[2]), (["Opiekun"], ["opiekun"]))
        self.assertTrue(all(row[1] == [] for row in out["lines"] if row is not signed),
                        "tylko wpisy opiekuna mają podpis")

    def test_card_without_caretaker_has_no_caretaker_signature(self):
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["full"] = full()
out["signed"] = js("(" + ALL + ")(page.cardFullView, 'historyCaretaker').filter(t => t.visible).length")
""", full_d(self.data))
        self.assertEqual(out["signed"], 0)

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

    def test_closed_card_offers_merge_to_main_in_the_full_card(self):
        # 03.11, K-16: po K-35 klik w domkniętą kartę otwierał pełną kartę bez „Scal do gałęzi głównej”.
        data = full_d(self.data)
        for item in data["cards"]:
            if item["id"] == "K-8":
                item["derived"]["flow"]["mainBranch"] = "master"
                item["derived"]["flow"]["base"] = {"status": "known", "baseRef": "refs/heads/master"}
                for phase in item["derived"]["phases"]:
                    if phase["id"] == "main-merge":
                        phase["state"] = "you"
        out = self.probe(STATE_PY + """
click(card("K-8"))
out["visible"] = js("(function () { const v = page.cardFullView; const all = " + ALL + "; "
                   "return all(v, 'fullMergeMain')[0].visible })()")
click(named("fullMergeMain"))
out["call"] = calls()[-1][:2]
""", data)
        self.assertTrue(out["visible"])
        self.assertEqual(out["call"], ["PreviewCardMerge", "K-8"])

    def test_named_main_requires_confirmation_and_cancel_is_only_a_read(self):
        data = full_d(self.data)
        for item in data["cards"]:
            if item["id"] == "K-8":
                item["derived"]["flow"]["mainBranch"] = "master"
                item["derived"]["flow"]["base"] = {"status": "known", "baseRef": "refs/heads/master"}
                for phase in item["derived"]["phases"]:
                    if phase["id"] == "main-merge":
                        phase["state"] = "you"
        names = ["master", "develop", "integration/custom-tide-main", "release/<b>" + "long-name-" * 18]
        out = self.probe(STATE_PY + "\nnames = " + repr(names) + r'''
click(card("K-8"))
out["labels"], out["texts"], out["rendered"] = [], [], []
for index, target in enumerate(names):
    snap = json.loads(backend.snapshotJson)
    next(c for c in snap["cards"] if c["id"] == "K-8")["derived"]["flow"]["mainBranch"] = target
    next(c for c in snap["cards"] if c["id"] == "K-8")["derived"]["flow"]["base"] = {"status": "known", "baseRef": "refs/heads/" + target}
    set_snapshot(snap)
    result = {"ok": True, "canMerge": True, "previewToken": "token-" + str(index),
              "targetRef": "refs/heads/" + target, "targetBranch": target, "targetCommit": "b" * 40,
              "sourceRef": "refs/heads/card/k-8", "sourceCommit": "a" * 40,
              "targetWorktree": "/repo", "targetWorktreeExists": True,
              "localCollisions": {"staged": [], "unstaged": [], "untracked": []}, "localCollisionCount": 0,
              "commits": [], "commitCount": 0, "files": [], "fileCount": 0, "conflicts": [], "conflictCount": 0}
    out["labels"].append(obj("fullMergeMain").property("text"))
    replies.append(result)
    click(named("fullMergeMain"))
    until(lambda: not obj("mergePreview").property("busy"))
    assert calls()[-1][0] == "PreviewCardMerge"
    # Anulowanie ani Escape nie są zgodą, nawet przy canMerge=true i check:auto.
    if index % 2:
        key(Qt.Key.Key_Escape)
    else:
        click(named("mergePreviewClose"))
    assert calls()[-1][0] == "PreviewCardMerge"
    replies.append(result)
    click(named("fullMergeMain"))
    until(lambda: not obj("mergePreview").property("busy"))
    out["texts"].append(obj("mergePreviewText").property("text"))
    button = obj("mergePreviewConfirm")
    out["labels"].append(button.property("text"))
    engine.rootContext().setContextProperty("probeTarget", button)
    out["rendered"].append(json.loads(js("JSON.stringify([probeTarget.contentItem.text, "
                                       "probeTarget.contentItem.textFormat, probeTarget.contentItem.truncated])")))
    replies.append({"ok": True, "targetBranch": target, "operationId": "op-" + str(index)})
    click(named("mergePreviewConfirm"))
    until(lambda: not obj("mergePreview").property("visible"))
out["calls"] = calls()
''', data)
        self.assertEqual(out["labels"], ["Scal do " + name for name in names for _ in range(2)])
        for index, name in enumerate(names):
            self.assertIn("refs/heads/" + name, out["texts"][index])
            self.assertIn("b" * 40, out["texts"][index])
            self.assertEqual(out["rendered"][index], ["Scal do " + name, 0, False])
            calls = out["calls"][3 * index:3 * index + 3]
            self.assertEqual([json.loads(c[2]) for c in calls[:2]], [{"targetRef": "refs/heads/" + name}] * 2)
            self.assertEqual(calls[2][:2], ["MergeCard", "K-8"])
            self.assertEqual(json.loads(calls[2][2]), {"targetRef": "refs/heads/" + name,
                                                     "previewToken": "token-" + str(index)})

    def test_stale_preview_requires_refresh_and_another_explicit_confirmation(self):
        data = full_d(self.data)
        for item in data["cards"]:
            if item["id"] == "K-8":
                item["derived"]["flow"]["mainBranch"] = "develop"
                item["derived"]["flow"]["base"] = {"status": "known", "baseRef": "refs/heads/develop"}
                for phase in item["derived"]["phases"]:
                    if phase["id"] == "main-merge":
                        phase["state"] = "you"
        out = self.probe(STATE_PY + r'''
click(card("K-8"))
result = {"ok": True, "canMerge": True, "targetBranch": "develop", "targetRef": "refs/heads/develop",
          "targetCommit": "b" * 40, "previewToken": "old"}
replies.append(result)
click(named("fullMergeMain"))
until(lambda: not obj("mergePreview").property("busy"))
replies.append({"ok": False, "error": "stale-preview", "message": "Potwierdź nowy podgląd"})
click(named("mergePreviewConfirm"))
until(lambda: not obj("mergePreview").property("busy"))
out["message"] = obj("mergePreviewText").property("text")
out["enabled"] = obj("mergePreviewConfirm").property("enabled")
replies.append({**result, "previewToken": "new", "targetCommit": "c" * 40})
click(named("mergePreviewRefresh"))
until(lambda: not obj("mergePreview").property("busy"))
out["beforeConfirm"] = calls()
out["text"] = obj("mergePreviewText").property("text")
replies.append({"ok": True, "targetBranch": "develop"})
click(named("mergePreviewConfirm"))
until(lambda: not obj("mergePreview").property("visible"))
out["calls"] = calls()
''', data)
        self.assertEqual(out["message"], "Potwierdź nowy podgląd")
        self.assertFalse(out["enabled"])
        self.assertEqual([c[0] for c in out["beforeConfirm"]], ["PreviewCardMerge", "MergeCard", "PreviewCardMerge"])
        self.assertIn("c" * 40, out["text"])
        self.assertEqual(json.loads(out["calls"][-1][2]), {"targetRef": "refs/heads/develop", "previewToken": "new"})

    def test_merge_preview_button_full_lists_refusals_and_close_only_read(self):
        data = full_d(self.data)
        for item in data["cards"]:
            if item["id"] == "K-8":
                item["derived"]["flow"]["mainBranch"] = "release/complete-target-name"
                item["derived"]["flow"]["base"] = {"status": "known", "baseRef": "refs/heads/release/complete-target-name"}
                for phase in item["derived"]["phases"]:
                    if phase["id"] == "main-merge":
                        phase["state"] = "you"
        result = {"ok": True, "sourceRef": "refs/heads/card/k-8", "sourceCommit": "a" * 40,
                  "targetRef": "refs/heads/release/complete-target-name", "targetCommit": "b" * 40,
                  "targetBranch": "release/complete-target-name", "targetWorktree": "/repo",
                  "targetWorktreeExists": True, "reason": "", "canMerge": True, "readyToMerge": False,
                  "warnings": ["Lokalne kolizje nie zostały ocenione."],
                  "commits": [{"sha": str(i).zfill(40), "subject": f"commit {i}"} for i in range(46)],
                  "commitCount": 46, "files": [{"status": "A", "oldPath": "", "newPath": f"file-{i}.txt"}
                                               for i in range(465)],
                  "fileCount": 465, "conflictCount": 0, "conflicts": []}
        variants = [result, {**result, "canMerge": False, "reason": "conflict", "message": "Konflikty",
                            "conflictCount": 2, "conflicts": ["conflict-a.txt", "conflict-b.txt"]},
                    {**result, "canMerge": False, "reason": "unrelated-history", "message": "Brak wspólnej historii",
                     "commits": [], "commitCount": 0, "files": [], "fileCount": 0},
                    {**result, "commits": [], "commitCount": 0, "files": [], "fileCount": 0}]
        out = self.probe(STATE_PY + "\nvariants = " + repr(variants) + r'''
click(card("K-8"))
out["label"] = obj("fullPreviewMerge").property("text")
before = js("JSON.stringify(page.snapshot.cards)")
out["texts"] = []
for index, result in enumerate(variants):
    replies.append(result)
    click(named("fullPreviewMerge"))
    until(lambda: not obj("mergePreview").property("busy"))
    out["texts"].append(obj("mergePreviewText").property("text"))
    if index % 2:
        key(Qt.Key.Key_Escape)
    else:
        click(named("mergePreviewClose"))
out["opened"] = obj("mergePreview").property("visible")
out["same"] = before == js("JSON.stringify(page.snapshot.cards)")
out["calls"] = calls()
''', data)
        self.assertEqual(out["label"], "Podgląd scalenia do release/complete-target-name")
        self.assertFalse(out["opened"])
        self.assertTrue(out["same"])
        self.assertEqual([json.loads(c[2]) for c in out["calls"]], [{"targetRef": result["targetRef"]}] * 4)
        for text in out["texts"]:
            self.assertIn(result["sourceRef"], text)
            self.assertIn(result["targetRef"], text)
            self.assertIn("Lokalne kolizje: nieocenione", text)
        self.assertIn("commit 45", out["texts"][0])
        self.assertIn("file-464.txt", out["texts"][0])
        self.assertIn("conflict-b.txt", out["texts"][1])
        self.assertIn("Brak wspólnej historii [unrelated-history]", out["texts"][2])
        self.assertIn("Wnoszone commity (0)", out["texts"][3])

    def test_merge_preview_shows_local_collisions_and_allows_unrelated_staged(self):
        data = full_d(self.data)
        for item in data["cards"]:
            if item["id"] == "K-8":
                item["derived"]["flow"]["mainBranch"] = "master"
                item["derived"]["flow"]["base"] = {"status": "known", "baseRef": "refs/heads/master"}
                for phase in item["derived"]["phases"]:
                    if phase["id"] == "main-merge":
                        phase["state"] = "you"
        result = {"ok": True, "sourceRef": "refs/heads/card/k-8", "sourceCommit": "a" * 40,
                  "targetRef": "refs/heads/master", "targetCommit": "b" * 40, "targetBranch": "master",
                  "targetWorktree": "/repo", "targetWorktreeExists": True, "canMerge": False,
                  "readyToMerge": False, "previewToken": "odcisk-stanu", "reason": "local-collision",
                  "message": "Lokalna praca koliduje", "localCollisionCount": 3,
                  "localCollisions": {kind: [{"status": code, "paths": [path], "mergePaths": [path]}]
                                      for kind, code, path in (("staged", "M ", "index.txt"),
                                                               ("unstaged", " M", "work.txt"),
                                                               ("untracked", "??", "new.txt"))}}
        staged = {**result, "reason": "", "message": "", "canMerge": True,
                  "localChanges": {"staged": [{"status": "MM", "paths": ["local.txt"]}],
                                   "unstaged": [{"status": "MM", "paths": ["local.txt"]}], "untracked": []},
                  "localCollisionCount": 0, "localCollisions": {k: [] for k in result["localCollisions"]}}
        out = self.probe(STATE_PY + "\nvariants = " + repr([result, staged]) + r'''
click(card("K-8"))
out["texts"] = []
out["enabled"] = []
for result in variants:
    replies.append(result)
    click(named("fullPreviewMerge"))
    until(lambda: not obj("mergePreview").property("busy"))
    out["texts"].append(obj("mergePreviewText").property("text"))
    out["enabled"].append(obj("mergePreviewConfirm").property("enabled"))
    click(named("mergePreviewClose"))
out["calls"] = calls()
''', data)
        self.assertEqual([c[:2] for c in out["calls"]], [["PreviewCardMerge", "K-8"]] * 2)
        self.assertEqual([json.loads(c[2]) for c in out["calls"]], [{"targetRef": "refs/heads/master"}] * 2)
        self.assertEqual(out["enabled"], [False, True])
        for text in out["texts"]:
            self.assertIn("Nie stanowi zgody", text)
            self.assertNotIn("odcisk-stanu", text)
        self.assertIn("Kolizje staged (indeks) (1)", out["texts"][0])
        self.assertIn("Kolizje unstaged (poza indeksem) (1)", out["texts"][0])
        self.assertIn("Kolizje untracked (nieśledzone) (1)", out["texts"][0])
        for path in ("index.txt", "work.txt", "new.txt"):
            self.assertIn(path, out["texts"][0])
        self.assertIn("Lokalne kolizje: 0", out["texts"][1])
        self.assertIn("Odczyt nie wykazał blokad", out["texts"][1])


if __name__ == "__main__":
    unittest.main()
