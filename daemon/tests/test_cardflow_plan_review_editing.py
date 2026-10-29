"""PT-80/63: bramka review i zdarzenia zmian planu."""
import json
import os
from . import test_cardflow_plan_integration as fixtures
from .test_cardflow import FlowInProcess
from .privatebus import wait_until


class PlanReviewEditingTest(FlowInProcess):
    proposal = fixtures.PlanFlowTest.proposal
    submit = fixtures.PlanFlowTest.submit
    approve = fixtures.PlanFlowTest.approve

    def test_review_precedes_approval_and_survives_restart(self):
        card = self.card()
        path = self.proposal(card)
        with open(os.path.join(os.path.dirname(path), "spec.md"), "w", encoding="utf-8") as fh:
            fh.write("Pełna specyfikacja: eksport uwzględnia polskie znaki.")
        self.assertTrue(self.submit(card, path)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
        review = next(s for s in self.of(card, "step.started") if s["kind"] == "plan-review")
        self.assertNotEqual(review["executor"], "Sonnet")
        with open(os.path.join(review["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            prompt = json.load(fh)["prompt"]
        for expected in ("eksport uwzględnia polskie znaki", "mierzalność", "pionowość", "Dla recenzenta", "jednym świeżym kontekście"):
            self.assertIn(expected, prompt)
        shown = self.wait_column(card, "waiting")["derived"]["flow"]["plan"]
        self.assertEqual(shown["review"]["verdict"], "APPROVE")
        self.restart()
        wait_until(lambda: False, timeout=.3)
        self.assertEqual(len(self.of(card, "plan.reviewed")), 1)

    def test_edit_is_journalled_and_replayed(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        self.flow.stop()
        result = self.flow.engine.edit_plan(card, {"action": "edit", "ticket": "#2", "class": "mocny",
                                                  "executor": "Opus", "reviewer": "Sonnet",
                                                  "body": "# Poprawiona kartka\n- [ ] Nowe kryterium.\n"})
        self.assertTrue(result["ok"])
        self.flow.engine.edit_plan(card, {"action": "edit", "ticket": "#2", "executor": "Opus"})
        self.restart()
        rows = self.shown(card)["derived"]["flow"]["plan"]["rows"]
        self.assertEqual(rows[1]["executor"], "Opus")
        self.assertIn("Nowe kryterium", rows[1]["body"])
        self.assertEqual(len(self.of(card, "plan.ticket-edited")), 1)

    def test_started_ticket_cannot_be_edited(self):
        from tide_daemon.cardflow.engine import FlowError
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        self.flow.stop()
        self.flow.engine.approve_plan(card)
        self.flow.engine.journal.append(card, "step.started", step="working", ticket="PT-1", kind="ticket")
        with self.assertRaises(FlowError) as error:
            self.flow.engine.edit_plan(card, {"action": "edit", "ticket": "PT-1", "executor": "Opus"})
        self.assertEqual(error.exception.code, "ticket-started")

    def test_one_correction_after_review_then_plan_is_visible(self):
        card = self.card(description="ATRAPA: plan-review-changes")
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
        self.assertEqual([s["kind"] for s in self.of(card, "step.started")], ["plan-review", "plan-revise"])
        self.assertTrue(self.of(card, "plan.reviewed")[0]["corrected"])
        self.restart()
        wait_until(lambda: False, timeout=.3)
        self.assertEqual(len(self.of(card, "plan.reviewed")), 1)

    def test_returned_plan_has_service_difference(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "Doprecyzuj ryzyka."}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.revision-ready"), timeout=20), self.events(card))
        difference = self.of(card, "plan.revision-ready")[-1]["difference"]
        self.assertEqual(difference["added"], [])
        self.assertEqual(difference["changed"][0], {"ticket": "#1", "fields": ["body"]})

    def test_pending_review_cannot_be_approved(self):
        from tide_daemon.cardflow.engine import FlowError
        self.flow.stop()
        card = self.card()
        self.flow.engine.propose_plan(card, self.proposal(card))
        with self.assertRaises(FlowError) as error:
            self.flow.engine.approve_plan(card)
        self.assertEqual(error.exception.code, "review-pending")
        self.assertEqual(self.results(card, "reserve"), [])

    def test_editing_unstarted_approved_ticket_does_not_stop_work(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.flow.stop()
        approved = self.flow.engine.approve_plan(card)
        ident = approved["map"]["#2"]
        self.flow.engine.edit_plan(card, {"action": "edit", "ticket": ident, "executor": "Opus", "reviewer": "Sonnet"})
        self.assertEqual(self.of(card, "plan.ticket-edited")[-1]["ticket"], ident)
        self.assertFalse(any(e.get("result") == "stopped" for e in self.of(card, "step.finished")))
        self.restart()
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=30), self.events(card))
        second = next(s for s in self.of(card, "step.started") if s.get("ticket") == ident and s["kind"] == "ticket")
        self.assertEqual(second["executor"], "Opus")

    def test_approving_difference_does_not_stop_running_ticket(self):
        card = self.card()
        path = self.proposal(card)
        ticket_path = os.path.join(os.path.dirname(path), "tickets", "1.md")
        with open(ticket_path, encoding="utf-8") as fh:
            body = fh.read()
        with open(ticket_path, "w", encoding="utf-8") as fh:
            fh.write(body.replace("ATRAPA: gotowe", "ATRAPA: powoli 8"))
        self.assertTrue(self.submit(card, path)["ok"])
        approved = self.approve(card)
        self.assertTrue(approved["ok"], approved)
        self.assertTrue(wait_until(lambda: any(s["kind"] == "ticket" for s in self.of(card, "step.started")), timeout=10))
        working = next(s for s in self.of(card, "step.started") if s["kind"] == "ticket")
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "Doprecyzuj drugi ticket."}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=6), self.events(card))
        self.assertTrue(self.approve(card)["ok"])
        self.assertFalse(any(e["step"] == working["step"] for e in self.of(card, "step.finished")), self.events(card))
        self.assertEqual(len(self.of(card, "plan.revision-approved")), 1)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=25), self.events(card))

    def test_enter_opens_planner_in_console(self):
        from .test_cardflow_enter_unit import _Terminal
        order = []
        terminal = _Terminal(order)
        self.flow.engine.terminal = terminal
        card = self.card(description="ATRAPA: planowanie")
        self.assertTrue(self.start(card, entry="planning", mode="light")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "enter"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        entered = self.of(card, "step.entered")[-1]
        source = next(s for s in self.of(card, "step.started") if s["step"] == entered["step"])
        self.assertEqual(source["executor"], "Sonnet")
        self.assertEqual(order, ["open"])
        self.assertIn("--resume", terminal.plan.argv)

    def test_added_ticket_gets_number_only_when_difference_is_approved(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "Dodaj ticket"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) == 2, timeout=20), self.events(card))
        self.assertEqual(self.of(card, "plan.revision-ready")[-1]["difference"]["added"], ["#3"])
        self.assertEqual(self.results(card, "reserve"), [])
        approved = self.approve(card)
        self.assertTrue(approved["ok"], approved)
        self.assertEqual(len(approved["map"]), 3)
