"""Okno ustawień ticketu trwającego (PT-335, K-97): przepięcie autora i recenzenta z „Ustawień” na kafelku planu
pełnej karty Tablicy (apps/board/qml/TicketSettingsDialog.qml, reguły w apps/common/qml/card/assignment.js).

Sonda offscreen na migawce z dziennika usługi (`fold` + `derived`), w której każdy ticket jest w innym stanie:
- #1 — poprawki po review czekają na limit (`step.waiting` kind fix): autor aktywny, review jeszcze nie czeka,
- #2 — review czeka na start (`step.waiting` kind review), klasa „mocny”: recenzent aktywny, wyszarzenia według klasy,
- #3 — krok autora trwa (`flow.running[]` z krokiem `ticket`): pole autora wyszarzone,
- #4 — review biegnie (`flow.running[]` z krokiem `review`): pole recenzenta wyszarzone,
- #5 — scalony: samo zdanie bez pól (tryb tylko do odczytu robi #10).

Warunki okna odwzorowują `_reassign_fix_author` i `_reassign_waiting_reviewer` w daemon/tide_daemon/cardflow/engine.py
(tabela w notce PT-335). Każde pole zapisuje się osobno jednym `ResolveCardStep`; odpowiedź usługi wraca przy polu,
okno się nie zamyka; Esc i „Zamknij” niczego nie wysyłają.
"""
import json
import os
import shutil
import unittest

from .decisionfix import full_card
from .test_ticket_settings import EXECUTORS, REASON_DOWN, REASON_SAME, REASON_SCOPE, REASON_STRONG, \
    STATE_PY as SETTINGS_STATE_PY
from .test_view import ProbeCase

LOCK_AUTHOR_WORKING = "Autor właśnie pracuje — zmienisz go, gdy skończy bieżący krok."
LOCK_REVIEW_RUNNING = "Review już biegnie."
LOCK_REVIEW_NOT_WAITING = "Review jeszcze nie czeka na start."


def journal_running() -> list[dict]:
    """Plan zatwierdzony, pięć ticketów w pięciu stanach (opis modułu)."""
    items = []

    def add(event_type, ts, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-03T{ts}+01:00", "card": "K-8",
                      "type": event_type, **data})

    def step(name, ticket, kind, executor, ts, **extra):
        add("step.started", ts, step=name, ticket=ticket, kind=kind, executor=executor,
            provider="openai" if executor.startswith("Codex") else "anthropic", model=executor.lower(), attempt=0,
            parentStep="", stepDir=f"/wt/{name}.step", worktree=f"/wt/{ticket}",
            branch=f"ticket/{ticket.strip('#')}", **extra)

    def finish(name, ticket, ts, result="done", **extra):
        add("step.finished", ts, step=name, ticket=ticket, result=result, **extra)

    def verdict(name, ticket, ts, value):
        add("review.verdict", ts, ticket=ticket, round=1, step=name, verdict=value,
            blockers=0, majors=0 if value == "APPROVE" else 1, nits=0)

    rows = [{"id": "#1", "workId": "#1", "title": "Poprawki na limicie", "executor": "Opus",
             "reviewer": "Codex standard", "class": "standard", "blockers": [], "body": "# 1\n"},
            {"id": "#2", "workId": "#2", "title": "Review czeka", "executor": "Opus",
             "reviewer": "Codex standard", "class": "mocny", "blockers": [], "body": "# 2\n"},
            {"id": "#3", "workId": "#3", "title": "Autor pracuje", "executor": "Sonnet", "reviewer": "Opus",
             "class": "lekki", "blockers": [], "body": "# 3\n"},
            {"id": "#4", "workId": "#4", "title": "Review biegnie", "executor": "Opus", "reviewer": "Sonnet",
             "class": "standard", "blockers": [], "body": "# 4\n"},
            {"id": "#5", "workId": "#5", "title": "Scalony", "executor": "Sonnet", "reviewer": "Opus",
             "class": "standard", "blockers": [], "body": "# 5\n"}]
    add("card.started", "09:00:00", entry="planning", executor="Opus", program="claude", provider="anthropic")
    add("plan.proposed", "09:10:00", planner="Opus", rows=[{k: v for k, v in r.items() if k != "id"} for r in rows])
    add("plan.reviewed", "09:20:00", reviewer="Sonnet", verdict="APPROVE", findings=[], corrected=False)
    add("plan.approved", "09:30:00", map={r["id"]: r["id"] for r in rows}, rows=rows)
    # #5: scalony do gałęzi karty.
    step("K-8-ticket-5-0", "#5", "ticket", "Sonnet", "09:31:00")
    finish("K-8-ticket-5-0", "#5", "09:40:00")
    step("K-8-review-5-1", "#5", "review", "Opus", "09:41:00", round=1)
    finish("K-8-review-5-1", "#5", "09:50:00")
    verdict("K-8-review-5-1", "#5", "09:50:00", "APPROVE")
    add("result", "09:51:00", action="merge", key="#5", ok=True)
    # #1: review prosi o zmiany, poprawki czekają na limit Claude.
    step("K-8-ticket-1-0", "#1", "ticket", "Opus", "10:00:00")
    finish("K-8-ticket-1-0", "#1", "10:30:00")
    step("K-8-review-1-1", "#1", "review", "Codex standard", "10:31:00", round=1)
    finish("K-8-review-1-1", "#1", "10:40:00")
    verdict("K-8-review-1-1", "#1", "10:40:00", "REQUEST_CHANGES")
    add("step.waiting", "10:41:00", ticket="#1", kind="fix", reason="limit Claude do 16:30")
    # #2: krok autora skończony, review czeka na start.
    step("K-8-ticket-2-0", "#2", "ticket", "Opus", "10:00:00")
    finish("K-8-ticket-2-0", "#2", "10:50:00")
    add("step.waiting", "10:51:00", ticket="#2", kind="review", reason="limit kroków dostawcy")
    # #3: autor pracuje.
    step("K-8-ticket-3-0", "#3", "ticket", "Sonnet", "11:00:00")
    # #4: review biegnie.
    step("K-8-ticket-4-0", "#4", "ticket", "Opus", "10:00:00")
    finish("K-8-ticket-4-0", "#4", "11:00:00")
    step("K-8-review-4-1", "#4", "review", "Sonnet", "11:01:00", round=1)
    return items


STATE_PY = SETTINGS_STATE_PY + r'''
def fields():
    engine.rootContext().setContextProperty("probeDlg", obj("ticketSettingsDialog"))
    return json.loads(js("(function () { const d = probeDlg; const all = " + ALL + "; "
        "const txt = (f, n) => { const t = all(f, n)[0]; return t ? t.text : '' }; "
        "const out = {}; for (const f of all(d, 'ticketSettingsField')) out[f.key] = { lock: txt(f, 'ticketSettingsLock'), "
        "  why: txt(f, 'ticketSettingsFieldWhy'), error: txt(f, 'ticketSettingsFieldError'), "
        "  note: txt(f, 'ticketSettingsFieldNote'), save: all(f, 'ticketSettingsFieldSave')[0].enabled }; "
        "const cls = all(d, 'ticketSettingsClass')[0]; "
        "return JSON.stringify({ fields: out, cls: cls ? cls.text : null }) })()"))

def field_save(key):
    engine.rootContext().setContextProperty("probeDlg", obj("ticketSettingsDialog"))
    click(at("(function () { const all = " + ALL + "; "
             "return all(probeDlg, 'ticketSettingsFieldSave').find(b => b.fieldKey === '" + key + "') })()"))
    wait(150)

def resolves():
    return [[c[1], json.loads(c[2])] for c in calls() if c[0] == "ResolveCardStep"]
'''


def snapshot(data: dict) -> dict:
    data = full_card(data, journal_running())
    data["models"] = {"checked": True, "default": "local", "agentProgram": "codex", "endpoints": {}, "roles": {},
                      "executors": EXECUTORS}
    return data


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class RunningTicketSettingsTest(ProbeCase):
    def probe_settings(self, scenario: str) -> dict:
        return self.probe(STATE_PY + "\njs(\"page.openFull('K-8')\")\nwait(200)\n" + scenario, snapshot(self.data))

    def test_snapshot_from_the_service_has_the_states_the_window_reads(self):
        """Migawka sondy jest z `fold` + `derived`: sprawdzamy, że ma pola, z których okno liczy blokady."""
        flow = snapshot(self.data)
        card = next(c for c in flow["cards"] if c["id"] == "K-8")
        flow = card["derived"]["flow"]
        self.assertEqual(sorted((r["id"], r["editable"]) for r in flow["plan"]["rows"]),
                         [(f"#{n}", False) for n in range(1, 6)])
        self.assertEqual(sorted((w["ticket"], w["kind"]) for w in flow["waiting"]), [("#1", "fix"), ("#2", "review")])
        self.assertEqual(sorted((s["ticket"], s["kind"]) for s in flow["running"]), [("#3", "ticket"), ("#4", "review")])
        statuses = {t["id"]: t["status"] for t in card["derived"]["tickets"]}
        self.assertEqual(statuses["#5"], "merged")

    def test_running_ticket_shows_read_only_class_and_two_fields(self):
        out = self.probe_settings("""
open_settings("#2")
out["dlg"] = dlg()
out["fields"] = fields()
""")
        dlg, view = out["dlg"], out["fields"]
        self.assertTrue(dlg["open"])
        self.assertIsNone(dlg["started"])
        self.assertIsNone(dlg["save"], "w trwającym tickecie nie ma zapisu EditCardPlan")
        self.assertIn("mocny", view["cls"])
        self.assertEqual(sorted({o[0] for o in dlg["options"]}), ["author", "reviewer"], "klasy nie da się wybrać")
        picked = sorted((o[0], o[1]) for o in dlg["options"] if o[2])
        self.assertEqual(picked, [("author", "Opus"), ("reviewer", "Codex standard")])
        self.assertEqual(view["fields"]["author"]["lock"], "")
        self.assertEqual(view["fields"]["reviewer"]["lock"], "")

    def test_reviewer_greying_follows_the_plan_class(self):
        out = self.probe_settings("""
open_settings("#2")
out["strong"] = {o[1]: o[3:] for o in dlg()["options"] if o[0] == "reviewer"}
out["authors"] = {o[1]: o[3:] for o in dlg()["options"] if o[0] == "author"}
key(Qt.Key.Key_Escape)
open_settings("#1")
out["standard"] = {o[1]: o[3:] for o in dlg()["options"] if o[0] == "reviewer"}
""")
        strong = out["strong"]
        self.assertEqual(strong["Sonnet"], [True, REASON_STRONG], "klasa mocny z wiersza planu: inna rodzina")
        self.assertEqual(strong["Opus"], [True, REASON_SAME])
        self.assertEqual(strong["Codex mocny"], [False, ""])
        self.assertEqual(strong["Lokalny"], [True, REASON_SCOPE])
        self.assertEqual(strong["Codex Astra"], [True, REASON_DOWN])
        self.assertEqual(out["authors"]["Codex Astra"], [True, REASON_DOWN])
        self.assertEqual(out["authors"]["Sonnet"], [False, ""])
        self.assertEqual(out["standard"]["Sonnet"], [False, ""], "przy klasie standard ta sama rodzina wolno")

    def test_fix_waiting_on_limit_sends_one_reassign_author(self):
        out = self.probe_settings("""
open_settings("#1")
out["before"] = fields()["fields"]
pick("author", "Codex standard")
field_save("author")
out["sent"] = resolves()
out["after"] = fields()["fields"]
out["dlg"] = dlg()
field_save("reviewer")
out["sentAfterReviewer"] = resolves()
""")
        before = out["before"]
        self.assertEqual(before["author"]["lock"], "")
        self.assertTrue(before["author"]["save"])
        self.assertEqual(before["reviewer"]["lock"], LOCK_REVIEW_NOT_WAITING)
        self.assertFalse(before["reviewer"]["save"])
        self.assertEqual(out["sent"], [["K-8", {"choice": "reassign-author", "ticket": "#1",
                                                "executor": "Codex standard"}]])
        self.assertTrue(out["dlg"]["open"], "okno się nie zamyka")
        self.assertIn("Codex standard", out["after"]["author"]["note"])
        self.assertEqual(out["after"]["author"]["error"], "")
        self.assertEqual(out["sentAfterReviewer"], out["sent"], "wyszarzone pole recenzenta niczego nie wysyła")

    def test_author_working_greys_out_the_author_field_and_sends_nothing(self):
        out = self.probe_settings("""
open_settings("#3")
out["fields"] = fields()["fields"]
pick("author", "Opus")
out["picked"] = picked("author")
field_save("author")
field_save("reviewer")
out["sent"] = resolves()
""")
        self.assertEqual(out["fields"]["author"]["lock"], LOCK_AUTHOR_WORKING)
        self.assertFalse(out["fields"]["author"]["save"])
        self.assertEqual(out["fields"]["reviewer"]["lock"], LOCK_REVIEW_NOT_WAITING)
        self.assertEqual(out["picked"], ["Sonnet"], "wyszarzone pole nie przyjmuje wyboru")
        self.assertEqual(out["sent"], [])

    def test_waiting_review_sends_one_reassign_reviewer(self):
        out = self.probe_settings("""
open_settings("#2")
pick("reviewer", "Codex mocny")
field_save("reviewer")
out["sent"] = resolves()
out["fields"] = fields()["fields"]
out["dlg"] = dlg()
""")
        self.assertEqual(out["sent"], [["K-8", {"choice": "reassign-reviewer", "ticket": "#2",
                                                "executor": "Codex mocny"}]])
        self.assertTrue(out["dlg"]["open"])
        self.assertIn("Codex mocny", out["fields"]["reviewer"]["note"])
        self.assertEqual(out["fields"]["author"]["note"], "", "odpowiedź wraca tylko przy swoim polu")

    def test_running_review_greys_out_the_reviewer_field(self):
        out = self.probe_settings("""
open_settings("#4")
out["fields"] = fields()["fields"]
pick("reviewer", "Codex standard")
out["picked"] = picked("reviewer")
field_save("reviewer")
out["sent"] = resolves()
""")
        self.assertEqual(out["fields"]["reviewer"]["lock"], LOCK_REVIEW_RUNNING)
        self.assertFalse(out["fields"]["reviewer"]["save"])
        self.assertEqual(out["fields"]["author"]["lock"], "", "autor kolejnych kroków po review — wolno")
        self.assertEqual(out["picked"], ["Sonnet"])
        self.assertEqual(out["sent"], [])

    def test_service_refusals_come_back_next_to_the_field_and_the_window_stays(self):
        refusals = [{"ok": False, "error": "not-waiting", "message": "K-8: review ticketu #2 nie czeka na start"},
                    {"ok": False, "error": "bad-executor", "message": "wybierz autora z katalogu wykonawców"},
                    {"ok": False, "error": "already-decided", "message": "decyzja o tym kroku została już zapisana"}]
        out = self.probe_settings("""
replies.extend(json.loads(%r))
open_settings("#2")
pick("reviewer", "Codex mocny")
field_save("reviewer")
out["reviewer"] = fields()["fields"]
pick("author", "Sonnet")
field_save("author")
out["author"] = fields()["fields"]
field_save("author")
out["again"] = fields()["fields"]
out["dlg"] = dlg()
out["sent"] = resolves()
""" % json.dumps(refusals, ensure_ascii=False))
        first = out["reviewer"]
        self.assertEqual(first["reviewer"]["error"], refusals[0]["message"])
        self.assertEqual(first["author"]["error"], "")
        self.assertTrue(first["reviewer"]["save"], "po odmowie można ponowić")
        self.assertEqual(out["author"]["author"]["error"], refusals[1]["message"])
        self.assertEqual(out["author"]["reviewer"]["error"], refusals[0]["message"], "błąd drugiego pola zostaje")
        self.assertEqual(out["again"]["author"]["error"], refusals[2]["message"])
        self.assertTrue(out["dlg"]["open"])
        self.assertEqual([r[1]["choice"] for r in out["sent"]],
                         ["reassign-reviewer", "reassign-author", "reassign-author"])

    def test_escape_and_close_send_nothing(self):
        out = self.probe_settings("""
open_settings("#2")
pick("reviewer", "Codex mocny")
pick("author", "Sonnet")
key(Qt.Key.Key_Return)
key(Qt.Key.Key_Escape)
out["afterEsc"] = [dlg()["open"], js("page.fullOpen")]
open_settings("#2")
out["reopened"] = [picked("author"), picked("reviewer")]
click(named("ticketSettingsCancel"))
out["afterCancel"] = [dlg()["open"], js("page.fullOpen")]
out["calls"] = [c for c in calls() if c[0] in ("ResolveCardStep", "EditCardPlan")]
""")
        self.assertEqual(out["afterEsc"], [False, True])
        self.assertEqual(out["reopened"], [["Opus"], ["Codex standard"]], "porzucony wybór nie zostaje")
        self.assertEqual(out["afterCancel"], [False, True])
        self.assertEqual(out["calls"], [])

    def test_merged_ticket_keeps_the_sentence_without_fields(self):
        out = self.probe_settings("""
open_settings("#5")
out["dlg"] = dlg()
out["fields"] = fields()
""")
        self.assertEqual(out["dlg"]["started"], "Ticket już ruszył — zmiana w następnym kroku")
        self.assertEqual(out["dlg"]["options"], [])
        self.assertEqual(out["fields"], {"fields": {}, "cls": None})


if __name__ == "__main__":
    unittest.main()
