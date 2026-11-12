"""Stan „Zatwierdzanie…” przycisków zatwierdzenia planu (PT-408, K-102) w prawdziwym QML.

Źródła stanu: oczekiwanie okna na odpowiedź ApproveCardPlan / ApproveCardEnvironment oraz `flow.plan.approving`
z migawki (przeładowanie panelu go nie gubi). Różnica planu zależy wyłącznie od oczekiwania okna. Backend Tablicy
i Sesji trzyma zatwierdzenia w grupie długich wywołań (limit jak START_TIMEOUT_MS) i nie obiecuje „nic nie zapisano”.
"""
import json
import os
import unittest

from apps.board import backend as board_backend
from apps.sessions import backend as sessions_backend

from .environmentfix import environment, plan_card
from .test_view import FIND_PY, ProbeCase

ROW = {"id": "PT-1", "workId": "#1", "title": "Eksport", "class": "standard", "executor": "Sonnet",
       "reviewer": "Opus", "blockers": [], "editable": True, "body": "- [ ] Plik ma 3 kolumny."}
APPROVING = "Zatwierdzanie…"
PLAN_TEXT = "Zatwierdź plan i rozpocznij implementację"

HELPERS = r'''
def set_plan(**changes):
    snap = json.loads(backend._snapshot)
    plan = next(c for c in snap["cards"] if c["id"] == "K-6")["derived"]["flow"]["plan"]
    for key in [k for k, v in changes.items() if v is None]:
        plan.pop(key, None)
    plan.update({k: v for k, v in changes.items() if v is not None})
    set_snapshot(snap)

def state(name):
    item = obj(name)
    return {"text": item.property("text"), "enabled": item.property("enabled"), "visible": item.property("visible")}

def notice():
    return js("page.cardPanel.notice")

def approve_calls():
    return [c for c in calls() if c[0] in ("ApproveCardPlan", "ApproveCardEnvironment")]
'''


def pending_plan(data, **plan):
    """K-6 z planem do zatwierdzenia, bez sekcji środowiska."""
    data = json.loads(json.dumps(data))
    for card in data["cards"]:
        if card["id"] == "K-6":
            card["derived"] = {"column": "waiting", "note": "plan do zatwierdzenia", "flow": {
                "entry": "plan", "plan": dict({"status": "pending", "waves": [["PT-1"]], "rows": [ROW],
                                               "errors": []}, **plan)}}
            card["boardColumn"] = "waiting"
    return data


class ApprovingButtonsTest(ProbeCase):
    def scenario(self, body, data=None):
        return self.probe(FIND_PY + HELPERS + body, data=data or pending_plan(self.data))

    def test_snapshot_approving_disables_the_panel_button_and_survives_reloads(self):
        out = self.scenario("""
click(card("K-6"))
out["idle"] = state("cardPlanApprove")
set_plan(approving=True, approvalError="")
out["approving"] = state("cardPlanApprove")
set_plan(approving=True, approvalError="", note="nowa migawka")   # przeładowanie danych karty
out["reloaded"] = state("cardPlanApprove")
click(at(FIND + "(page.cardPanel, 'cardPlanApprove')"))         # kliknięcie w trakcie nie wysyła nic
out["calls"] = calls()
""")
        self.assertEqual(out["idle"], {"text": PLAN_TEXT, "enabled": True, "visible": True})
        self.assertEqual(out["approving"]["text"], APPROVING)
        self.assertFalse(out["approving"]["enabled"])
        self.assertEqual(out["reloaded"]["text"], APPROVING)
        self.assertFalse(out["reloaded"]["enabled"], "nowa migawka z approving nie przywraca przycisku")
        self.assertEqual(out["calls"], [])

    def test_manager_button_follows_the_same_state(self):
        out = self.scenario("""
click(card("K-6"))
click(at(FIND + "(page.cardPanel, 'cardOpenPlanManager')"))
out["idle"] = state("planManagerApprove")
set_plan(approving=True)
out["approving"] = state("planManagerApprove")
set_plan(approving=True, note="nowa migawka")
out["reloaded"] = state("planManagerApprove")
""")
        self.assertEqual(out["idle"]["text"], PLAN_TEXT)
        self.assertTrue(out["idle"]["enabled"])
        for key in ("approving", "reloaded"):
            self.assertEqual(out[key]["text"], APPROVING, key)
            self.assertFalse(out[key]["enabled"], key)

    def test_environment_button_is_disabled_while_the_plan_is_approving(self):
        model = environment()
        out = self.probe(FIND_PY + HELPERS + """
ENV = lambda n: js("(function () { const it = " + FIND + "(page.cardPanel, '" + n + "'); "
                   "return it ? JSON.stringify({text: it.text, enabled: it.enabled}) : '' })()")
click(card("K-6"))
out["idle"] = json.loads(ENV("envApprove"))
backend.hold.add("ApproveCardEnvironment")
js(FIND + "(page.cardPanel, 'envApprove').clicked()")
wait(80)
out["waiting"] = json.loads(ENV("envApprove"))
out["waitingOnlyPlan"] = json.loads(ENV("envOnlyPlan"))
js(FIND + "(page.cardPanel, 'envApprove').clicked()")           # drugie kliknięcie w trakcie oczekiwania
js(FIND + "(page.cardPanel, 'envOnlyPlan').clicked()")
wait(80)
out["during"] = approve_calls()
backend.release(0, {"ok": True, "id": "K-6", "message": "Środowisko zatwierdzone."})
wait(80)
out["released"] = json.loads(ENV("envApprove"))
set_plan(approving=True)                                       # migawka mówi „trwa” — bez oczekiwania okna
out["snapshot"] = json.loads(ENV("envApprove"))
""", data=plan_card(self.data, model))
        self.assertEqual(out["idle"], {"text": "Zatwierdź plan i środowisko", "enabled": True})
        self.assertEqual(out["waiting"], {"text": APPROVING, "enabled": False})
        self.assertFalse(out["waitingOnlyPlan"]["enabled"])
        self.assertEqual([c[0] for c in out["during"]], ["ApproveCardEnvironment"], "jedno wywołanie, bez ApproveCardPlan")
        self.assertEqual(out["released"]["text"], "Zatwierdź plan i środowisko")
        self.assertTrue(out["released"]["enabled"])
        self.assertEqual(out["snapshot"], {"text": APPROVING, "enabled": False})

    def test_two_quick_clicks_send_one_approve_call(self):
        out = self.scenario("""
click(card("K-6"))
backend.hold.add("ApproveCardPlan")
target = at(FIND + "(page.cardPanel, 'cardPlanApprove')")
click(target)
click(target)
out["calls"] = approve_calls()
out["waiting"] = state("cardPlanApprove")
js(FIND + "(page.cardPanel, 'cardPlanApprove').clicked()")      // także wywołanie wprost nie wychodzi drugi raz
wait(60)
out["afterDirect"] = approve_calls()
""".replace("//", "#"))
        self.assertEqual(out["calls"], [["ApproveCardPlan", "K-6"]])
        self.assertEqual(out["waiting"]["text"], APPROVING)
        self.assertEqual(out["afterDirect"], [["ApproveCardPlan", "K-6"]])

    def test_plan_approved_reply_ends_the_wait_with_a_notice(self):
        out = self.scenario("""
click(card("K-6"))
backend.hold.add("ApproveCardPlan")
click(at(FIND + "(page.cardPanel, 'cardPlanApprove')"))
out["waiting"] = state("cardPlanApprove")
backend.release(0, {"ok": True, "id": "K-6", "approved": True})
wait(80)
out["notice"] = notice()
out["busy"] = js("page.cardPanel.busy")
""")
        self.assertEqual(out["waiting"]["text"], APPROVING)
        self.assertIn("Plan zatwierdzony", out["notice"])
        self.assertFalse(out["busy"])

    def test_approving_reply_is_an_info_notice_not_an_error(self):
        out = self.scenario("""
click(card("K-6"))
backend.hold.add("ApproveCardPlan")
click(at(FIND + "(page.cardPanel, 'cardPlanApprove')"))
backend.release(0, {"ok": True, "approving": True})
wait(80)
out["notice"] = notice()
out["errorText"] = js("page.cardPanel.error")
out["button"] = state("cardPlanApprove")           # migawki jeszcze nie ma — okno trzyma stan „trwa”
set_plan(approving=True)
out["snapshot"] = state("cardPlanApprove")
""")
        self.assertEqual(out["notice"], "Zatwierdzanie trwa — karta ruszy sama.")
        self.assertEqual(out["errorText"], "")
        self.assertEqual(out["button"]["text"], APPROVING)
        self.assertEqual(out["snapshot"]["text"], APPROVING)

    def test_error_reply_restores_the_button_and_shows_the_error(self):
        out = self.scenario("""
click(card("K-6"))
backend.hold.add("ApproveCardPlan")
click(at(FIND + "(page.cardPanel, 'cardPlanApprove')"))
backend.release(0, {"ok": False, "error": "x", "message": "Rezerwacja numeru nie powiodła się."})
wait(80)
out["button"] = state("cardPlanApprove")
out["errorText"] = js("page.cardPanel.error")
""")
        self.assertEqual(out["button"], {"text": PLAN_TEXT, "enabled": True, "visible": True})
        self.assertIn("Rezerwacja numeru", out["errorText"])

    def test_approval_error_in_the_snapshot_shows_the_reason_and_keeps_the_button_active(self):
        reason = "Zatwierdzanie wstrzymane: czeka decyzja o bazie karty"
        out = self.scenario("""
click(card("K-6"))
set_plan(approving=False, approvalError=%s)
out["button"] = state("cardPlanApprove")
out["shown"] = state("cardPlanApprovalError")
click(at(FIND + "(page.cardPanel, 'cardOpenPlanManager')"))
out["manager"] = state("planManagerApprove")
out["managerShown"] = obj("planManagerError").property("text")
""" % json.dumps(reason))
        self.assertEqual(out["button"], {"text": PLAN_TEXT, "enabled": True, "visible": True})
        self.assertEqual(out["shown"]["text"], reason)
        self.assertTrue(out["shown"]["visible"])
        self.assertNotEqual(out["manager"]["text"], APPROVING)
        self.assertEqual(out["managerShown"], reason)

    def test_plan_difference_waits_only_for_the_reply_not_for_plan_approved(self):
        diff = {"revision": {"kind": "append", "pending": True, "difference": {"added": ["#2"], "changed": [], "removed": []}}}
        for reply in ({"ok": True, "id": "K-6"}, {"ok": False, "error": "x", "message": "Różnica nieaktualna."},
                      {"ok": True, "approving": True}):
            with self.subTest(reply=reply):
                out = self.scenario("""
click(card("K-6"))
backend.hold.add("ApproveCardPlan")
out["idle"] = state("cardPlanApprove")
click(at(FIND + "(page.cardPanel, 'cardPlanApprove')"))
out["waiting"] = state("cardPlanApprove")
out["calls"] = approve_calls()
click(at(FIND + "(page.cardPanel, 'cardOpenPlanManager')"))
out["managerWaiting"] = state("planManagerApprove")
backend.release(0, json.loads(%s))
wait(80)
out["managerReleased"] = state("planManagerApprove")
js(FIND + "(page, 'planManager').cardRequested()")
wait(120)
out["released"] = state("cardPlanApprove")
""" % json.dumps(json.dumps(reply)), data=pending_plan(self.data, **diff))
                self.assertEqual(out["idle"]["text"], "Zatwierdź różnicę")
                self.assertEqual(out["waiting"]["text"], APPROVING)
                self.assertFalse(out["waiting"]["enabled"])
                self.assertEqual(out["calls"], [["ApproveCardPlan", "K-6"]])
                self.assertEqual(out["managerWaiting"]["text"], APPROVING)
                self.assertEqual(out["released"]["text"], "Zatwierdź różnicę")
                self.assertTrue(out["released"]["enabled"], "migawka bez approving i bez plan.approved")
                self.assertTrue(out["managerReleased"]["enabled"])

    def test_other_actions_keep_their_rules_while_the_approval_is_pending(self):
        out = self.scenario("""
click(card("K-6"))
set_plan(approving=True)
out["return"] = obj("cardPlanReturn").property("enabled")
out["busy"] = js("page.cardPanel.busy")
""")
        self.assertFalse(out["busy"], "stan zatwierdzania nie zapala panel.busy na stałe")


class LongCallGroupTest(unittest.TestCase):
    """Zatwierdzenia planu i środowiska idą grupą długich wywołań (limit jak START_TIMEOUT_MS)."""

    def board(self):
        backend = board_backend.Backend(None)
        backend.bus = object()
        seen = {}
        backend._call = lambda member, args, done, timeout=15_000, **kw: seen.update({member: timeout})
        return backend, seen

    def test_board_backend_uses_the_long_limit_for_both_approvals(self):
        backend, seen = self.board()
        for member in ("ApproveCardPlan", "ApproveCardEnvironment"):
            backend._write("tag", member, ["K-1"])
        backend._write("tag", "UpdateCard", ["K-1", "{}"])
        self.assertEqual(seen["ApproveCardPlan"], board_backend.START_TIMEOUT_MS)
        self.assertEqual(seen["ApproveCardEnvironment"], board_backend.START_TIMEOUT_MS)
        self.assertEqual(seen["UpdateCard"], 15_000, "pozostałe zapisy zostają przy krótkim limicie")

    def test_sessions_backend_uses_the_long_limit_for_the_plan_approval(self):
        backend = sessions_backend.Backend(None)
        backend.bus = object()
        seen = {}
        backend._call = lambda path, iface, member, args, done, timeout=15_000: seen.update({member: timeout})
        backend._write("tag", "ApproveCardPlan", ["K-1"])
        backend._write("tag", "EditCardPlan", ["K-1", "{}"])
        self.assertEqual(seen["ApproveCardPlan"], sessions_backend.START_TIMEOUT_MS)
        self.assertEqual(seen["EditCardPlan"], 15_000)
        self.assertEqual(sessions_backend.START_TIMEOUT_MS, board_backend.START_TIMEOUT_MS)

    def test_missing_answer_to_an_approval_does_not_claim_that_nothing_was_saved(self):
        for module, call in ((board_backend, lambda b, got: setattr(b, "_call", lambda m, a, done, **k: done(False, None))),
                             (sessions_backend, lambda b, got: setattr(b, "_call", lambda p, i, m, a, done, **k: done(False, None)))):
            backend = module.Backend(None)
            backend.bus = object()
            got = []
            backend.replied.connect(lambda tag, text: got.append(json.loads(text)))
            call(backend, got)
            backend._write("t", "ApproveCardPlan", ["K-1"])
            self.assertEqual(got[0]["ok"], False)
            self.assertNotIn("nic nie zapisano", got[0]["message"])
            self.assertIn("zatwierdzanie mogło ruszyć", got[0]["message"])
            got.clear()
            backend._write("t", "UpdateCard", ["K-1", "{}"])
            self.assertIn("nic nie zapisano", got[0]["message"], "pozostałe zapisy bez zmian")


if __name__ == "__main__":
    unittest.main()
