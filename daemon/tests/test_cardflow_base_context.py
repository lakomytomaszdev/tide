"""PT-213: publiczne skutki pustej korekty, domknięcie i nowa sesja, bez replay grafu."""
import json
from pathlib import Path
from unittest import mock

from .test_cardflow_base import StartBaseSetup
from . import test_cardflow_base_change as preview_tests
from . import test_cardflow_base_generation as generation_tests
from .test_cardflow_dbus import git
from .privatebus import wait_until
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.state import fold, ticket_of


class BaseContextTest(StartBaseSetup):
    call = preview_tests.BaseChangeTest.call
    preview = generation_tests.EmptyGenerationTest.preview
    request = generation_tests.EmptyGenerationTest.request

    def setUp(self):
        super().setUp()
        # Ten sam adapter prawdziwego gita co w publicznych testach podglądu.
        from .test_cardflow_gitops import LocalStepGit
        from tide_daemon.cardflow import gitops

        class ObjectGit(LocalStepGit):
            def __init__(self, *args, objects_only=False):
                super().__init__(*args)

        for patch in (mock.patch.object(gitops, "sandbox_available", return_value=True),
                      mock.patch.object(gitops, "StepGit", ObjectGit)):
            patch.start()
            self.addCleanup(patch.stop)
        self.feature_sha = self.feature()
        git(self.repo, "worktree", "add", str(Path(self.base, "ws/feature")), "feature/start")
        self.ident = self.card(description="ATRAPA: pytanie-bez-pracy")
        self.started = self.start(self.ident)
        self.assertTrue(self.started["ok"], self.started)

    def seed_question(self):
        journal = self.flow.engine.journal
        step_dir = Path(self.base, self.ident + "-old.step")
        (step_dir / "out").mkdir(parents=True)
        (step_dir / "out/note.md").write_text("Pytanie przed pierwszą zmianą.\n")
        journal.append(self.ident, "step.started", step="old", kind="ticket", attempt=1,
                       ticket=self.started["ticket"], executor="Sonnet", program="claude",
                       model="sonnet", provider="anthropic", session="old-session",
                       branch=self.started["branch"], worktree=self.started["worktree"],
                       stepDir=str(step_dir), baseVersion=1, baseCommit=self.master)
        journal.append(self.ident, "step.finished", step="old", result="error", reason="login", notes=["note.md"])

    def closed_empty_fixture(self):
        """Od razu, publiczne odrzucenie bez wkładu; podsumowanie poza repo projektu."""
        self.seed_question()
        self.assertTrue(self.call("resolve_card_step", {"choice": "reject", "step": "old"})["ok"])
        journal = self.flow.engine.journal
        # Domknięcie jest metadanymi, nie commitem ani akceptacją pustego ticketu.
        journal.append(self.ident, "card.closed", summary="Stare podsumowanie na A", commit=self.master,
                       baseVersion=1, check="auto")
        self.assertTrue(Path(journal.mirror_path(self.ident)).is_file())
        self.assertFalse(Path(journal.mirror_path(self.ident)).is_relative_to(self.repo))
        self.assertFalse(Path(journal.path(self.ident)).is_relative_to(self.repo))
        for path, branch in ((self.started["worktree"], self.started["branch"]),
                             (self.started["cardWorktree"], self.started["cardBranch"])):
            self.assertEqual(git(path, "rev-parse", "HEAD").strip(), self.master)
            self.assertEqual(git(path, "status", "--porcelain"), "")
            self.assertEqual(git(self.repo, "rev-list", self.master + ".." + branch), "")
        st = fold(self.events(self.ident))
        self.assertFalse(st.running())
        self.assertFalse(st.card_finished)
        self.assertFalse(st.merged)
        self.assertFalse(st.verdicts)
        self.assertFalse(self.results(self.ident, "merge"))

    def change(self):
        before = self.events(self.ident)
        preview = self.preview()
        self.assertTrue(preview["canExecute"])
        result = self.call("resolve_card_step", self.request(preview))
        self.assertTrue(result["changed"], result)
        self.assertEqual(self.events(self.ident)[:len(before)], before)
        return result

    def test_closed_rejected_empty_ticket_requires_new_summary_on_b_after_restart(self):
        self.closed_empty_fixture()
        old = self.call("preview_card_merge", {"targetRef": "refs/heads/master"})
        self.assertTrue(old["canMerge"], old)
        changed = self.change()
        self.restart()
        engine = self.flow.engine
        with mock.patch.object(engine.git, "branch_worktree", side_effect=AssertionError("ponowna aktywacja")):
            engine.tick()
            engine.tick()
        self.board.publish()
        flow = self.shown(self.ident)["derived"]["flow"]
        self.assertFalse(flow["closed"])
        self.assertEqual(flow["summary"], "")
        self.assertEqual(flow["initialBase"]["baseCommit"], self.master)
        self.assertEqual((flow["base"]["baseCommit"], flow["baseVersion"]), (self.feature_sha, 2))
        self.assertTrue(any(h["old"] and h["kind"] == "card" for h in flow["history"]))
        targets = self.call("list_card_branches", {"purpose": "merge"})
        self.assertEqual(targets["branches"][0]["ref"], "refs/heads/feature/start")
        preview = self.call("preview_card_merge", {"targetRef": "refs/heads/feature/start"})
        self.assertFalse(preview["canMerge"])
        self.assertIn("not-closed", {r["reason"] for r in preview["blockedReasons"]})
        before = self.events(self.ident)
        stale = self.call("merge_card", {"targetRef": "refs/heads/master", "previewToken": old["previewToken"]})
        self.assertEqual(stale["error"], "stale-preview", stale)
        self.assertEqual(self.events(self.ident), before)
        with self.assertRaises(FlowError):
            engine.merge_card_to_main(self.ident)
        # Nowe podsumowanie uruchamia rzeczywistą atrapę CLI, bez review fikcyjnego commitu.
        engine._launch = FlowEngine._launch.__get__(engine)
        engine.tick()
        self.assertTrue(wait_until(lambda: self._tick_closed(), timeout=15))
        st = fold(self.events(self.ident))
        close = st.steps[st.closed["step"]]
        self.assertEqual(close["kind"], "close")
        self.assertEqual(close["baseVersion"], 2)
        self.assertEqual(close["baseCommit"], self.feature_sha)
        self.assertNotEqual(close["session"], "old-session")
        self.assertEqual(close["worktree"], ticket_of(st)["cardWorktree"])
        self.assertEqual(st.closed["baseVersion"], 2)
        self.assertFalse(st.verdicts)
        self.assertFalse(any(s["kind"] == "review" for s in st.steps.values()))
        ready = self.call("preview_card_merge", {"targetRef": "refs/heads/feature/start"})
        self.assertTrue(ready["canMerge"], ready)
        self.assertEqual(engine.card_diff(self.ident)["targetRef"], "refs/heads/feature/start")
        self.assertEqual(len(self.of(self.ident, "card.base-changed")), 1)
        for row in changed["generation"]:
            self.assertEqual(git(self.repo, "rev-parse", row["oldBranch"]).strip(), self.master)

    def _tick_closed(self):
        self.flow.engine.tick()
        self.board.publish()
        return bool(fold(self.events(self.ident)).closed)

    def test_historical_review_metadata_is_not_an_approval_for_b(self):
        self.seed_question()
        journal = self.flow.engine.journal
        # Odczyt dawnych metadanych; brak zaakceptowanego ticketu, kroku review lub commitu wkładu.
        journal.append(self.ident, "review.verdict", step="historical-review-metadata", ticket=self.started["ticket"],
                       round=1, verdict="APPROVE", blockers=0, majors=0, nits=0,
                       baseVersion=1, reviewTip=self.master)
        journal.append(self.ident, "outside.ticket-consent", ticket=self.started["ticket"], step="old")
        self.change()
        self.restart()
        self.board.publish()
        flow = self.shown(self.ident)["derived"]["flow"]
        self.assertEqual(flow["approvals"], [])
        self.assertEqual(len(flow["historicalApprovals"]), 1)
        historical = flow["historicalApprovals"][0]
        self.assertEqual((historical["baseVersion"], historical["reviewTip"]), (1, self.master))
        self.assertNotIn("verdict", flow)
        self.assertFalse(self.flow.engine._ticket_consent(self.ident, self.started["ticket"]))
        self.assertFalse(self.results(self.ident, "merge"))
        self.assertFalse(fold(self.events(self.ident)).merge_pending())
        refused = self.call("resolve_card_step", {"choice": "retry", "step": "old"})
        self.assertEqual(refused["error"], "stale-context")

    def test_new_step_uses_new_directory_context_note_and_fresh_cli_session(self):
        self.seed_question()
        self.change()
        self.restart()
        engine = self.flow.engine
        engine._launch = FlowEngine._launch.__get__(engine)
        engine.tick()
        new = next(s for name, s in fold(self.events(self.ident)).steps.items() if name != "old")
        self.assertEqual((new["baseRef"], new["baseCommit"], new["baseVersion"]),
                         ("refs/heads/feature/start", self.feature_sha, 2))
        self.assertNotEqual(new["worktree"], self.started["worktree"])
        self.assertNotEqual(new["session"], "old-session")
        prompt = Path(new["stepDir"], "in/prompt.txt").read_text()
        self.assertIn("Notka korekty bazy", prompt)
        self.assertIn(self.feature_sha, prompt)
        self.assertIn("Wersja bazy karty: 2", prompt)
        self.assertIn("old.step/out", prompt)
        invocation = Path(new["stepDir"], "out/fake-call.json")
        # Plik może już istnieć, gdy atrapa jeszcze zapisuje końcówkę JSON. Pierwszy
        # odczyt urywamy celowo, żeby ta próba obejmowała wyścig niezależnie od obciążenia.
        read_text, partial_reads = Path.read_text, []

        def partial_read(path, *args, **kwargs):
            text = read_text(path, *args, **kwargs)
            if path == invocation and not partial_reads:
                partial_reads.append(True)
                return text[:-1]
            return text

        def invocation_ready():
            try:
                return json.loads(invocation.read_text())
            except (OSError, ValueError):
                return None

        with mock.patch.object(Path, "read_text", partial_read):
            calls = wait_until(invocation_ready, timeout=5)
        self.assertTrue(calls, f"Brak kompletnego wywołania atrapy: {invocation}")
        self.assertEqual(partial_reads, [True])
        self.assertNotIn("--resume", calls["argv"])
        self.assertEqual(calls["cwd"], new["worktree"])
        self.assertIn(new["session"], calls["argv"])
        with self.assertRaises(FlowError):
            engine.enter(self.ident, "old")

    def test_reviewed_uncommitted_plan_requires_new_planning_and_review_after_restart(self):
        self.ident = self.card(description="ATRAPA: planowanie")
        engine = self.flow.engine
        engine._launch = FlowEngine._launch.__get__(engine)
        self.assertTrue(self.start(self.ident, entry="planning", mode="light")["ok"])

        def reviewed(count):
            self.flow.engine.tick()
            return len(self.of(self.ident, "plan.reviewed")) == count

        self.assertTrue(wait_until(lambda: reviewed(1), timeout=10), self.events(self.ident))
        old_steps = self.of(self.ident, "step.started")
        old_proposal = self.of(self.ident, "plan.proposed")[0]
        old_review = self.of(self.ident, "plan.reviewed")[0]
        self.assertEqual(old_review["verdict"], "APPROVE")
        artifacts = {str(p): p.read_bytes() for step in old_steps
                     for p in Path(step["stepDir"], "out").iterdir() if p.is_file()}
        for step in old_steps:
            self.assertEqual(git(step["worktree"], "rev-parse", "HEAD").strip(), self.master)
            self.assertEqual(git(step["worktree"], "status", "--porcelain"), "")
        self.assertFalse(self.of(self.ident, "plan.approved"))
        self.assertFalse(fold(self.events(self.ident)).running())
        self.change()
        self.restart()
        self.board.publish()
        flow = self.shown(self.ident)["derived"]["flow"]
        self.assertEqual(flow["plan"]["rows"], [])
        self.assertFalse(flow["plan"]["review"].get("verdict"))
        self.assertTrue(any(h["old"] and h["id"] == f"plan.reviewed#{old_review['seq']}"
                            for h in flow["history"]))
        box = []
        self.flow.approve_plan(self.ident, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertEqual(json.loads(box[0])["error"], "no-plan")
        self.assertFalse(self.of(self.ident, "plan.approved"))
        engine = self.flow.engine
        engine._launch = FlowEngine._launch.__get__(engine)
        # Nowa propozycja sama nie wystarcza: obowiązuje dotychczasowa bramka review.
        engine.tick()

        def proposed():
            engine.tick()
            return len(self.of(self.ident, "plan.proposed")) == 2

        self.assertTrue(wait_until(proposed, timeout=10))
        with self.assertRaises(FlowError) as error:
            engine.approve_plan(self.ident)
        self.assertEqual(error.exception.code, "review-pending")
        self.assertTrue(wait_until(lambda: reviewed(2), timeout=10))
        new_review = [s for s in self.of(self.ident, "step.started") if s["kind"] == "plan-review"][-1]
        self.assertEqual((new_review["baseCommit"], new_review["baseVersion"]), (self.feature_sha, 2))
        self.assertGreater(self.of(self.ident, "plan.proposed")[-1]["seq"], old_proposal["seq"])
        self.assertTrue(engine.approve_plan(self.ident)["ok"])
        for path, content in artifacts.items():
            self.assertEqual(Path(path).read_bytes(), content)

    def test_new_question_has_separate_resumable_history_after_base_change_and_restart(self):
        self.seed_question()
        self.change()
        self.restart()
        engine = self.flow.engine
        engine._launch = FlowEngine._launch.__get__(engine)

        def asked():
            engine.tick()
            return any(e.get("result") == "question" for e in self.of(self.ident, "step.finished"))

        self.assertTrue(wait_until(asked, timeout=10))
        new = self.of(self.ident, "step.started")[-1]
        self.assertEqual(new["parentStep"], "old")  # dowód dla promptu pozostaje
        for restart in (False, True):
            if restart:
                self.restart()
            self.board.publish()
            flow = self.shown(self.ident)["derived"]["flow"]
            lines = [h for h in flow["history"] if h["kind"] == "step"]
            self.assertEqual([h["step"] for h in lines], ["old", new["step"]])
            self.assertTrue(lines[0]["old"])
            self.assertEqual(lines[0]["result"], "error")
            self.assertFalse(lines[0]["canResume"])
            self.assertFalse(lines[1]["old"])
            self.assertEqual(lines[1]["result"], "question")
            self.assertTrue(lines[1]["canResume"])
            self.assertEqual(len(lines[1]["attempts"]), 1)

    def test_empty_planning_question_restarts_on_new_base_with_fresh_step_after_restart(self):
        self.ident = self.card(description="ATRAPA: plan-question")
        engine = self.flow.engine
        engine._launch = FlowEngine._launch.__get__(engine)
        reply = self.start(self.ident, entry="planning", mode="light")
        self.assertTrue(reply["ok"], reply)

        def question_count(count):
            self.flow.engine.tick()
            return len([e for e in self.of(self.ident, "step.finished") if e.get("result") == "question"]) == count

        self.assertTrue(wait_until(lambda: question_count(1), timeout=10))
        old = self.of(self.ident, "step.started")[0]
        old_call = Path(old["stepDir"], "out/fake-call.json").read_text()
        self.assertEqual(old["kind"], "planning")
        self.assertEqual(git(old["worktree"], "rev-parse", "HEAD").strip(), self.master)
        self.assertEqual(git(old["worktree"], "status", "--porcelain"), "")
        self.assertFalse(fold(self.events(self.ident)).running())
        self.assertFalse(self.of(self.ident, "plan.proposed"))
        self.change()
        self.restart()
        engine = self.flow.engine
        engine._launch = FlowEngine._launch.__get__(engine)
        with mock.patch.object(engine.git, "branch_worktree", side_effect=AssertionError("ponowna aktywacja")):
            self.assertTrue(wait_until(lambda: question_count(2), timeout=10), self.events(self.ident))
        steps = self.of(self.ident, "step.started")
        self.assertEqual(len(steps), 2)
        new = steps[-1]
        self.assertNotEqual(new["step"], old["step"])
        self.assertNotEqual(new["stepDir"], old["stepDir"])
        self.assertNotEqual(new["session"], old["session"])
        self.assertNotEqual(new["worktree"], old["worktree"])
        self.assertEqual((new["baseRef"], new["baseCommit"], new["baseVersion"]),
                         ("refs/heads/feature/start", self.feature_sha, 2))
        self.assertEqual(git(new["worktree"], "rev-parse", "HEAD").strip(), self.feature_sha)
        call = json.loads(Path(new["stepDir"], "out/fake-call.json").read_text())
        self.assertEqual(call["cwd"], new["worktree"])
        self.assertNotIn("--resume", call["argv"])
        self.assertIn(new["session"], call["argv"])
        self.assertIn("Notka korekty bazy", call["prompt"])
        self.assertIn("Wersja bazy karty: 2", call["prompt"])
        self.assertIn(self.feature_sha, call["prompt"])
        self.assertEqual(Path(old["stepDir"], "out/fake-call.json").read_text(), old_call)
        self.assertEqual(git(old["worktree"], "rev-parse", "HEAD").strip(), self.master)
        self.restart()
        engine = self.flow.engine
        engine._launch = FlowEngine._launch.__get__(engine)
        engine.tick()
        engine.tick()
        self.assertEqual(self.of(self.ident, "step.started"), steps)
        self.assertEqual(len(self.results(self.ident, "step-start")), 2)
        self.assertEqual(len(self.results(self.ident, "card-branch")), 1)
        self.board.publish()
        flow = self.shown(self.ident)["derived"]["flow"]
        self.assertTrue(any(h["old"] and h.get("step") == old["step"] for h in flow["history"]))
        self.assertEqual(flow["decisions"][0]["step"], new["step"])
        before = self.events(self.ident)
        refused = self.call("resolve_card_step", {"choice": "answer", "step": old["step"], "answer": "Pierwszy"})
        self.assertEqual(refused["error"], "stale-context", refused)
        self.assertEqual(self.events(self.ident), before)
        self.assertEqual(len(self.of(self.ident, "card.base-changed")), 1)
        answered = self.call("resolve_card_step", {"choice": "answer", "step": new["step"], "answer": "Pierwszy"})
        self.assertTrue(answered["ok"], answered)

        def reviewed():
            engine.tick()
            return bool(self.of(self.ident, "plan.reviewed"))

        self.assertTrue(wait_until(reviewed, timeout=10), self.events(self.ident))
        self.assertEqual(self.of(self.ident, "plan.reviewed")[-1]["verdict"], "APPROVE")
        review = next(s for s in self.of(self.ident, "step.started") if s["kind"] == "plan-review")
        self.assertEqual((review["baseCommit"], review["baseVersion"]), (self.feature_sha, 2))

    def test_empty_full_planning_restarts_with_current_context_and_note(self):
        from .test_cardflow_full_planning import _Terminal

        self.ident = self.card(description="Planowanie bez zmian")
        engine = self.flow.engine
        terminal = _Terminal()
        engine.terminal = terminal
        reply = self.start(self.ident, entry="planning", mode="full")
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(reply["interactive"])
        old = self.of(self.ident, "step.started")[0]
        old_prompt = Path(old["stepDir"], "in/prompt.txt").read_text()
        # Zakończona rozmowa bez wkładu; dowód nieaktywnego procesu Konsoli.
        Path(old["stepDir"], "in/console-process.json").write_text(json.dumps({
            "launcher": {"pid": 99999999, "start": "0", "boot": "previous-boot"}}))
        Path(old["stepDir"], "in/console-closed.json").write_text("{}")
        Path(old["stepDir"], "out/result.json").write_text(json.dumps({
            "result": "question", "question": "Jaki format?"}))
        engine.tick()
        self.assertEqual(self.of(self.ident, "step.finished")[-1]["result"], "question")
        self.assertFalse(fold(self.events(self.ident)).running())
        self.assertEqual(git(old["worktree"], "rev-parse", "HEAD").strip(), self.master)
        self.assertEqual(git(old["worktree"], "status", "--porcelain"), "")
        self.assertFalse(self.of(self.ident, "plan.proposed"))
        self.change()
        self.restart()
        engine = self.flow.engine
        engine.terminal = terminal
        with mock.patch.object(engine.git, "branch_worktree", side_effect=AssertionError("ponowna aktywacja")):
            engine.tick()
        steps = self.of(self.ident, "step.started")
        self.assertEqual(len(steps), 2)
        new = steps[-1]
        for key in ("step", "stepDir", "session", "worktree"):
            self.assertNotEqual(new[key], old[key], key)
        self.assertEqual((new.get("baseRef"), new.get("baseCommit"), new.get("baseVersion")),
                         ("refs/heads/feature/start", self.feature_sha, 2))
        self.assertEqual(git(new["worktree"], "rev-parse", "HEAD").strip(), self.feature_sha)
        self.assertEqual(len(terminal.opened), 2)
        task = json.loads(Path(terminal.opened[-1].task_file).read_text())
        prompt = Path(new["stepDir"], "in/prompt.txt").read_text()
        self.assertEqual(task["workdir"], new["worktree"])
        self.assertEqual(task["argv"][-1], prompt)
        self.assertNotIn("--resume", task["argv"])
        self.assertIn(new["session"], task["argv"])
        self.assertIn("Notka korekty bazy", prompt)
        self.assertIn("Wersja bazy karty: 2", prompt)
        self.assertIn(self.feature_sha, prompt)
        self.assertEqual(Path(old["stepDir"], "in/prompt.txt").read_text(), old_prompt)
        self.assertEqual(git(old["worktree"], "rev-parse", "HEAD").strip(), self.master)
        self.assert_base(self.ident, "refs/heads/feature/start", self.feature_sha, version=2)
        flow = self.shown(self.ident)["derived"]["flow"]
        shown = next(s for s in flow["steps"] if s["id"] == new["step"])
        self.assertEqual(shown["baseVersion"], 2)
        self.assertTrue(any(h["old"] and h.get("step") == old["step"] for h in flow["history"]))
        self.restart()
        self.flow.engine.terminal = terminal
        self.flow.engine.tick()
        self.flow.engine.tick()
        self.assertEqual(self.of(self.ident, "step.started"), steps)
        self.assertEqual(len(terminal.opened), 2)
        self.assertEqual(len(self.of(self.ident, "card.base-changed")), 1)

    def test_even_empty_commit_or_merge_is_refused(self):
        for merge in (False, True):
            with self.subTest(merge=merge):
                if merge:
                    self.ident = self.card()
                    self.started = self.start(self.ident)
                path = self.started["worktree"]
                git(path, "commit", "--allow-empty", "-qm", "empty contribution")
                if merge:
                    git(self.started["cardWorktree"], "merge", "--no-ff", "--no-edit", self.started["branch"])
                preview = self.call("preview_card_base_change", {"baseRef": "refs/heads/feature/start"})
                self.assertFalse(preview["canExecute"])
                self.assertIn("contribution", {r["reason"] for r in preview["blockedReasons"]})

    def test_new_work_requires_review_of_its_actual_sha_before_integration(self):
        self.ident = self.card(description="ATRAPA: gotowe")
        self.started = self.start(self.ident)
        self.seed_question()
        self.change()
        self.restart()
        engine = self.flow.engine
        engine._launch = FlowEngine._launch.__get__(engine)
        engine.tick()
        self.assertTrue(wait_until(lambda: self._tick_closed(), timeout=20))
        st = fold(self.events(self.ident))
        reviews = [s for s in st.steps.values() if s.get("kind") == "review"]
        self.assertEqual(len(reviews), 1)
        review = reviews[0]
        self.assertEqual(review["baseVersion"], 2)
        self.assertEqual(review["baseCommit"], self.feature_sha)
        self.assertNotIn(review["reviewTip"], (self.master, self.feature_sha))
        verdict = next(iter(st.verdicts.values()))
        self.assertEqual((verdict["reviewTip"], verdict["baseVersion"]), (review["reviewTip"], 2))
        events = self.events(self.ident)
        self.assertEqual(fold(events), fold(json.loads(json.dumps(events))))
        # Późne metadane z dawnym SHA albo wersją nie zastępują bieżącej zgody.
        for stale in ({**verdict, "baseVersion": 1}, {**verdict, "reviewTip": self.master}):
            replay = fold(events + [{**stale, "seq": st.last_seq + 1}])
            self.assertEqual(replay.verdicts, st.verdicts)
        merged = self.results(self.ident, "merge")[0]
        self.assertGreater(merged["seq"], verdict["seq"])
        self.assertGreater(st.closed["seq"], merged["seq"])
        self.assertEqual(Path(ticket_of(st)["cardWorktree"], "feature.txt").read_text(), "tylko feature\n")
        self.assertTrue(self.results(self.ident, "cleanup")[0]["preserved"])

    def test_current_review_enter_preserves_base_and_approval_after_restart(self):
        self._review_enter_preserves_context()

    def test_local_unavailable_review_enter_preserves_approval_after_restart(self):
        self._review_enter_preserves_context(local_unavailable=True)

    def _review_enter_preserves_context(self, *, local_unavailable=False):
        from .test_cardflow_enter_unit import _Terminal

        def configure_local(engine):
            from tide_daemon.models import config as models_config

            path = Path(self.base, "config/tide/models.json")
            path.write_text(json.dumps({
                "roles": {"agent": "local:qwen3-coder:30b"}, "agentProgram": "claude",
                "executors": [
                    {"name": "Sonnet", "provider": "anthropic", "program": "claude", "model": "sonnet"},
                    {"name": "Opus", "provider": "anthropic", "program": "claude", "model": "opus"},
                    {"name": "Lokalny", "provider": "local", "classes": ["lokalny"]},
                    {"name": "Lokalny recenzent", "provider": "local", "program": "claude",
                     "model": "qwen2.5-coder:7b", "endpoint": "local", "classes": ["lokalny"]}]}))
            engine.models_config = lambda: models_config.load(str(path))
            engine.model_probe = lambda endpoint: {
                "online": True, "kind": "ollama",
                "models": [{"name": "qwen3-coder:30b"}, {"name": "qwen2.5-coder:7b"}]}

        for result in ("question", "error"):
            for recover in (False, True):
                with self.subTest(result=result, recover=recover):
                    from tide_daemon.cardflow.engine import FlowDeferred

                    self.flow.engine._launch = mock.Mock(side_effect=FlowDeferred("kolejka testowa"))
                    self.ident = self.card(description="ATRAPA: gotowe")
                    if local_unavailable:
                        configure_local(self.flow.engine)
                    self.started = self.start(self.ident, **(
                        {"executor": "Lokalny", "preferredProvider": "local"} if local_unavailable else {}))
                    self.assertTrue(self.started["ok"], self.started)
                    self.seed_question()
                    self.change()
                    self.restart()
                    engine = self.flow.engine
                    if local_unavailable:
                        configure_local(engine)
                    engine._launch = FlowEngine._launch.__get__(engine)
                    finish = engine._finish
                    advance = engine._advance

                    def interrupt_review(card, step, **kwargs):
                        if step.get("kind") == "review":
                            kwargs["forced"] = {"result": result, "question": "Potrzebuję wyjaśnienia",
                                                "reason": "test"}
                        return finish(card, step, **kwargs)

                    def review_waiting():
                        engine.tick()
                        st = fold(self.events(self.ident))
                        return any(s.get("kind") == "review" and s["step"] in st.finished
                                   for s in st.steps.values())

                    def pause_review(card, st, *, own=None):
                        if any(s.get("kind") == "review" and s["step"] in st.finished
                               for s in st.steps.values()):
                            return
                        return advance(card, st, own=own)

                    with mock.patch.object(engine, "_finish", side_effect=interrupt_review), \
                            mock.patch.object(engine, "_advance", side_effect=pause_review):
                        self.assertTrue(wait_until(review_waiting, timeout=20))
                    st = fold(self.events(self.ident))
                    review = next(s for s in st.steps.values() if s.get("kind") == "review")
                    self.assertEqual(st.finished[review["step"]]["result"], result)
                    self.assertFalse(st.verdicts)
                    self.assertFalse(self.results(self.ident, "merge"))
                    if local_unavailable:
                        self.assertEqual((review["program"], review["provider"]), ("claude", "local"))
                        engine.model_probe = lambda endpoint: {"online": False, "error": "offline", "models": []}
                        engine._resume_step(self.ident, st, review, {"choice": "retry"})
                        st = fold(self.events(self.ident))
                        unavailable = next(s for s in st.steps.values()
                                           if s.get("parentStep") == review["step"])
                        self.assertEqual(st.finished[unavailable["step"]]["reason"], "local-unavailable")
                        self.assertFalse(st.verdicts)
                        self.assertFalse(self.results(self.ident, "merge"))
                        self.restart()
                        engine = self.flow.engine
                        configure_local(engine)
                        review = fold(self.events(self.ident)).steps[unavailable["step"]]
                    engine.terminal = _Terminal([])
                    entered = engine.enter(self.ident, review["step"])
                    current = fold(self.events(self.ident)).steps[entered["step"]]
                    self.assertEqual(current["kind"], "review")
                    self.assertEqual(current.get("reviewTip"), review["reviewTip"])
                    for key, expected in (("baseRef", "refs/heads/feature/start"),
                                          ("baseCommit", self.feature_sha), ("baseVersion", 2)):
                        self.assertEqual(current.get(key), expected, key)
                    self.assertEqual(current["baseVersion"], 2)
                    Path(current["stepDir"], "out/result.json").write_text(json.dumps({
                        "result": "done", "verdict": "APPROVE", "blockers": 0, "majors": 0, "nits": 0}))
                    Path(current["closeFile"]).write_text("{}")
                    if recover:
                        # Awaria między step.finished a review.verdict: restart odzyskuje zgodę z pliku.
                        append = engine.journal.append

                        def crash(card, kind, **data):
                            if kind == "review.verdict":
                                raise OSError("przerwano zapis werdyktu")
                            return append(card, kind, **data)

                        with mock.patch.object(engine.journal, "append", side_effect=crash):
                            with self.assertRaisesRegex(OSError, "przerwano zapis werdyktu"):
                                engine._finish(self.ident, current, exit_code=0)
                        self.assertIn(current["step"], fold(self.events(self.ident)).finished)
                        self.assertFalse(fold(self.events(self.ident)).verdicts)
                    self.restart()
                    if local_unavailable:
                        configure_local(self.flow.engine)
                    self.flow.engine._launch = FlowEngine._launch.__get__(self.flow.engine)
                    self.assertTrue(wait_until(lambda: self._tick_closed(), timeout=20))
                    st = fold(self.events(self.ident))
                    verdict = st.verdicts[(review["ticket"], review["round"])]
                    self.assertEqual((verdict["step"], verdict["reviewTip"], verdict["baseVersion"]),
                                     (current["step"], review["reviewTip"], 2))
                    self.assertEqual(verdict["verdict"], "APPROVE")
                    self.assertGreater(self.results(self.ident, "merge")[0]["seq"], verdict["seq"])
                    self.assertEqual(len(self.of(self.ident, "card.base-changed")), 1)
