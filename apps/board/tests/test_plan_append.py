"""Formularz dopisania i niezależne zatwierdzenie różnicy w prawdziwym QML."""
import json

from . import test_view as fixture


class AppendPlanViewTest(fixture.ProbeCase):
    def test_form_validation_save_review_and_separate_approval(self):
        data = json.loads(json.dumps(self.data))
        data["models"] = dict(fixture.models_status(), executors=fixture.EXECUTORS)
        for card in data["cards"]:
            if card["id"] == "K-6":
                card["derived"] = {"column": "progress", "flow": {"entry": "planning", "plan": {
                    "status": "approved", "fingerprint": "approved-plan-1", "canAppend": True,
                    "planner": "Sonnet", "waves": [["PT-1"]], "rows": [
                        {"id": "PT-1", "workId": "#1", "title": "Dotychczasowy", "class": "standard",
                         "executor": "Sonnet", "reviewer": "Opus", "blockers": [], "editable": False,
                         "body": "- [ ] Istniejące kryterium."}]}}}
                card["boardColumn"] = "progress"
        out = self.probe(fixture.FIND_PY + """
click(card("K-6"))
click(at(FIND + "(page.cardPanel, 'cardOpenPlanManager')"))
click(at(FIND + "(page, 'planManagerAddTicket')"))
click(at(FIND + "(page, 'planManagerSubmitAppend')"))
out["invalidTitle"] = obj("planManagerError").property("text")
out["callsBeforeValid"] = calls()
obj("planManagerAppendTitle").setProperty("text", "Nowy eksport")
obj("planManagerBody").setProperty("text", "- [ ] Plik ma 3 kolumny.")
obj("planManagerExecutor").setProperty("currentIndex", 0)
wait(50)
obj("planManagerReviewer").setProperty("currentIndex", 0)
obj("planManagerAppendBlockers").setProperty("text", "PT-999")
click(at(FIND + "(page, 'planManagerSubmitAppend')"))
out["invalidBlocker"] = obj("planManagerError").property("text")
obj("planManagerAppendBlockers").setProperty("text", "PT-1")
click(at(FIND + "(page, 'planManagerSubmitAppend')"))
wait(50)
out["save"] = calls()[-1]
click(at(FIND + "(page, 'planManagerSubmitAppend')"))
wait(50)
out["retry"] = calls()[-1]
snap = json.loads(backend._snapshot)
plan = next(c for c in snap["cards"] if c["id"] == "K-6")["derived"]["flow"]["plan"]
plan.update(status="reviewing", canAppend=False, revision={"kind": "append", "pending": True,
            "difference": {"added": ["#2"], "changed": [], "removed": []}})
plan["rows"].append({"workId": "#2", "title": "Nowy eksport", "class": "standard", "executor": "Sonnet",
                      "reviewer": "Opus", "body": "- [ ] Plik ma 3 kolumny.", "blockers": ["PT-1"]})
set_snapshot(snap)
wait(80)
out["approveDuringReview"] = obj("planManagerApprove").property("visible")
out["difference"] = obj("planManagerAppendDifference").property("text")
plan.update(status="pending", review={"verdict": "APPROVE", "reviewer": "Opus", "findings": []})
set_snapshot(snap)
wait(80)
out["approveLabel"] = obj("planManagerApprove").property("text")
out["discardVisible"] = obj("planManagerDiscardAppend").property("visible")
out["callsBeforeApproval"] = calls()
click(at(FIND + "(page, 'planManagerApprove')"))
out["approval"] = calls()[-1]
""", data=data)
        self.assertIn("Wpisz tytuł", out["invalidTitle"])
        self.assertFalse(out["callsBeforeValid"])
        self.assertIn("istniejącego planu", out["invalidBlocker"])
        self.assertEqual(out["save"][:2], ["EditCardPlan", "K-6"])
        request = json.loads(out["save"][2])
        self.assertEqual(request["action"], "append")
        self.assertEqual(request["planFingerprint"], "approved-plan-1")
        self.assertEqual(request["blockers"], ["PT-1"])
        self.assertEqual(request["title"], "Nowy eksport")
        self.assertTrue(request["requestKey"])
        self.assertNotIn("ticket", request)
        self.assertEqual(out["retry"], out["save"])
        self.assertFalse(out["approveDuringReview"])
        self.assertIn("#2", out["difference"])
        self.assertIn("Zatwierdź różnicę", out["approveLabel"])
        self.assertTrue(out["discardVisible"])
        self.assertFalse(any(c[0] == "ApproveCardPlan" for c in out["callsBeforeApproval"]))
        self.assertEqual(out["approval"], ["ApproveCardPlan", "K-6"])
