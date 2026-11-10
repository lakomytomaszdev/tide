"""PT-312: bramka na granicy uruchomienia review, niezależnie od drogi autora poprawek."""
import os
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
        # Testy PT-312 sprawdzają dotychczasową regułę: projekt z polityką `other-model` (PT-330).
        self.project = SimpleNamespace(repo="/r", review_independence="other-model")
        engine._project = lambda card: (SimpleNamespace(id=CARD, title="T", description="D"), self.project)
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

    # --- PT-330: polityka projektu zamiast stałej reguły -------------------------------------------------

    def test_project_without_setting_reviews_opus_with_opus_in_a_new_session(self):
        self.project = SimpleNamespace(repo="/r")
        fixed = self.history()
        self.reassign_reviewer("Opus")
        with self.assertRaises(_Captured):
            self.engine._review(CARD, self.engine.journal.state(CARD), fixed, 4)
        selected, kind = self.admitted[-1]
        self.assertEqual((selected.name, kind), ("Opus", "review"))
        self.assertFalse([e for e in self.events if e["type"] == "review.question"])

    def test_policy_is_read_from_the_project_at_every_launch_without_restart(self):
        self.history()
        st = self.engine.journal.state(CARD)
        self.project.review_independence = "free"
        self.assertEqual(self.engine._guard_review_launch(CARD, st, TICKET, 4, self.selected("Opus"))[0].name,
                         "Opus")
        self.project.review_independence = "other-model"
        self.assertNotEqual(self.engine._guard_review_launch(CARD, st, TICKET, 4,
                                                             self.selected("Opus"))[0].model, "opus")

    def test_unknown_value_falls_back_to_free_with_a_log_warning(self):
        project = SimpleNamespace(slug="proj", path="/b/projects/proj.md", review_independence="strict")
        with self.assertLogs("tide_daemon.cardflow.reviewgate", level="WARNING") as logs:
            self.assertEqual(reviewgate.project_policy(project, "mocny"), "free")
        self.assertIn("strict", logs.output[0])
        self.assertEqual(reviewgate.project_policy(None), "free")
        self.assertEqual(reviewgate.project_policy(SimpleNamespace(review_independence="")), "free")

    def test_other_family_class_list_limits_the_policy_to_listed_classes(self):
        project = SimpleNamespace(review_independence="other-family", review_independence_classes=["mocny"])
        self.assertEqual(reviewgate.project_policy(project, "mocny"), "other-family")
        self.assertEqual(reviewgate.project_policy(project, "standard"), "free")
        self.assertEqual(reviewgate.project_policy(project, None), "other-family")  # review planu
        self.project = project
        self.card("Sonnet", "Opus", klass="standard")
        self.add("step.finished", step="s-1", ticket=TICKET, result="done")
        self.assertEqual(self.engine._guard_review_launch(CARD, self.engine.journal.state(CARD), TICKET, 1,
                                                          self.selected("Sonnet"))[0].name, "Sonnet")

    def test_other_family_without_candidate_gives_reason_and_owner_choices(self):
        self.project = SimpleNamespace(repo="/r", review_independence="other-family")
        self.card("Opus", "Sonnet", klass="mocny")
        self.add("step.finished", step="s-1", ticket=TICKET, result="done")
        self.engine.catalog = lambda: executors.Catalog(tuple(e for e in EXECUTORS.values()
                                                              if e.provider == "anthropic"), {})
        with self.assertRaises(FlowDeferred):
            self.gate("Sonnet", policy=None, round_no=1)
        st = self.engine.journal.state(CARD)
        question = st.review_questions[(TICKET, 1)]
        self.assertEqual((question["reason"], question["policy"]), ("independence", "other-family"))
        for fragment in ("innej rodziny", "reviewIndependence: other-family", "„Sonnet”",
                         "Wybierz innego recenzenta", "poczekaj", "poluzuj politykę"):
            self.assertIn(fragment, question["question"])
        self.assertEqual(derived(st, {"column": "progress"})["column"], "waiting")
        # Właściciel poluzował politykę: następny przebieg rusza review bez restartu usługi.
        self.project.review_independence = "free"
        self.assertEqual(self.gate("Sonnet", policy=None, round_no=1)[0].name, "Sonnet")
        self.assertEqual(self.engine.journal.state(CARD).review_decisions[(TICKET, 1)]["choice"], "review-ready")

    def test_other_family_accepts_codex_for_an_opus_author(self):
        self.project = SimpleNamespace(repo="/r", review_independence="other-family")
        self.card("Opus", "Codex Astra", klass="mocny")
        st = self.engine.journal.state(CARD)
        self.assertEqual(self.gate("Codex Astra", policy=None, round_no=1)[0].name, "Codex Astra")
        self.assertEqual(self.gate("Sonnet", policy=None, round_no=1)[0].provider, "openai")
        self.assertFalse(st.review_questions)

    # --- PT-330 (review PT-312, MAJOR-2): wejście do Konsoli to nowe uruchomienie review ---------------------

    def console_review_entry(self, policy):
        """Historyczne review rundy 4 pisane przez Opusa, który napisał oceniane poprawki, zakończone błędem;
        użytkownik wchodzi do niego w Konsoli publiczną ścieżką `enter`. Rzeczywiste `_launch_full_planning`."""
        from tide_daemon.cardflow import engine as engine_mod
        self.project.review_independence = policy
        self.project.name, self.project.allowed_commands, self.project.denied_commands = "proj", [], []
        self.history()
        step_dir = self.tmp.name + "/r-4.step"
        os.makedirs(step_dir + "/in")
        with open(step_dir + "/in/prompt.txt", "w", encoding="utf-8") as fh:
            fh.write(f"Oceń poprawki. Wynik: {step_dir}/out")
        self.step("r-4", "review", "Opus", 4, stepDir=step_dir, session="sess-r-4", worktree=self.tmp.name,
                  branch="ticket/pt-1", reviewTip="abc")
        self.add("step.finished", step="r-4", ticket=TICKET, result="error", reason="crash")
        opened = self.console_stubs("console-review")
        with patch.object(engine_mod.agentcmd, "resolve_git_dir", return_value=""), \
                patch.object(engine_mod.agentcmd, "resume_interactive",
                             side_effect=AssertionError("review w Konsoli nie wznawia starej sesji")):
            reply = self.engine.enter(CARD, "r-4")
        started = self.engine.journal.state(CARD).steps["console-review"]
        return reply, started, opened

    def console_stubs(self, next_step):
        """Atrapy otoczenia rzeczywistego `_launch_full_planning` (Konsola, pliki zadań, profil odczytu)."""
        engine = self.engine
        item = SimpleNamespace(id=CARD, title="T", description="D", projects=[])
        engine._project = lambda card: (item, self.project)
        engine._process_state = lambda card: engine.journal.state(card)
        engine._next_step_id = lambda *args: next_step
        engine._step_base_context = lambda *args: ("", {})
        engine._configured_reads = lambda *args, **kw: []
        engine._used_read_profile = lambda *args: False
        engine.store = SimpleNamespace(load=lambda: SimpleNamespace(projects={}))
        engine.task_files = SimpleNamespace(write=lambda payload: "task-file", cancel=lambda path: True)
        opened = []
        engine.terminal = SimpleNamespace(available=lambda: True,
                                          open=lambda plan: opened.append(plan) or "tab",
                                          launchers=SimpleNamespace(agents={}, codex_for=lambda source: "codex"))
        engine.journal.lock = __import__("threading").RLock()
        engine.systemd_run = "/usr/bin/systemd-run"
        engine.clock = lambda: 1.0
        return opened

    def test_console_fix_transfer_keeps_the_manual_reason_through_error_and_retry(self):
        # Review PT-312, MAJOR-3: klasa standard, autor Sonnet, recenzent Astra; poprawki rundy 3 w Konsoli,
        # ręczne przekazanie innemu wykonawcy, błąd, ponowienie — eskalacja nie nadpisuje ręcznego wyboru.
        from tide_daemon.cardflow import engine as engine_mod
        self.project.name, self.project.allowed_commands, self.project.denied_commands = "proj", [], []
        self.card("Sonnet", "Codex Astra", klass="standard", preferred="anthropic")
        for round_no in (1, 2, 3):
            self.step(f"r-{round_no}", "review", "Codex Astra", round_no)
        fix = self.fix_step("f-3", "Opus", 3, executorReason="escalated", mode="interactive",
                            worktree=self.tmp.name, branch="ticket/pt-1")
        self.add("step.finished", step="f-3", ticket=TICKET, result="error", reason="crash")
        self.console_stubs("console-fix")
        with open(fix["stepDir"] + "/in/prompt.txt", "a", encoding="utf-8") as fh:
            fh.write("\nWynik: " + fix["stepDir"] + "/out")
        with patch.object(engine_mod.agentcmd, "resolve_git_dir", return_value=""):
            self.engine._resume_step(CARD, self.engine.journal.state(CARD), self.engine.journal.state(CARD).steps["f-3"],
                                     {"choice": "transfer", "executor": "Codex mocny"})
        console = self.engine.journal.state(CARD).steps["console-fix"]
        self.assertEqual((console["kind"], console["executor"], console.get("executorReason")),
                         ("fix", "Codex mocny", "override"))
        self.add("step.finished", step="console-fix", ticket=TICKET, result="error", reason="crash")
        self.assertEqual(self.engine._resumed_fix_target(CARD, self.engine.journal.state(CARD), console)[:2],
                         ("Codex mocny", "override"))

    def test_console_entry_of_a_fix_keeps_its_executor_reason(self):
        from tide_daemon.cardflow import engine as engine_mod
        self.project.name, self.project.allowed_commands, self.project.denied_commands = "proj", [], []
        self.card("Sonnet", "Codex Astra", klass="standard", preferred="anthropic")
        self.step("r-3", "review", "Codex Astra", 3)
        self.fix_step("f-3", "Opus", 3, executorReason="override", worktree=self.tmp.name,
                      branch="ticket/pt-1")
        self.add("step.finished", step="f-3", ticket=TICKET, result="error", reason="crash")
        self.console_stubs("unused")
        self.engine._session = lambda step: ("0b6a9f0e-3c1d-4e8a-9b7f-2d5c6e7f8a90", "")
        self.engine._ancestor_outs = lambda *args: []
        with patch.object(engine_mod.agentcmd, "resolve_git_dir", return_value=""):
            reply = self.engine.enter(CARD, "f-3")
        entered = self.engine.journal.state(CARD).steps[reply["step"]]
        self.assertEqual((entered["kind"], entered.get("executorReason")), ("fix", "override"))

    def test_console_entry_of_a_review_runs_the_gate_for_the_fixer_as_reviewer(self):
        reply, started, opened = self.console_review_entry("other-model")
        self.assertEqual((started["kind"], started["parentStep"]), ("review", "r-4"))
        self.assertEqual(started["executor"], "Codex mocny")
        self.assertNotEqual(started["session"], "sess-r-4")
        self.assertEqual((reply["step"], reply["executor"]), ("console-review", "Codex mocny"))
        self.assertEqual(len(opened), 1)

    def test_console_entry_of_a_review_in_a_free_project_keeps_the_model_with_a_new_session(self):
        reply, started, _ = self.console_review_entry("")
        self.assertEqual((started["kind"], started["executor"]), ("review", "Opus"))
        self.assertTrue(started["session"])
        self.assertNotIn(started["session"], ("sess-r-4", "sess-f-3"))

    def test_local_review_does_not_fall_back_to_cloud(self):
        self.history()
        local = replace(self.selected("Opus"), local=True)
        with self.assertRaises(FlowDeferred):
            self.engine._guard_review_launch(CARD, self.engine.journal.state(CARD), TICKET, 4, local)


if __name__ == "__main__":
    unittest.main()
