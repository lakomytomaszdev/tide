"""PT-312: bramka na granicy uruchomienia review, niezależnie od drogi autora poprawek."""
import unittest
import tempfile
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from . import test_cardflow_fix_escalation as fixtures
from .test_cardflow_fix_escalation import _FixJournal, CARD, TICKET, EXECUTORS
from tide_daemon.cardflow import decisions, execution, reviewgate
from tide_daemon.cardflow.engine import FlowDeferred
from tide_daemon.cardflow.state import derived
from tide_daemon.models import executors


class _Captured(Exception):
    pass


class ReviewGateTest(_FixJournal):
    fix_step = fixtures.ResumeFixTest.fix_step

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        engine = self.engine
        engine._process_check = lambda *args: engine.journal.state(CARD)
        engine.journal.append = lambda card, event, **fields: self.add(event, **fields)
        engine._project = lambda card: (SimpleNamespace(id=CARD, title="T", description="D"),
                                       SimpleNamespace(repo="/r"))
        engine.git = SimpleNamespace(clean_tip=lambda *args: "abc", review_diff=lambda *args: "")
        engine._rules = lambda repo: ""
        engine._trial_check = lambda *args: None
        engine._issue_evidence = lambda *args, **kw: None
        # Rzeczywisty _launch: przechwytujemy dopiero rezerwację procesu, po bramce.
        del engine._launch
        self.admitted = []

        def capture(card, selected, ticket, kind, step_id):
            self.admitted.append((selected, kind))
            raise _Captured()

        engine._admit = capture

    def selected(self, name):
        return execution.resolve(name, self.engine.catalog(), which=self.engine.which)

    def gate(self, name, policy="other-model", round_no=4):
        return self.engine._guard_review_launch(CARD, self.engine.journal.state(CARD), TICKET,
                                                round_no, self.selected(name), policy=policy)

    def history(self, fixer="Opus"):
        self.card("Opus", "Codex Astra", klass="mocny", preferred="mixed")
        self.step("r-3", "review", "Codex Astra", 3)
        fixed = self.fix_step("f-3", fixer, 3, executorReason="override")
        for step in list(self.engine.journal.state(CARD).steps.values()):
            self.add("step.finished", step=step["step"], ticket=TICKET, result="done")
        return fixed

    def test_poisoned_automatic_limit_history_is_checked_at_actual_review_launch(self):
        fixed = self.history()
        # Dziennik pozostawiony przez transfer sprzed poprawki rundy 4.
        self.add("step.decision", step="f-3", ticket=TICKET, choice="transfer",
                 executor="Codex standard", automatic=True)
        self.reassign_reviewer("Opus")
        with self.assertRaises(_Captured):
            self.engine._review(CARD, self.engine.journal.state(CARD), fixed, 4)
        selected, kind = self.admitted[-1]
        self.assertEqual(kind, "review")
        self.assertNotEqual(selected.model, fixed["model"])
        self.assertEqual(selected.name, "Codex mocny")

    def test_manual_fixer_is_checked_even_when_another_executor_finished_last(self):
        self.card("Sonnet", "Codex Astra", klass="mocny", preferred="mixed")
        self.step("r-3", "review", "Codex Astra", 3)
        self.fix_step("failed-fix", "Opus", 3, executorReason="override")
        self.add("step.finished", step="failed-fix", result="error", reason="limit")
        fixed = self.fix_step("last-fix", "Sonnet", 3, executorReason="override")
        self.reassign_reviewer("Opus")
        with self.assertRaises(_Captured):
            self.engine._review(CARD, self.engine.journal.state(CARD), fixed, 4)
        self.assertEqual(self.admitted[-1][0].name, "Codex mocny")

    def test_free_allows_same_model_but_always_requests_a_new_session(self):
        self.history()
        selected, session = self.gate("Opus", "free")
        self.assertEqual((selected.name, session), ("Opus", ""))

    def test_other_model_preserves_different_effort_semantics(self):
        self.card("Codex standard", "Opus", klass="mocny")
        selected, session = self.gate("Codex mocny")
        self.assertEqual((selected.name, session), ("Codex mocny", ""))

    def test_family_comes_from_provider_and_program_not_executor_name(self):
        self.history()
        self.assertTrue(reviewgate.permits(self.selected("Sonnet"),
                                          reviewgate.authors(self.engine.journal.state(CARD), TICKET, 4),
                                          "other-model"))
        self.assertEqual(self.gate("Sonnet", "other-family")[0].name, "Codex mocny")
        renamed = replace(self.selected("Opus"), name="Inna nazwa")
        self.assertFalse(reviewgate.permits(renamed,
                                           reviewgate.authors(self.engine.journal.state(CARD), TICKET, 4),
                                           "other-family"))

    def test_authors_window_survives_review_retry_and_excludes_older_fixes(self):
        self.card("Sonnet", "Codex Astra", klass="standard")
        self.step("old-fix", "fix", "Haiku", 2)
        self.step("r-3", "review", "Codex Astra", 3)
        self.fix_step("f-3", "Opus", 3)
        self.step("other-ticket", "fix", "Haiku", 3)["ticket"] = "PT-2"
        self.step("r-4", "review", "Codex Astra", 4)
        self.step("r-4-retry", "review", "Codex Astra", 4)
        authored = reviewgate.authors(self.engine.journal.state(CARD), TICKET, 4)
        self.assertEqual([s["step"] for s in authored], ["s-1", "f-3"])

    def test_resume_review_passes_through_gate_with_the_same_authors(self):
        self.history()
        step = self.fix_step("review-retry", "Opus", 4)
        step["kind"] = "review"
        with self.assertRaises(_Captured):
            self.engine._resume_step(CARD, self.engine.journal.state(CARD), step, {"choice": "retry"})
        self.assertEqual(self.admitted[-1][0].name, "Codex mocny")

    def test_launch_discards_author_session_even_with_free_policy(self):
        self.history()
        real_gate = self.engine._guard_review_launch
        engine = self.engine
        engine._admit = lambda *args: None
        engine._box_place = lambda *args: None
        engine._step_base_context = lambda *args: ("", {})
        engine._ancestor_outs = lambda *args: []
        engine.store = SimpleNamespace(load=lambda: SimpleNamespace(projects={}))
        engine._hidden = (set(), ())
        engine.git.untracked = lambda *args: []
        recorded = []

        def capture_start(*args, **fields):
            recorded.append(fields)
            raise _Captured()

        engine._act = capture_start
        item, project = self.engine._project(CARD)
        item.projects = []
        project.allowed_commands, project.denied_commands = [], []
        with patch.object(engine, "_guard_review_launch", side_effect=lambda *args: real_gate(*args, policy="free")):
            for name in ("Opus", "Codex Astra"):
                with self.subTest(executor=name), self.assertRaises(_Captured):
                    engine._launch(item, project, self.selected(name), TICKET, {"branch": "card/k-1"},
                                   {"branch": "ticket/pt-1", "worktree": self.tmp.name + "/work"},
                                   "new-review", [], kind="review", round_no=4, prompt_text="Review",
                                   parent_step="old-review", resume_session="sess-f-3")
                session = recorded[-1]["session"]
                self.assertNotEqual(session, "sess-f-3")
                self.assertEqual(bool(session), name == "Opus")

    def test_console_review_also_passes_through_the_gate(self):
        self.history()
        item, project = self.engine._project(CARD)
        with patch.object(self.engine, "_guard_review_launch", side_effect=_Captured) as gate:
            with self.assertRaises(_Captured):
                self.engine._launch_full_planning(item, project, self.selected("Opus"), {},
                                                  "console-review", [], [], "", kind="review",
                                                  ticket=TICKET, round_no=4)
        gate.assert_called_once()

    def test_no_candidate_asks_once_and_selection_unblocks_without_affecting_round_threshold(self):
        fixed = self.history()
        self.engine.catalog = lambda: executors.Catalog((EXECUTORS["Opus"],), {})
        for _ in range(2):
            with self.assertRaises(FlowDeferred):
                self.gate("Opus")
        self.assertEqual(len([e for e in self.events if e["type"] == "review.question"]), 1)
        st = self.engine.journal.state(CARD)
        prompt = decisions.build_decisions(st)[0]
        self.assertEqual(prompt["options"][0]["params"],
                         {"choice": "reassign-reviewer", "ticket": TICKET, "executor": ""})
        self.assertNotIn("accept", [o["choice"] for o in prompt["options"]])
        self.assertEqual(prompt["reply"]["field"], "executor")
        self.assertEqual(derived(st, {"column": "progress"})["column"], "waiting")
        # Katalog wraca; rzeczywista akcja panelu wybiera zgodnego recenzenta.
        self.engine.catalog = executors.default_catalog
        self.engine._require_base_context = lambda *args: None
        self.engine.resolve_step(CARD, {**prompt["reply"]["params"], "executor": "Codex Astra"})
        with self.assertRaises(_Captured):
            self.engine._review(CARD, self.engine.journal.state(CARD), fixed, 4)
        self.assertEqual(self.admitted[-1][0].name, "Codex Astra")
        self.step("r-4", "review", "Codex Astra", 4)
        st = self.engine.journal.state(CARD)
        self.assertNotIn((TICKET, 4), st.review_questions)
        self.assertNotIn((TICKET, 4), st.review_decisions)
        self.add("review.question", ticket=TICKET, round=4, question="Próg rund")
        self.assertIn("accept", [o["choice"] for o in decisions.build_decisions(
            self.engine.journal.state(CARD))[0]["options"]])

    def test_first_review_can_ask_without_a_previous_verdict(self):
        self.card("Opus", "Opus", klass="mocny")
        self.add("step.finished", step="s-1", ticket=TICKET, result="done")
        self.engine.catalog = lambda: executors.Catalog((EXECUTORS["Opus"],), {})
        st = self.engine.journal.state(CARD)
        with self.assertRaises(FlowDeferred):
            self.engine._review(CARD, st, st.steps["s-1"], 1)
        st = self.engine.journal.state(CARD)
        self.assertEqual(st.review_questions[(TICKET, 1)]["reason"], "independence")
        self.assertFalse(st.verdicts)
        self.assertEqual(derived(st, {"column": "progress"})["column"], "waiting")

    def test_local_review_does_not_fall_back_to_cloud(self):
        self.history()
        local = replace(self.selected("Opus"), local=True)
        with self.assertRaises(FlowDeferred):
            self.engine._guard_review_launch(CARD, self.engine.journal.state(CARD), TICKET, 4, local)


if __name__ == "__main__":
    unittest.main()
