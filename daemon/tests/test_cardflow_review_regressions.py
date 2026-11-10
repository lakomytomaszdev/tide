"""PT-260: decyzje oczekiwania, autor propozycji i przepięcie wznowionych kroków."""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tide_daemon.cardflow import baseproposal
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold
from tide_daemon.models.executors import default_catalog


class ReviewRegressionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = SimpleNamespace(root=str(self.root / "board"))
        self.journal = Journal(self.store.root, mirror=str(self.root / "mirror"))
        # Projekt bez ustawienia polityki review (PT-330: `free`); testy reguły ustawiają `other-model`.
        self.project = SimpleNamespace(repo=str(self.root), review_independence="")
        self.engine = self.new_engine()
        self.journal.append("K-1", "card.started", project="proj", entry="now", executor="Sonnet",
                            repo=str(self.root), cardBranch="card/k-1", cardWorktree=str(self.root),
                            ticket="PT-1", branch="ticket/pt-1", worktree=str(self.root))
        self.rows = [{"id": f"PT-{n}", "workId": f"#{n}", "executor": "Sonnet", "blockers": []}
                     for n in range(1, 4)]
        self.journal.append("K-1", "plan.proposed", planner="Sonnet", rows=self.rows)
        self.journal.append("K-1", "plan.approved", rows=self.rows)

    def new_engine(self):
        engine = FlowEngine(self.store, journal=self.journal, catalog=default_catalog,
                            which=lambda name: "/bin/true", tmp_root=str(self.root / "tmp"))
        engine._project = mock.Mock(return_value=(SimpleNamespace(description="Test"), self.project))
        engine._repo = mock.Mock(return_value=str(self.root))
        engine._queue_notices = mock.Mock()
        return engine

    def state(self, card="K-1"):
        return fold(self.journal.read(card))

    def step(self, name="a", ticket="PT-1", kind="ticket", card="K-1", executor="Sonnet", **extra):
        folder = self.root / name
        (folder / "in").mkdir(parents=True)
        (folder / "out").mkdir()
        (folder / "in/prompt.txt").write_text("Zrób krok. Wynik: " + str(folder / "out"))
        selected = self.engine.catalog().get(executor)
        return self.journal.append(card, "step.started", step=name, ticket=ticket, kind=kind,
                                   executor=executor, program=selected.program, model=selected.model,
                                   provider=selected.provider, effort=selected.effort, session="old-session",
                                   local=selected.provider == "local",
                                   worktree=str(self.root), branch="ticket/pt-1", stepDir=str(folder),
                                   attempt=1, **extra)

    def finish(self, step, **data):
        Path(step["stepDir"], "out/result.json").write_text(json.dumps(data))
        self.engine._finish(step["card"], step, exit_code=0)

    def test_base_proposal_has_priority_over_declared_wait_and_survives_restart(self):
        for target in ("PT-2", "K-2"):
            with self.subTest(target=target):
                name = target.lower()
                self.journal.append("K-2", "card.started", entry="now")
                step = self.step(name)
                with mock.patch.object(baseproposal, "validate", return_value={
                        "valid": True, "ref": "refs/heads/topic", "evidence": "Sprawdzona historia"}):
                    self.finish(step, result="question", question="Która baza?", waitFor=target,
                                baseProposal={"ref": "refs/heads/topic", "evidence": "Sprawdzona historia"})
                self.assertNotIn(name, self.state().step_decisions)
                self.engine = self.new_engine()
                self.assertIn(name, baseproposal.pending(self.state()))
                with mock.patch.object(self.engine, "_resume_step") as resume:
                    self.engine._advance("K-1", self.state())
                    resume.assert_not_called()

    def test_transitive_plan_and_dynamic_wait_edges_reject_cycles(self):
        a = self.step()
        for dynamic in (False, True):
            with self.subTest(dynamic=dynamic):
                rows = [dict(r) for r in self.rows]
                rows[1]["blockers"] = ["PT-3"]
                rows[2]["blockers"] = [] if dynamic else ["PT-1"]
                self.journal.append("K-1", "plan.approved", rows=rows)
                if dynamic:
                    c = self.step("c", "PT-3")
                    self.finish(c, result="question", question="Brak modułu", waitFor="PT-1")
                with self.assertRaises(FlowError):
                    self.engine._wait_ticket_target(self.state(), a, "PT-2")

    def test_wait_card_edges_are_part_of_cycle_validation(self):
        self.journal.append("K-2", "card.started", entry="now")
        self.journal.append("K-2", "plan.approved", rows=[{"id": "PT-4", "blockers": []}])
        other = self.step("other", "PT-4", card="K-2")
        self.finish(other, result="question", question="Brak pracy", waitFor="K-1")
        a = self.step()
        self.finish(a, result="question", question="Brak pracy", waitFor="K-2")
        self.assertNotIn("a", self.state().step_decisions)
        with self.assertRaises(FlowError):
            self.engine.resolve_step("K-1", {"step": "a", "choice": "wait-card", "card": "K-2"})

    def test_parallel_mutual_results_cannot_both_park(self):
        a, b = self.step(), self.step("b", "PT-2")
        barrier = threading.Barrier(2)

        def finish(step, target):
            barrier.wait(timeout=5)
            self.finish(step, result="question", question="Brak modułu", waitFor=target)

        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(finish, a, "PT-2"), pool.submit(finish, b, "PT-1")]
            for future in futures:
                future.result(timeout=10)
        waits = [d for d in self.state().step_decisions.values() if d.get("waitTicket")]
        self.assertEqual(len(waits), 1, "drugi wynik pozostaje pytaniem zamiast zamykać cykl")

    def test_imported_done_wait_target_is_rejected_after_restart(self):
        self.journal.append("K-1", "plan.imported", rows=[{"id": "PT-2", "state": "gotowe"}])
        # Ten sam zapis, który zatwierdza przejęty plan.
        self.journal.append("K-1", "plan.approved", rows=[{**r, "sourceState": "gotowe" if r["id"] == "PT-2" else "todo"}
                                                        for r in self.rows])
        a = self.step()
        self.engine = self.new_engine()
        self.assertIn("PT-2", self.state().imported_done)
        with self.assertRaises(FlowError):
            self.engine._wait_ticket_target(self.state(), a, "PT-2")
        self.finish(a, result="question", question="Brak modułu", waitFor="PT-2")
        self.assertNotIn("a", self.state().step_decisions)

    def test_restart_between_finished_and_wait_decision_recovers_once(self):
        a = self.step()
        self.journal.append("K-1", "step.finished", step="a", result="question", question="Brak modułu", waitFor="PT-2")
        self.engine = self.new_engine()
        with mock.patch.object(self.engine, "_advance_plan_review", return_value=False), \
                mock.patch.object(self.engine, "_resume_step") as resume:
            self.engine._advance("K-1", self.state())
            self.engine._advance("K-1", self.state())
            self.assertEqual(self.state().step_decisions["a"]["waitTicket"], "PT-2")
            resume.assert_not_called()
            self.journal.append("K-1", "result", action="merge", key="PT-2", ok=True)
            self.engine._advance("K-1", self.state())
            resume.assert_called_once()
            self.assertIn("git merge --no-edit", resume.call_args.args[3]["answer"])

    def test_reassigned_close_and_plan_resume_with_fresh_executor_after_restart(self):
        for kind in ("close", "plan-amend", "plan-revise"):
            for result, choice in (("question", "answer"), ("error", "retry")):
                with self.subTest(kind=kind, result=result):
                    name = kind + result
                    step = self.step(name, "", kind)
                    self.journal.append("K-1", "step.finished", step=name, result=result, question="Który wariant?")
                    self.engine.resolve_step("K-1", {"choice": "reassign-author", "ticket": "close" if kind == "close" else "plan",
                                                       "executor": "Codex standard"})
                    self.engine = self.new_engine()
                    self.engine.resolve_step("K-1", {"step": name, "choice": choice, "answer": "Pierwszy"})
                    st = self.state()
                    with mock.patch.object(self.engine, "_launch") as launch:
                        if kind == "close":
                            self.engine._advance("K-1", st)
                        else:
                            self.engine._plan_step_decision("K-1", st, step, st.finished[name])
                        launch.assert_called_once()
                        self.assertEqual(launch.call_args.args[2].name, "Codex standard")
                        self.assertEqual(launch.call_args.kwargs["resume_session"], "")
                        self.assertIn("nową sesję", launch.call_args.kwargs["prompt_text"])
                    # Kolejny podprzypadek ma nowy, pierwotny krok Sonneta.
                    self.journal.append("K-1", "step.started", step=name + "-superseded", parentStep=name, kind=kind)
                    self.journal.append("K-1", "step.finished", step=name + "-superseded", result="done")

    def test_local_plan_resume_rejects_cloud_reassignment_without_journal_write(self):
        for kind in ("plan-amend", "plan-revise"):
            for result in ("question", "error"):
                for restart in (False, True):
                    with self.subTest(kind=kind, result=result, restart=restart):
                        catalog = default_catalog()
                        local = replace(catalog.get("Lokalny"), program="codex", model="local-model")
                        catalog = replace(catalog, executors=tuple(
                            local if e.name == local.name else e for e in catalog.executors))
                        self.engine.catalog = lambda: catalog
                        name = f"{kind}-{result}-{restart}"
                        self.step(name, "", kind, executor=local.name,
                                  routeUrl="http://127.0.0.1:11434", routeKind="ollama")
                        self.journal.append("K-1", "step.finished", step=name, result=result,
                                            question="Który wariant?")
                        if restart:
                            self.engine = self.new_engine()
                            self.engine.catalog = lambda: catalog
                        before = self.journal.read("K-1")
                        with self.assertRaises(FlowError) as failure:
                            self.engine.resolve_step("K-1", {"choice": "reassign-author", "ticket": "plan",
                                                               "executor": "Codex standard"})
                        self.assertEqual(failure.exception.code, "bad-executor-provider")
                        self.assertIn("lokalny albo chmurowy zakres", str(failure.exception))
                        self.assertEqual(self.journal.read("K-1"), before)
                        self.engine = self.new_engine()
                        self.assertNotIn("plan", self.state().author_overrides)

    def test_reassigning_future_planner_does_not_change_proposal_author(self):
        # Nowa propozycja wymaga review i nie jest jeszcze zatwierdzona. Reguła innego modelu: polityka
        # projektu `other-model` (PT-330).
        self.project.review_independence = "other-model"
        self.journal.append("K-1", "plan.proposed", planner="Sonnet", rows=self.rows)
        self.journal.append("K-1", "plan.revision-ready", pending=True)
        self.engine.resolve_step("K-1", {"choice": "reassign-author", "ticket": "plan", "executor": "Codex standard"})
        self.engine = self.new_engine()
        catalog = default_catalog()
        self.engine.catalog = lambda: replace(catalog, executors=tuple(
            e for e in catalog.executors if e.name in ("Sonnet", "Codex standard")))
        with mock.patch.object(self.engine, "_plan_step") as launch:
            self.engine._advance_plan_review("K-1", self.state())
            self.assertNotEqual(launch.call_args.args[2].name, "Sonnet")
        with self.assertRaises(FlowError):
            self.engine.edit_plan("K-1", {"action": "review-executor", "executor": "Sonnet"})

    def test_selected_reviewer_is_checked_against_actual_author_at_launch(self):
        self.project.review_independence = "other-model"
        self.journal.append("K-1", "plan.proposed", planner="Sonnet", rows=self.rows)
        self.journal.append("K-1", "plan.revision-ready", pending=True)
        self.journal.append("K-1", "plan.reviewer-selected", executor="Sonnet")
        with mock.patch.object(self.engine, "_plan_step") as launch:
            with self.assertRaises(FlowError):
                self.engine._advance_plan_review("K-1", self.state())
            launch.assert_not_called()

    def test_reassigning_planner_during_active_plan_review_is_rejected(self):
        self.step("review", "", "plan-review")
        with self.assertRaises(FlowError):
            self.engine.resolve_step("K-1", {"choice": "reassign-author", "ticket": "plan", "executor": "Codex standard"})

    def test_resuming_review_still_checks_the_proposal_author(self):
        self.project.review_independence = "other-model"
        review = self.step("review", "", "plan-review")
        self.journal.append("K-1", "step.finished", step="review", result="error")
        self.engine.resolve_step("K-1", {"choice": "reassign-author", "ticket": "plan", "executor": "Codex standard"})
        self.engine = self.new_engine()
        with self.assertRaises(FlowError):
            self.engine._resume_executor("K-1", self.state(), review, "Sonnet", changed=True)

    # --- PT-330: projekt bez ustawienia (`free`) — review planu tym samym modelem, zawsze w nowej sesji ----

    def test_free_project_plan_review_by_the_planners_model_launches_a_new_step(self):
        self.journal.append("K-1", "plan.proposed", planner="Sonnet", rows=self.rows)
        self.journal.append("K-1", "plan.revision-ready", pending=True)
        self.assertTrue(self.engine.edit_plan("K-1", {"action": "review-executor", "executor": "Sonnet"})["ok"])
        with mock.patch.object(self.engine, "_plan_step") as launch:
            self.engine._advance_plan_review("K-1", self.state())
        selected, kind = launch.call_args.args[2], launch.call_args.args[3]
        self.assertEqual((selected.name, kind), ("Sonnet", "plan-review"))

    def test_free_project_default_plan_reviewer_still_prefers_another_executor(self):
        self.journal.append("K-1", "plan.proposed", planner="Sonnet", rows=self.rows)
        self.journal.append("K-1", "plan.revision-ready", pending=True)
        with mock.patch.object(self.engine, "_plan_step") as launch:
            self.engine._advance_plan_review("K-1", self.state())
        self.assertNotEqual(launch.call_args.args[2].name, "Sonnet")

    def test_free_project_resumed_plan_review_accepts_the_planners_model_in_a_new_session(self):
        review = self.step("review", "", "plan-review")
        self.journal.append("K-1", "step.finished", step="review", result="error")
        selected = self.engine._resume_executor("K-1", self.state(), review, "Sonnet", changed=True)
        self.assertEqual(selected.name, "Sonnet")
        # Zmiana wykonawcy wznawia krok w nowej sesji (`resume_session` tylko bez zmiany wykonawcy).
        self.assertEqual(self.engine._review_policy("K-1", klass=None), "free")

    def test_new_proposal_records_its_actual_author_independently_of_future_override(self):
        self.journal.append("K-1", "step.started", step="new-version", kind="plan-amend", executor="Codex standard")
        self.journal.append("K-1", "step.finished", step="new-version", result="done")
        self.journal.append("K-1", "plan.proposed", planner="Codex standard", sourceStep="new-version", rows=self.rows)
        self.journal.append("K-1", "plan.revision-ready", pending=True)
        self.engine.resolve_step("K-1", {"choice": "reassign-author", "ticket": "plan", "executor": "Sonnet"})
        self.assertTrue(self.engine.edit_plan("K-1", {"action": "review-executor", "executor": "Sonnet"})["ok"])
        with mock.patch.object(self.engine, "_plan_step") as launch:
            self.engine._advance_plan_review("K-1", self.state())
            self.assertEqual(launch.call_args.args[2].name, "Sonnet")

    def test_legacy_wait_for_imported_done_resumes_without_a_future_merge(self):
        from tide_daemon.cardflow.state import ticket_waits
        a = self.step()
        self.journal.append("K-1", "step.finished", step="a", result="question", question="Brak pracy")
        self.journal.append("K-1", "step.decision", step="a", choice="wait-ticket", waitTicket="PT-2")
        self.journal.append("K-1", "plan.approved", rows=[{**r, "sourceState": "gotowe" if r["id"] == "PT-2" else "todo"}
                                                        for r in self.rows])
        self.engine = self.new_engine()
        st = self.state()
        self.assertEqual(ticket_waits(st), {})
        with mock.patch.object(self.engine, "_resume_step") as resume:
            self.engine._advance("K-1", st)
            resume.assert_called_once()
            self.assertIn("gotowy z przejętego planu", resume.call_args.args[3]["answer"])
