"""Okno ustawień ticketu: „Ponów innym wykonawcą”, tryb tylko do odczytu i karta „Od razu” (PT-341, K-97).

Sonda offscreen na migawkach z dziennika usługi (`fold` + `derived`, decyzje z `build_decisions`):
- #1 — krok autora dwa razy zakończony błędem: czeka decyzja `error` z możliwością `change-executor`,
- #2 — scalony do gałęzi karty, #3 — odrzucony (tylko do odczytu, ze zdaniem dlaczego),
- #4 — krok autora trwa, bez decyzji (pola „Ponów…” nie ma),
Zabezpieczenie przed `no-step-question` (inny krok tego ticketu trwa) sprawdza test na migawce z dopisanym trwającym
krokiem: usługa przestaje pytać o ticket, który ma nowszy krok, więc taki stan w dzienniku sam nie powstaje.

„Ponów innym wykonawcą” nie ma drugiej logiki obok okna decyzji: `params` możliwości (`choice`, `step`) idą z decyzji
usługi, a okno dokłada wybranego wykonawcę. Test porównuje wywołanie z okna ustawień z wywołaniem okna decyzji dla tej
samej decyzji (możliwość z wykonawcą wpisanym z góry nie wymaga wyboru w panelu, więc okno decyzji wysyła ją wprost).
Karta „Od razu”: kafelek jedynego ticketu ma „Ustawienia” (w Tablicy; w Sesjach nie), klasa „standard” jest tylko do
odczytu z dopiskiem, że to klasa domyślna.
"""
import json
import os
import shutil
import unittest

from .decisionfix import CARD, build_decisions, derived, fold, full_card
from .test_now_ticket_tile import now_journal
from .test_ticket_settings import EXECUTORS, REASON_DOWN
from .test_ticket_settings_running import STATE_PY, LOCK_AUTHOR_WORKING, LOCK_REVIEW_NOT_WAITING
from .test_view import ProbeCase

REASON_SCOPE_RETRY = "praca lokalna i chmurowa się nie mieszają"
LOCK_RETRY = "Krok tego ticketu właśnie pracuje — ponowisz go, gdy skończy."
SENTENCE_MERGED = "Ticket jest scalony do gałęzi karty — wszystkie pola są tylko do odczytu."
SENTENCE_REJECTED = "Ticket został odrzucony — wszystkie pola są tylko do odczytu."


def journal_retry() -> list[dict]:
    """Plan zatwierdzony, cztery tickety (opis modułu)."""
    items = []

    def add(event_type, ts, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-03T{ts}+01:00", "card": CARD,
                      "type": event_type, **data})

    def step(name, ticket, kind, executor, ts, attempt=0, parent="", **extra):
        add("step.started", ts, step=name, ticket=ticket, kind=kind, executor=executor,
            provider="openai" if executor.startswith("Codex") else "anthropic", model=executor.lower(),
            attempt=attempt, parentStep=parent, stepDir=f"/wt/{name}.step", worktree=f"/wt/{ticket}",
            branch=f"ticket/{ticket.strip('#')}", **extra)

    def failing(ticket, executor, ts):
        """Dwie nieudane próby kroku autora: po drugiej usługa pyta użytkownika."""
        first, second = f"K-8-ticket-{ticket.strip('#')}-0", f"K-8-ticket-{ticket.strip('#')}-1"
        step(first, ticket, "ticket", executor, ts)
        add("step.finished", ts, step=first, ticket=ticket, result="error", reason="awaria testowa")
        step(second, ticket, "ticket", executor, ts, attempt=1, parent=first)
        add("step.finished", ts, step=second, ticket=ticket, result="error", reason="awaria testowa")

    rows = [{"id": f"#{n}", "workId": f"#{n}", "title": title, "executor": author, "reviewer": reviewer,
             "class": cls, "blockers": [], "body": f"# {n}\n"}
            for n, (title, author, reviewer, cls) in enumerate([
                ("Błąd kroku", "Opus", "Sonnet", "standard"), ("Scalony", "Sonnet", "Opus", "standard"),
                ("Odrzucony", "Sonnet", "Opus", "lekki"), ("Autor pracuje", "Opus", "Codex standard", "standard")], 1)]
    add("card.started", "09:00:00", entry="planning", executor="Opus", program="claude", provider="anthropic")
    add("plan.proposed", "09:10:00", planner="Opus", rows=[{k: v for k, v in r.items() if k != "id"} for r in rows])
    add("plan.reviewed", "09:20:00", reviewer="Sonnet", verdict="APPROVE", findings=[], corrected=False)
    add("plan.approved", "09:30:00", map={r["id"]: r["id"] for r in rows}, rows=rows)
    failing("#1", "Opus", "10:00:00")
    step("K-8-ticket-2-0", "#2", "ticket", "Sonnet", "10:01:00")
    add("step.finished", "10:10:00", step="K-8-ticket-2-0", ticket="#2", result="done")
    step("K-8-review-2-1", "#2", "review", "Opus", "10:11:00", round=1)
    add("step.finished", "10:20:00", step="K-8-review-2-1", ticket="#2", result="done")
    add("review.verdict", "10:20:00", ticket="#2", round=1, step="K-8-review-2-1", verdict="APPROVE",
        blockers=0, majors=0, nits=0)
    add("result", "10:21:00", action="merge", key="#2", ok=True)
    step("K-8-ticket-3-0", "#3", "ticket", "Sonnet", "10:02:00")
    add("step.finished", "10:30:00", step="K-8-ticket-3-0", ticket="#3", result="error", reason="awaria testowa")
    add("ticket.rejected", "10:31:00", ticket="#3")
    step("K-8-ticket-4-0", "#4", "ticket", "Opus", "10:03:00")
    return items


def journal_now_error() -> list[dict]:
    """Karta „Od razu” (PT-7): krok autora dwa razy zakończony błędem — czeka decyzja z `change-executor`."""
    items = now_journal("ticket")

    def add(event_type, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-03T11:{len(items):02d}:00+01:00", "card": CARD,
                      "type": event_type, **data})

    add("step.finished", step="K-8-ticket-0", ticket="PT-7", result="error", reason="awaria testowa")
    add("step.started", step="K-8-ticket-1", ticket="PT-7", kind="ticket", executor="Sonnet", provider="anthropic",
        model="sonnet", attempt=1, parentStep="K-8-ticket-0", stepDir="/wt/K-8-ticket-1.step", worktree="/wt/pt-7",
        branch="ticket/pt-7")
    add("step.finished", step="K-8-ticket-1", ticket="PT-7", result="error", reason="awaria testowa")
    return items


def journal_now_review_waiting() -> list[dict]:
    """Karta „Od razu”: krok autora skończony, review czeka na start (limit kroków dostawcy)."""
    items = now_journal("ticket")
    items.append({"v": 1, "seq": len(items) + 1, "ts": "2026-11-03T11:30:00+01:00", "card": CARD,
                  "type": "step.finished", "step": "K-8-ticket-0", "ticket": "PT-7", "result": "done"})
    items.append({"v": 1, "seq": len(items) + 1, "ts": "2026-11-03T11:31:00+01:00", "card": CARD,
                  "type": "step.waiting", "ticket": "PT-7", "kind": "review", "reason": "limit kroków dostawcy"})
    return items


def snapshot(data: dict, events: list[dict] | None = None) -> dict:
    data = full_card(data, journal_retry() if events is None else events)
    data["models"] = {"checked": True, "default": "local", "agentProgram": "codex", "endpoints": {}, "roles": {},
                      "executors": EXECUTORS}
    return data


def change_executor(events: list[dict], ticket: str) -> dict:
    """Możliwość `change-executor` decyzji błędu ticketu, tak jak ją zbudowała usługa."""
    decision = next(d for d in build_decisions(fold(events)) if d["kind"] == "error" and d["ticket"] == ticket)
    return next(o for o in decision["options"] if o["id"] == "change-executor")


RETRY_PY = r'''
# Kafelki dalsze niż pierwszy leżą poza widokiem pełnej karty: przed kliknięciem przewijamy do kafelka.
_open_settings = open_settings

def open_settings(ticket):
    js("(function () { const v = page.cardFullView; const all = " + ALL + "; "
       "const tile = all(v, 'fullTicket').find(t => t.ticketId === '" + ticket + "'); "
       "let f = tile.parent; while (f && f.contentY === undefined) f = f.parent; "
       "if (f) f.contentY = Math.max(0, Math.min(f.contentHeight - f.height, tile.mapToItem(f.contentItem, 0, 0).y - 40)) })()")
    wait(80)
    _open_settings(ticket)

def readonly():
    engine.rootContext().setContextProperty("probeDlg", obj("ticketSettingsDialog"))
    return json.loads(js("(function () { const d = probeDlg; const all = " + ALL + "; "
        "const txt = n => all(d, n).filter(t => t.visible).map(t => t.text); "
        "return JSON.stringify({ sentence: txt('ticketSettingsFinished'), rows: txt('ticketSettingsReadonly'), "
        "  started: txt('ticketSettingsStarted'), groups: all(d, 'ticketSettingsOption').length, "
        "  fields: all(d, 'ticketSettingsField').length, save: all(d, 'ticketSettingsSave').filter(b => b.visible).length, "
        "  close: all(d, 'ticketSettingsCancel')[0].text }) })()"))
'''


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class RetryFieldTest(ProbeCase):
    def probe_settings(self, scenario: str, events=None, data=None) -> dict:
        data = data or snapshot(self.data, events)
        return self.probe(STATE_PY + RETRY_PY + "\njs(\"page.openFull('K-8')\")\nwait(200)\n" + scenario, data)

    def test_fixture_comes_from_the_service(self):
        option = change_executor(journal_retry(), "#1")
        self.assertEqual(option["method"], "ResolveCardStep")
        self.assertEqual(option["params"], {"choice": "change-executor", "step": "K-8-ticket-1-1", "executor": ""})
        errors = {d["ticket"] for d in build_decisions(fold(journal_retry())) if d["kind"] == "error"}
        self.assertEqual(errors, {"#1"}, "#4 pracuje, #2 i #3 skończone — bez decyzji błędu")

    def test_error_decision_adds_the_field_and_sends_the_same_call_as_the_decision_window(self):
        option = change_executor(journal_retry(), "#1")
        settings = self.probe_settings("""
open_settings("#1")
out["fields"] = fields()["fields"]
out["listed"] = [o[1] for o in dlg()["options"] if o[0] == "retry"]
pick("retry", "Sonnet")
field_save("retry")
out["sent"] = resolves()
out["after"] = fields()["fields"]
out["dlg"] = dlg()
field_save("retry")
out["again"] = resolves()
""")
        retry = settings["fields"]["retry"]
        self.assertEqual((retry["lock"], retry["error"], retry["note"]), ("", "", ""))
        self.assertEqual(settings["listed"], [e["name"] for e in EXECUTORS])
        self.assertEqual(settings["sent"], [["K-8", {**option["params"], "executor": "Sonnet"}]])
        self.assertIn("Sonnet", settings["after"]["retry"]["note"])
        self.assertTrue(settings["dlg"]["open"], "okno się nie zamyka")
        self.assertEqual(settings["again"], settings["sent"], "po zapisie „Ponów” jest zużyte — nic nie leci drugi raz")
        # Okno decyzji dla tej samej decyzji, z wykonawcą wpisanym z góry w możliwości (bez wyboru w panelu).
        data = snapshot(self.data)
        card = next(c for c in data["cards"] if c["id"] == "K-8")
        decisions = card["derived"]["flow"]["decisions"]
        decision = next(d for d in decisions if d["kind"] == "error" and d["ticket"] == "#1")
        index = [o["id"] for o in decision["options"]].index("change-executor")
        decision["options"][index]["params"]["executor"] = "Sonnet"
        card["derived"]["flow"]["decisions"] = [decision]
        window = self.probe(STATE_PY + f"""
click(card("K-8"))
click(option({index}))
key(Qt.Key.Key_Return)
out["sent"] = [[c[0], c[1], json.loads(c[2])] for c in calls() if c[0] == "ResolveCardStep"]
""", data)
        self.assertEqual(window["sent"], [["ResolveCardStep", "K-8", settings["sent"][0][1]]],
                         "te same parametry: jedno ResolveCardStep z choice, step i executor")
        self.assertEqual(window["sent"][0][2]["choice"], "change-executor")

    def test_without_an_error_decision_there_is_no_retry_field(self):
        out = self.probe_settings("""
open_settings("#4")
out["running"] = fields()["fields"]
key(Qt.Key.Key_Escape)
open_settings("#2")
out["merged"] = fields()["fields"]
""")
        self.assertEqual(sorted(out["running"]), ["author", "reviewer"])
        self.assertEqual(out["merged"], {})

    def test_greying_follows_the_service_rules(self):
        out = self.probe_settings("""
open_settings("#1")
out["list"] = {o[1]: o[3:] for o in dlg()["options"] if o[0] == "retry"}
pick("retry", "Lokalny")
pick("retry", "Codex Astra")
out["picked"] = picked("retry")
field_save("retry")
out["sent"] = resolves()
out["save"] = fields()["fields"]["retry"]["save"]
""")
        self.assertEqual(out["list"]["Lokalny"], [True, REASON_SCOPE_RETRY], "recenzent Sonnet jest chmurowy")
        self.assertEqual(out["list"]["Codex Astra"], [True, REASON_DOWN])
        self.assertEqual(out["list"]["Opus"], [False, ""], "ten sam wykonawca co nieudany krok jest dozwolony")
        self.assertEqual(out["picked"], [], "wyszarzone pozycje nie przyjmują wyboru")
        self.assertEqual(out["sent"], [])
        self.assertFalse(out["save"])

    def test_nothing_is_sent_without_a_chosen_executor(self):
        out = self.probe_settings("""
open_settings("#1")
out["fields"] = fields()["fields"]
field_save("retry")
out["sent"] = resolves()
""")
        self.assertFalse(out["fields"]["retry"]["save"])
        self.assertEqual(out["fields"]["retry"]["why"], "Wybierz wykonawcę z katalogu.")
        self.assertEqual(out["sent"], [])

    def test_another_running_step_of_the_ticket_locks_the_field(self):
        data = snapshot(self.data)
        flow = next(c for c in data["cards"] if c["id"] == "K-8")["derived"]["flow"]
        flow["running"] = flow["running"] + [{"id": "K-8-review-1-9", "ticket": "#1", "kind": "review",
                                              "executor": "Sonnet", "mode": "background"}]
        out = self.probe_settings("""
open_settings("#1")
out["fields"] = fields()["fields"]
pick("retry", "Sonnet")
out["picked"] = picked("retry")
field_save("retry")
out["sent"] = resolves()
""", data=data)
        self.assertEqual(out["fields"]["retry"]["lock"], LOCK_RETRY)
        self.assertFalse(out["fields"]["retry"]["save"])
        self.assertEqual(out["picked"], [])
        self.assertEqual(out["sent"], [])

    def test_service_refusals_come_back_next_to_the_field_and_the_window_stays(self):
        refusals = [{"ok": False, "error": "not-waiting", "message": "K-8: krok K-8-ticket-1-1 nie czeka na decyzję"},
                    {"ok": False, "error": "bad-executor", "message": "wybierz wykonawcę"},
                    {"ok": False, "error": "already-decided", "message": "K-8: odpowiedź na ten krok jest już zapisana"},
                    {"ok": False, "error": "no-step-question", "message": "K-8: ten krok nie czeka na taką decyzję"}]
        out = self.probe_settings("""
replies.extend(json.loads(%r))
open_settings("#1")
pick("retry", "Sonnet")
shots = []
for _ in range(4):
    field_save("retry")
    shots.append(fields()["fields"]["retry"])
out["shots"] = shots
out["author"] = fields()["fields"]["author"]
out["dlg"] = dlg()
out["sent"] = resolves()
""" % json.dumps(refusals, ensure_ascii=False))
        self.assertEqual([s["error"] for s in out["shots"]], [r["message"] for r in refusals])
        self.assertTrue(all(s["save"] and not s["note"] for s in out["shots"]), "po odmowie można ponowić")
        self.assertEqual(out["author"]["error"], "", "błąd tylko przy swoim polu")
        self.assertTrue(out["dlg"]["open"])
        self.assertEqual(len(out["sent"]), 4)

    def test_escape_and_close_send_nothing(self):
        out = self.probe_settings("""
open_settings("#1")
pick("retry", "Sonnet")
key(Qt.Key.Key_Return)
key(Qt.Key.Key_Escape)
out["afterEsc"] = [dlg()["open"], js("page.fullOpen")]
open_settings("#1")
out["reopened"] = picked("retry")
click(named("ticketSettingsCancel"))
out["afterCancel"] = dlg()["open"]
out["calls"] = [c for c in calls() if c[0] in ("ResolveCardStep", "EditCardPlan")]
""")
        self.assertEqual(out["afterEsc"], [False, True])
        self.assertEqual(out["reopened"], [], "porzucony wybór nie zostaje")
        self.assertFalse(out["afterCancel"])
        self.assertEqual(out["calls"], [])


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class ReadOnlyTicketTest(ProbeCase):
    def probe_settings(self, scenario: str, events=None) -> dict:
        return self.probe(STATE_PY + RETRY_PY + "\njs(\"page.openFull('K-8')\")\nwait(200)\n" + scenario,
                          snapshot(self.data, events))

    def test_merged_and_rejected_tickets_are_read_only_with_a_sentence(self):
        out = self.probe_settings("""
for ticket in ("#2", "#3"):
    open_settings(ticket)
    out[ticket] = {"view": readonly(), "dlg": dlg()}
    key(Qt.Key.Key_Return)
    key(Qt.Key.Key_Escape)
out["calls"] = [c for c in calls() if c[0] in ("ResolveCardStep", "EditCardPlan")]
""")
        merged, rejected = out["#2"]["view"], out["#3"]["view"]
        self.assertEqual(merged["sentence"], [SENTENCE_MERGED])
        self.assertEqual(rejected["sentence"], [SENTENCE_REJECTED])
        self.assertEqual(merged["rows"], ["Wykonawca: Sonnet", "Recenzent: Opus", "Klasa: standard"])
        self.assertEqual(rejected["rows"], ["Wykonawca: Sonnet", "Recenzent: Opus", "Klasa: lekki"])
        for view in (merged, rejected):
            self.assertEqual((view["groups"], view["fields"], view["save"], view["started"]), (0, 0, 0, []))
            self.assertEqual(view["close"], "Zamknij")
        self.assertEqual(out["calls"], [], "nic nie da się wysłać")


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class NowCardSettingsTest(ProbeCase):
    def probe_now(self, events, scenario: str) -> dict:
        return self.probe(STATE_PY + RETRY_PY + "\njs(\"page.openFull('K-8')\")\nwait(200)\n" + scenario,
                          snapshot(self.data, events))

    def test_now_snapshot_has_no_plan_rows_and_the_ticket_tile_has_the_button(self):
        flow = derived(fold(now_journal("ticket")), {"column": "todo"})["flow"]
        self.assertEqual(flow["entry"], "now")
        self.assertFalse(flow["plan"]["rows"] if flow.get("plan") else [], "karta „Od razu” nie ma wiersza planu")
        out = self.probe_now(now_journal("ticket"), 'out["tiles"] = tiles()')
        self.assertEqual(out["tiles"], [["PT-7", 1]])

    def test_running_ticket_shows_the_default_class_read_only_and_locks_the_author(self):
        out = self.probe_now(now_journal("ticket"), """
open_settings("PT-7")
out["dlg"] = dlg()
out["view"] = fields()
""")
        self.assertTrue(out["dlg"]["open"])
        self.assertIsNone(out["dlg"]["started"])
        self.assertIn("Klasa: standard", out["view"]["cls"])
        self.assertIn("klasa domyślna", out["view"]["cls"])
        self.assertEqual(sorted({o[0] for o in out["dlg"]["options"]}), ["author", "reviewer"], "klasy nie da się wybrać")
        self.assertIsNone(out["dlg"]["save"])
        self.assertEqual(out["view"]["fields"]["author"]["lock"], LOCK_AUTHOR_WORKING)
        self.assertEqual(out["view"]["fields"]["reviewer"]["lock"], LOCK_REVIEW_NOT_WAITING)

    def test_waiting_review_reassigns_author_and_reviewer_like_in_a_plan_card(self):
        out = self.probe_now(journal_now_review_waiting(), """
open_settings("PT-7")
out["before"] = fields()["fields"]
pick("author", "Codex standard")
field_save("author")
pick("reviewer", "Codex mocny")
field_save("reviewer")
out["sent"] = resolves()
out["after"] = fields()["fields"]
out["dlg"] = dlg()
""")
        self.assertEqual(out["before"]["author"]["lock"], "")
        self.assertEqual(out["before"]["reviewer"]["lock"], "")
        self.assertEqual(out["sent"], [["K-8", {"choice": "reassign-author", "ticket": "PT-7",
                                                "executor": "Codex standard"}],
                                       ["K-8", {"choice": "reassign-reviewer", "ticket": "PT-7",
                                                "executor": "Codex mocny"}]])
        self.assertIn("Codex standard", out["after"]["author"]["note"])
        self.assertIn("Codex mocny", out["after"]["reviewer"]["note"])
        self.assertTrue(out["dlg"]["open"])

    def test_error_decision_gives_the_retry_field_here_too(self):
        events = journal_now_error()
        option = change_executor(events, "PT-7")
        out = self.probe_now(events, """
open_settings("PT-7")
pick("retry", "Opus")
field_save("retry")
out["sent"] = resolves()
out["dlg"] = dlg()
""")
        self.assertEqual(out["sent"], [["K-8", {**option["params"], "executor": "Opus"}]])
        self.assertTrue(out["dlg"]["open"])

    def test_closed_card_ticket_is_read_only(self):
        out = self.probe_now(now_journal("closed"), """
open_settings("PT-7")
out["view"] = readonly()
out["calls"] = [c for c in calls() if c[0] in ("ResolveCardStep", "EditCardPlan")]
""")
        self.assertEqual(out["view"]["sentence"], [SENTENCE_MERGED])
        self.assertEqual(out["view"]["rows"], ["Wykonawca: Sonnet", "Recenzent: Opus", "Klasa: standard (klasa domyślna)"])
        self.assertEqual((out["view"]["fields"], out["view"]["save"]), (0, 0))
        self.assertEqual(out["calls"], [])


if __name__ == "__main__":
    unittest.main()
