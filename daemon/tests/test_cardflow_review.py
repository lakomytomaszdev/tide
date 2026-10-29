"""PT-59: review ticketu, poprawki, limit rund i sprzątanie po zatwierdzeniu."""
import json
import os

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git


class ReviewFlowTest(FlowInProcess):
    def test_reviewer_changes_to_ticket_are_rejected_before_merge(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: review-modifies")
        self.assertTrue(self.start(card)["ok"])
        shown = self.wait_column(card, "waiting")
        self.assertIn("recenzent zmienił", shown["derived"]["note"])
        self.assertEqual(self.of(card, "review.verdict"), [])
        self.assertEqual(self.results(card, "merge"), [])

    def test_approved_review_receives_full_context_and_cleans_worktrees(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: gotowe")
        reply = self.start(card, reviewer="Opus")
        self.assertTrue(reply["ok"], reply)
        shown = self.wait_column(card, "review")
        steps = self.of(card, "step.started")
        self.assertEqual([s["kind"] for s in steps], ["ticket", "review"])
        self.assertEqual((steps[0]["executor"], steps[1]["executor"]), ("Sonnet", "Opus"))
        self.assertNotEqual(steps[0]["model"], steps[1]["model"])
        with open(os.path.join(steps[1]["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            call = json.load(fh)
        prompt = call["prompt"]
        for phrase in ("Dodaj eksport.", "Dla recenzenta:", "Diff ticketu", "diff --git",
                       "Notka wyniku wykonawcy:", "Atrapa zrobiła ticket.", "Zasady projektu:",
                       "CLAUDE.md", "Plik wyniku:"):
            self.assertIn(phrase, prompt)
        verdict = self.of(card, "review.verdict")
        self.assertEqual([(v["verdict"], v["blockers"], v["majors"], v["nits"]) for v in verdict],
                         [("APPROVE", 0, 0, 0)])
        self.assertEqual(shown["derived"]["flow"]["verdict"], "APPROVE")
        self.assertEqual(len(self.results(card, "merge")), 1)
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        self.assertFalse(os.path.exists(reply["worktree"]), "worktree ticketu po zatwierdzeniu")
        self.assertFalse(os.path.exists(steps[1]["worktree"]), "worktree review po zatwierdzeniu")

    def test_two_request_changes_rounds_then_approve(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: review-requests 2")
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "review", timeout=50)
        verdicts = self.of(card, "review.verdict")
        self.assertEqual([(v["round"], v["verdict"], v["majors"]) for v in verdicts],
                         [(1, "REQUEST_CHANGES", 1), (2, "REQUEST_CHANGES", 1), (3, "APPROVE", 0)])
        self.assertEqual([s["kind"] for s in self.of(card, "step.started")],
                         ["ticket", "review", "fix", "review", "fix", "review"])
        fixes = [s for s in self.of(card, "step.started") if s["kind"] == "fix"]
        with open(os.path.join(fixes[0]["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            prompt = json.load(fh)["prompt"]
        self.assertIn("MAJOR: popraw ATRAPA.md", prompt)
        self.assertEqual(len(self.results(card, "merge")), 1)

    def test_three_failed_reviews_wait_for_user_without_merging(self):
        card = self.card(description="Dodaj eksport.\n\nATRAPA: review-requests 3")
        self.assertTrue(self.start(card)["ok"])
        shown = self.wait_column(card, "waiting", timeout=50)
        self.assertIn("trzy rundy review", shown["derived"]["note"])
        verdicts = self.of(card, "review.verdict")
        self.assertEqual([v["verdict"] for v in verdicts], ["REQUEST_CHANGES"] * 3)
        self.assertEqual(self.results(card, "merge"), [])
        count = len(self.events(card))
        wait_until(lambda: False, timeout=0.5)
        self.assertEqual(len(self.events(card)), count)

    def test_explicit_reviewer_and_same_model_validation(self):
        card = self.card()
        bad = self.start(card, reviewer="Sonnet")
        self.assertEqual((bad["ok"], bad["error"]), (False, "bad-reviewer"), bad)
        self.assertEqual(self.events(card), [])
        good = self.start(card, reviewer="Haiku")
        self.assertTrue(good["ok"], good)
        self.wait_column(card, "review")
        review = next(s for s in self.of(card, "step.started") if s["kind"] == "review")
        self.assertEqual(review["executor"], "Haiku")
        with open(os.path.join(review["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            self.assertIn("--model=haiku", json.load(fh)["argv"])

    def test_mixed_provider_rejects_reviewer_from_authors_provider(self):
        card = self.card()
        reply = self.start(card, preferredProvider="mixed", reviewer="Opus")
        self.assertEqual((reply["ok"], reply["error"]), (False, "bad-reviewer-provider"), reply)
        self.assertEqual(self.events(card), [])
