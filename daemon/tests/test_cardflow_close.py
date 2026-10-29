"""PT-70: konflikt, podsumowanie oraz jawne scalenie karty do gałęzi głównej."""
import os
import json

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_plan_integration import PlanFlowTest
from .test_cardflow_waves import WaveFlowTest
from .test_cardflow_dbus import git


class CardClosingTest(FlowInProcess):
    proposal = PlanFlowTest.proposal
    submit = PlanFlowTest.submit
    approve = PlanFlowTest.approve
    wave_plan = WaveFlowTest.wave_plan

    def test_manual_summary_then_explicit_main_merge(self):
        card = self.card()
        self.assertTrue(self.start(card, check="manual")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        summary = self.of(card, "card.closed")[-1]["summary"]
        self.assertIn("Co przetestować ręcznie i jak", summary)
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        self.assertEqual(self.shown(card)["boardColumn"], "review")
        difference = self.flow.engine.card_diff(card)
        self.assertTrue(difference["ok"])
        self.assertIn("ATRAPA.md", difference["diff"])
        box = []
        self.flow.merge_card_to_main(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        merged = json.loads(box[0])
        self.assertTrue(merged["ok"], merged)
        self.assertNotEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        self.assertTrue(wait_until(lambda: self.shown(card)["boardColumn"] == "done", timeout=5))
        self.assertEqual(len(self.of(card, "card.main-merged")), 1)

    def test_auto_check_closes_without_manual_list_and_leaves_main_untouched(self):
        card = self.card()
        self.assertTrue(self.start(card, check="auto")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        self.assertNotIn("Co przetestować ręcznie i jak", self.of(card, "card.closed")[-1]["summary"])
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        self.assertEqual(self.shown(card)["boardColumn"], "review")

    def test_main_merge_refuses_dirty_checkout_without_losing_changes(self):
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        source = os.path.join(self.repo, "CLAUDE.md")
        with open(source, "a", encoding="utf-8") as fh:
            fh.write("\nMoja niezapisana zmiana.\n")
        box = []
        self.flow.merge_card_to_main(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertFalse(json.loads(box[0])["ok"])
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        with open(source, encoding="utf-8") as fh:
            self.assertIn("Moja niezapisana zmiana", fh.read())

    def test_conflict_is_resolved_in_card_worktree_before_next_ticket(self):
        card = self.card(description="Plan z konfliktem")
        path = self.wave_plan(card)
        for number, mode in ((1, "gotowe"), (2, "powoli 2")):
            with open(os.path.join(os.path.dirname(path), "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\nATRAPA: {mode}\nATRAPA-PLIK: same\n"
                         "\n- [ ] Wynik.\n")
        self.assertTrue(self.submit(card, path)["ok"])
        approved = self.approve(card)
        self.assertTrue(approved["ok"], approved)
        self.assertTrue(wait_until(lambda: any(s.get("kind") == "conflict" for s in self.of(card, "step.started")), timeout=20))
        conflicts = [s for s in self.of(card, "step.started") if s.get("kind") == "conflict"]
        self.assertEqual(len(conflicts), 1, self.events(card))
        self.assertTrue(wait_until(lambda: any(e.get("step") == conflicts[0]["step"] for e in self.of(card, "step.finished")), timeout=15))
        finished = next(e for e in self.of(card, "step.finished") if e["step"] == conflicts[0]["step"])
        if finished["result"] != "done":
            with open(os.path.join(conflicts[0]["stepDir"], "out", "step.log"), encoding="utf-8") as fh:
                self.fail(f"krok konfliktu: {finished['reason']}\n{fh.read()[-4000:]}")
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=35), self.events(card))
        self.assertEqual(conflicts[0]["worktree"], next(e["worktree"] for e in self.results(card, "card-branch")))
        resolved = [r for r in self.results(card, "merge") if r.get("resolved")]
        self.assertEqual(len(resolved), 1, self.events(card))
        self.assertTrue(resolved[0]["ok"])
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)

    def test_failed_conflict_waits_for_user_without_retry_loop(self):
        card = self.card(description="ATRAPA: conflict-error")
        path = self.wave_plan(card)
        for number, mode in ((1, "gotowe"), (2, "powoli 2")):
            with open(os.path.join(os.path.dirname(path), "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\nATRAPA: {mode}\nATRAPA-PLIK: same\n"
                         "\n- [ ] Wynik.\n")
        self.assertTrue(self.submit(card, path)["ok"])
        self.assertTrue(self.approve(card)["ok"])
        self.assertTrue(wait_until(lambda: any(s.get("kind") == "conflict" for s in self.of(card, "step.started")),
                                   timeout=25), self.events(card))
        self.assertTrue(wait_until(lambda: self.shown(card)["derived"]["flow"].get("action") == "error",
                                   timeout=15), self.events(card))
        self.assertEqual(len([s for s in self.of(card, "step.started") if s.get("kind") == "conflict"]), 1)
        self.assertFalse(self.of(card, "card.closed"))
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)

    def test_return_from_direct_card_adds_new_ticket_on_same_card_branch(self):
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "card.closed"), timeout=30), self.events(card))
        branch = self.results(card, "card-branch")[0]["branch"]
        box = []
        self.flow.edit_plan(card, json.dumps({"action": "return", "feedback": "Dodaj ticket z poprawką"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.revision-ready"), timeout=20), self.events(card))
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.reviewed")) >= 2, timeout=20), self.events(card))
        approved = self.approve(card)
        self.assertTrue(approved["ok"], approved)
        self.assertTrue(wait_until(lambda: len(self.of(card, "card.closed")) == 2, timeout=30), self.events(card))
        self.assertEqual(self.results(card, "card-branch")[0]["branch"], branch)
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
