"""D-Bus: zapis formularza nie jest zatwierdzeniem różnicy."""
import json
import os

from .privatebus import wait_until
from .test_cardflow_dbus import FlowCase


class AppendPlanDbusTest(FlowCase):
    def test_save_review_and_explicit_approval(self):
        card = self.card()
        folder = os.path.join(self.repo, "docs", "agents", "plans")
        os.makedirs(os.path.join(folder, "tickets"))
        path = os.path.join(folder, "plan.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"---\ntide-plan: 1\ncard: {card}\ntracker: local\nticket-prefix: PT\n---\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                     "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                     "| #1 | Dotychczasowy | todo | | Sonnet | | standard | Opus | |\n")
        with open(os.path.join(folder, "tickets", "1.md"), "w", encoding="utf-8") as fh:
            fh.write("ATRAPA: pytanie\n- [ ] Działa.\n## Dla recenzenta\nSprawdź wynik.\n")
        self.assertTrue(self.call("StartCard", card, json.dumps({"entry": "plan", "planPath": path}))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        self.assertTrue(self.call("ApproveCardPlan", card)["ok"])
        self.assertTrue(wait_until(lambda: any(s.get("ticket") == "PT-1" and s["result"] == "question"
                                             for s in self.of(card, "step.finished")), timeout=20))
        plan = self.shown(card)["derived"]["flow"]["plan"]
        self.assertTrue(plan["canAppend"])
        req = {"action": "append", "requestKey": "dbus-append-1", "planFingerprint": plan["fingerprint"],
               "title": "Dopisany", "body": "ATRAPA: gotowe\n- [ ] Powstaje plik.\n## Dla recenzenta\nSprawdź plik.",
               "class": "mocny", "executor": "Codex mocny", "reviewer": "Codex standard", "blockers": []}
        reply = self.call("EditCardPlan", card, json.dumps(req))
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(self.call("EditCardPlan", card, json.dumps(req))["already"])
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=20), self.events(card))
        self.assertTrue(wait_until(lambda: self.shown(card)["derived"]["flow"]["plan"]["status"] == "pending",
                                   timeout=10))
        plan = self.shown(card)["derived"]["flow"]["plan"]
        self.assertEqual(plan["status"], "pending")
        self.assertEqual(plan["revision"]["difference"], {"added": ["#2"], "removed": [], "changed": []})
        self.assertEqual(len(self.results(card, "reserve")), 1)
        self.assertFalse(any(s.get("ticket") == "PT-2" for s in self.of(card, "step.started")))
        self.assertFalse(any(s["kind"] in ("planning", "plan-amend", "plan-revise") for s in self.of(card, "step.started")))
        self.assertTrue(self.call("ApproveCardPlan", card)["ok"])
        self.assertTrue(wait_until(lambda: any(s.get("ticket") == "PT-2" for s in self.of(card, "step.started")), timeout=20),
                        self.events(card))
        self.assertTrue(self.call("ApproveCardPlan", card)["already"])
        self.assertTrue(self.call("EditCardPlan", card, json.dumps(req))["already"])
        self.assertEqual(len(self.results(card, "reserve")), 2)
        self.assertEqual(len(self.of(card, "plan.revision-approved")), 1)
