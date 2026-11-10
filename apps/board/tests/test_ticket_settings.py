"""Okno ustawień ticketu nieruszonego (PT-334, K-97): przycisk „Ustawienia” na kafelku planu pełnej karty Tablicy
i okno z wyborem wykonawcy, recenzenta i klasy (apps/board/qml/TicketSettingsDialog.qml, reguły w
apps/common/qml/card/assignment.js).

Sonda offscreen na migawce z dziennika usługi (`fold` + `derived`: plan zatwierdzony, #1 nieruszony, #2 już ruszył)
i katalogu wykonawców z dwoma dostawcami (Anthropic, OpenAI), dwoma lokalnymi i jednym niedostępnym:
- obecne wartości są wybrane; wyszarzenia z powodami: ten sam model co autor (ten sam model z inną siłą myślenia
  wolno), inny zakres lokalny/chmura, klasa mocny z recenzentem tego samego dostawcy (standard — wolno),
  wykonawca niedostępny z powodem z usługi,
- zmiana wykonawcy albo klasy zachowuje recenzenta tylko wtedy, gdy nadal jest dozwolony; inaczej wybór jest pusty,
  „Zapisz” nieaktywne i okno mówi dlaczego,
- „Zapisz” wysyła dokładnie jedno `EditCardPlan` z `action: edit` i niezmienioną kartką; błąd usługi zostaje w oknie,
  sukces zamyka je z potwierdzeniem; Esc i „Anuluj” niczego nie wysyłają,
- ticket, który już ruszył, nie ma zapisu EditCardPlan (jego pola sprawdza test_ticket_settings_running, PT-335).
"""
import json
import shutil
import os
import unittest

from .decisionfix import full_card
from .test_decision_dialog import STATE_PY as DIALOG_STATE_PY
from .test_view import ProbeCase

BODY = "# Zadanie\n\nZrób rzecz.\n\n- [ ] Rzecz jest zrobiona.\n\n## Dla recenzenta\nSprawdź rzecz.\n"
BODY_STARTED = "# Zadanie 2\n\n- [ ] Druga rzecz jest zrobiona.\n"

EXECUTORS = [
    {"name": "Opus", "program": "claude", "model": "opus", "provider": "anthropic", "local": False},
    {"name": "Sonnet", "program": "claude", "model": "sonnet", "provider": "anthropic", "local": False},
    {"name": "Codex standard", "program": "codex", "model": "gpt-6.1-sol", "provider": "openai", "local": False,
     "effort": "medium"},
    {"name": "Codex mocny", "program": "codex", "model": "gpt-6.1-sol", "provider": "openai", "local": False,
     "effort": "high"},
    {"name": "Lokalny", "program": "codex", "model": "qwen3-coder:30b", "provider": "local", "local": True},
    {"name": "Lokalny dwa", "program": "codex", "model": "llama4:70b", "provider": "local", "local": True},
    {"name": "Codex Astra", "program": "codex", "model": "gpt-6-astra", "provider": "openai", "local": False,
     "effort": "high", "available": False, "error": "limit Codexa do 16:30"},
]
REASON_SAME = "ten sam model co autor"
REASON_SCOPE = "inny zakres: lokalny i chmura"
REASON_STRONG = "przy klasie mocny recenzent innej rodziny modeli"
REASON_DOWN = "limit Codexa do 16:30"


def journal_settings() -> list[dict]:
    """Plan zatwierdzony: #1 nieruszony (Opus → Sonnet, standard), #2 już ruszył (Sonnet → Opus)."""
    items = []

    def add(event_type, ts, **data):
        items.append({"v": 1, "seq": len(items) + 1, "ts": f"2026-11-03T{ts}+01:00", "card": "K-8",
                      "type": event_type, **data})

    rows = [{"id": "#1", "workId": "#1", "title": "Okno ustawień", "executor": "Opus", "reviewer": "Sonnet",
             "class": "standard", "blockers": [], "body": BODY},
            {"id": "#2", "workId": "#2", "title": "Drugi ticket", "executor": "Sonnet", "reviewer": "Opus",
             "class": "lekki", "blockers": [], "body": BODY_STARTED}]
    add("card.started", "09:00:00", entry="planning", executor="Opus", program="claude", provider="anthropic")
    add("plan.proposed", "09:10:00", planner="Opus", rows=[{k: v for k, v in r.items() if k != "id"} for r in rows])
    add("plan.reviewed", "09:20:00", reviewer="Sonnet", verdict="APPROVE", findings=[], corrected=False)
    add("plan.approved", "09:30:00", map={r["id"]: r["id"] for r in rows}, rows=rows)
    add("step.started", "09:40:00", step="K-8-ticket-2-0", ticket="#2", kind="ticket", executor="Sonnet",
        provider="anthropic", model="sonnet", attempt=0, parentStep="", stepDir="/wt/K-8-ticket-2-0.step",
        worktree="/wt/#2", branch="ticket/2")
    return items


STATE_PY = DIALOG_STATE_PY + r'''
def dlg():
    engine.rootContext().setContextProperty("probeDlg", obj("ticketSettingsDialog"))
    return json.loads(js("(function () { const d = probeDlg; const all = " + ALL + "; "
        "const one = n => all(d, n)[0] || null; "
        "const y = it => it.mapToItem(d, 0, 0).y; "
        "return JSON.stringify({ open: d.visible, ticket: d.ticketId, "
        "  title: one('ticketSettingsTitle') ? one('ticketSettingsTitle').text : null, "
        "  started: one('ticketSettingsStarted') ? one('ticketSettingsStarted').text : null, "
        "  options: all(d, 'ticketSettingsOption').sort((a, b) => y(a) - y(b)).map(o => [o.groupKey, o.value, o.checked, "
        "    o.disabled, o.disabled ? all(o, 'ticketSettingsReason')[0].text : '']), "
        "  save: one('ticketSettingsSave') ? one('ticketSettingsSave').enabled : null, "
        "  why: one('ticketSettingsWhy') ? one('ticketSettingsWhy').text : '', "
        "  error: one('ticketSettingsError') ? one('ticketSettingsError').text : '' }) })()"))

def group(name):
    return {o[1]: o for o in dlg()["options"] if o[0] == name}

def picked(name):
    return [o[1] for o in dlg()["options"] if o[0] == name and o[2]]

def option_point(name, value):
    engine.rootContext().setContextProperty("probeDlg", obj("ticketSettingsDialog"))
    return at("(function () { const all = " + ALL + "; "
              "return all(probeDlg, 'ticketSettingsOption').find(o => o.groupKey === '" + name + "' && o.value === '" + value + "') })()")

def pick(name, value):
    click(option_point(name, value))

def tiles():
    return json.loads(js("(function () { const v = page.cardFullView; const all = " + ALL + "; "
        "return JSON.stringify(all(v, 'fullTicket').map(t => [t.ticketId, all(t, 'fullTicketSettings').length])) })()"))

def open_settings(ticket):
    engine.rootContext().setContextProperty("probeTile", None)
    click(at("(function () { const v = page.cardFullView; const all = " + ALL + "; "
             "const tile = all(v, 'fullTicket').find(t => t.ticketId === '" + ticket + "'); "
             "return all(tile, 'fullTicketSettings')[0] })()"))

def edits():
    return [c for c in calls() if c[0] == "EditCardPlan"]
'''


def snapshot(data: dict) -> dict:
    data = full_card(data, journal_settings())
    data["models"] = {"checked": True, "default": "local", "agentProgram": "codex", "endpoints": {}, "roles": {},
                      "executors": EXECUTORS}
    return data


@unittest.skipUnless(shutil.which("qml6") or os.path.exists("/usr/lib/qt6/bin/qml"), "brak Qt Quick")
class TicketSettingsTest(ProbeCase):
    def probe_settings(self, scenario: str) -> dict:
        return self.probe(STATE_PY + "\njs(\"page.openFull('K-8')\")\nwait(200)\n" + scenario, snapshot(self.data))

    def test_every_tile_has_the_button_and_unstarted_ticket_shows_current_values(self):
        out = self.probe_settings("""
out["tiles"] = tiles()
open_settings("#1")
out["dlg"] = dlg()
out["calls"] = calls()
""")
        self.assertEqual(out["tiles"], [["#1", 1], ["#2", 1]])
        dlg = out["dlg"]
        self.assertTrue(dlg["open"])
        self.assertEqual(dlg["ticket"], "#1")
        self.assertIn("Okno ustawień", dlg["title"])
        self.assertIsNone(dlg["started"])
        selected = [(o[0], o[1]) for o in dlg["options"] if o[2]]
        self.assertEqual(sorted(selected), [("class", "standard"), ("executor", "Opus"), ("reviewer", "Sonnet")])
        for name in ("executor", "reviewer"):
            self.assertEqual([o[1] for o in dlg["options"] if o[0] == name], [e["name"] for e in EXECUTORS],
                             "lista ma wszystkie pozycje katalogu")
        self.assertEqual([o[1] for o in dlg["options"] if o[0] == "class"], ["lekki", "standard", "mocny", "lokalny"])
        self.assertTrue(dlg["save"], dlg)
        self.assertEqual(out["calls"], [])

    def test_reviewer_rules_grey_out_with_reasons(self):
        out = self.probe_settings("""
open_settings("#1")
out["reviewers"] = group("reviewer")
out["executors"] = group("executor")
out["classes"] = group("class")
""")
        reviewers = {name: [row[3], row[4]] for name, row in out["reviewers"].items()}
        self.assertEqual(reviewers["Opus"], [True, REASON_SAME])
        self.assertEqual(reviewers["Sonnet"], [False, ""])
        self.assertEqual(reviewers["Codex standard"], [False, ""])
        self.assertEqual(reviewers["Lokalny"], [True, REASON_SCOPE])
        self.assertEqual(reviewers["Lokalny dwa"], [True, REASON_SCOPE])
        self.assertEqual(reviewers["Codex Astra"], [True, REASON_DOWN], "powód niedostępności pochodzi z usługi")
        self.assertEqual(out["executors"]["Codex Astra"][3:], [True, REASON_DOWN])
        self.assertFalse(out["executors"]["Lokalny"][3], "wykonawca lokalny jest wybieralny przy klasie standard")
        self.assertEqual(out["classes"]["lokalny"][3:], [True, REASON_SCOPE])
        self.assertFalse(out["classes"]["mocny"][3])

    def test_same_model_with_other_effort_is_allowed_but_strong_class_blocks_same_provider(self):
        out = self.probe_settings("""
open_settings("#1")
pick("executor", "Codex standard")
out["afterExecutor"] = [picked("executor"), picked("reviewer"), dlg()["save"]]
out["reviewers"] = {k: v[3:] for k, v in group("reviewer").items()}
pick("reviewer", "Codex mocny")
out["effortReviewer"] = [picked("reviewer"), dlg()["save"]]
pick("class", "mocny")
out["strong"] = {"reviewers": {k: v[3:] for k, v in group("reviewer").items()}, "picked": picked("reviewer"),
                 "save": dlg()["save"], "why": dlg()["why"]}
pick("class", "standard")
out["back"] = {"picked": picked("reviewer"), "save": dlg()["save"]}
""")
        self.assertEqual(out["afterExecutor"], [["Codex standard"], ["Sonnet"], True],
                         "recenzent zostaje, bo nadal jest dozwolony")
        self.assertEqual(out["reviewers"]["Codex standard"], [True, REASON_SAME])
        self.assertEqual(out["reviewers"]["Codex mocny"], [False, ""], "ten sam model, inna siła myślenia")
        self.assertEqual(out["effortReviewer"], [["Codex mocny"], True])
        strong = out["strong"]
        self.assertEqual(strong["reviewers"]["Codex mocny"], [True, REASON_STRONG])
        self.assertEqual(strong["reviewers"]["Codex Astra"], [True, REASON_DOWN])
        self.assertEqual(strong["reviewers"]["Sonnet"], [False, ""])
        self.assertEqual(strong["reviewers"]["Opus"], [False, ""], "przy mocnym wolno recenzent innej rodziny")
        self.assertEqual(strong["picked"], [], "recenzent z tej samej rodziny przestał być dozwolony")
        self.assertFalse(strong["save"])
        self.assertIn("recenzenta", strong["why"])
        self.assertEqual(out["back"], {"picked": [], "save": False}, "pusty wybór nie wraca sam")

    def test_executor_change_clears_a_reviewer_that_is_no_longer_allowed(self):
        out = self.probe_settings("""
open_settings("#1")
pick("executor", "Sonnet")
out["empty"] = [picked("executor"), picked("reviewer"), dlg()["save"], dlg()["why"]]
pick("reviewer", "Opus")
out["chosen"] = [picked("reviewer"), dlg()["save"], dlg()["why"]]
""")
        self.assertEqual(out["empty"][:3], [["Sonnet"], [], False])
        self.assertTrue(out["empty"][3])
        self.assertEqual(out["chosen"], [["Opus"], True, ""])

    def test_local_class_and_local_cloud_scope(self):
        out = self.probe_settings("""
open_settings("#1")
pick("executor", "Lokalny")
out["afterLocal"] = [picked("reviewer"), dlg()["save"], group("class")["lokalny"][3:]]
out["reviewers"] = {k: v[3:] for k, v in group("reviewer").items()}
pick("reviewer", "Lokalny dwa")
pick("class", "lokalny")
out["localClass"] = [picked("class"), dlg()["save"], {k: v[3:] for k, v in group("executor").items()}]
""")
        self.assertEqual(out["afterLocal"], [[], False, [False, ""]], "lokalny wykonawca: chmurowy recenzent odpada")
        self.assertEqual(out["reviewers"]["Opus"], [True, REASON_SCOPE])
        self.assertEqual(out["reviewers"]["Lokalny"], [True, REASON_SAME])
        self.assertEqual(out["reviewers"]["Lokalny dwa"], [False, ""])
        local_class, save, executors = out["localClass"]
        self.assertEqual((local_class, save), (["lokalny"], True))
        self.assertEqual(executors["Opus"], [True, REASON_SCOPE], "klasa lokalny z wykonawcą spoza lokalnych")
        self.assertEqual(executors["Lokalny dwa"], [False, ""])

    def test_save_sends_one_edit_with_unchanged_card_and_success_closes_with_confirmation(self):
        out = self.probe_settings("""
open_settings("#1")
pick("reviewer", "Codex standard")
pick("class", "mocny")
click(named("ticketSettingsSave"))
wait(150)
out["calls"] = calls()
out["dlg"] = dlg()
out["toast"] = js("page.toast")
""")
        sent = [c for c in out["calls"] if c[0] == "EditCardPlan"]
        self.assertEqual(len(sent), 1, out["calls"])
        self.assertEqual(sent[0][1], "K-8")
        self.assertEqual(json.loads(sent[0][2]), {"action": "edit", "ticket": "#1", "executor": "Opus",
                                                  "reviewer": "Codex standard", "class": "mocny", "body": BODY})
        self.assertFalse(out["dlg"]["open"])
        self.assertIn("#1", out["toast"])
        self.assertIn("zapisane", out["toast"])

    def test_save_without_changes_sends_the_same_values_and_card(self):
        out = self.probe_settings("""
open_settings("#1")
click(named("ticketSettingsSave"))
wait(150)
out["calls"] = calls()
""")
        sent = [c for c in out["calls"] if c[0] == "EditCardPlan"]
        self.assertEqual(len(sent), 1)
        self.assertEqual(json.loads(sent[0][2]), {"action": "edit", "ticket": "#1", "executor": "Opus",
                                                  "reviewer": "Sonnet", "class": "standard", "body": BODY})

    def test_service_error_stays_in_the_window_with_its_message(self):
        message = "ticket już ruszył — nie można zmienić kartki ani przydziału"
        out = self.probe_settings("""
replies.append({"ok": False, "error": "ticket-started", "message": %s})
open_settings("#1")
pick("reviewer", "Codex standard")
click(named("ticketSettingsSave"))
wait(150)
out["dlg"] = dlg()
out["edits"] = len(edits())
""" % json.dumps(message))
        self.assertTrue(out["dlg"]["open"])
        self.assertEqual(out["dlg"]["error"], message)
        self.assertTrue(out["dlg"]["save"], "po błędzie można ponowić")
        self.assertEqual(out["edits"], 1)

    def test_escape_and_cancel_send_nothing(self):
        out = self.probe_settings("""
open_settings("#1")
pick("reviewer", "Codex standard")
key(Qt.Key.Key_Escape)
out["afterEsc"] = [dlg()["open"], js("page.fullOpen")]
open_settings("#1")
out["reopened"] = picked("reviewer")
click(named("ticketSettingsCancel"))
out["afterCancel"] = [dlg()["open"], js("page.fullOpen")]
out["edits"] = edits()
""")
        self.assertEqual(out["afterEsc"], [False, True], "Esc zamyka okno, nie pełną kartę")
        self.assertEqual(out["reopened"], ["Sonnet"], "porzucony wybór nie zostaje")
        self.assertEqual(out["afterCancel"], [False, True])
        self.assertEqual(out["edits"], [])

    def test_started_ticket_has_no_plan_edit(self):
        """Ticket, który ruszył, nie ma zapisu EditCardPlan ani wyboru klasy; jego pola (autor, recenzent przez
        ResolveCardStep) sprawdza test_ticket_settings_running (PT-335)."""
        out = self.probe_settings("""
open_settings("#2")
out["dlg"] = dlg()
click(named("ticketSettingsCancel"))
out["edits"] = edits()
""")
        dlg = out["dlg"]
        self.assertTrue(dlg["open"])
        self.assertIsNone(dlg["save"])
        self.assertEqual(sorted({o[0] for o in dlg["options"]}), ["author", "reviewer"])
        self.assertEqual(out["edits"], [])


if __name__ == "__main__":
    unittest.main()
