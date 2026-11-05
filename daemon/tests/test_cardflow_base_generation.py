"""PT-202: publiczny Preview → Resolve → Snapshot i awarie na prawdziwym repo."""
import copy
from pathlib import Path
from unittest import mock

from .test_cardflow_base import StartBaseSetup
from . import test_cardflow_base_change as preview_tests
from .test_cardflow_dbus import git
from .test_cardflow_merge_recovery import PowerLoss
from tide_daemon.cardflow.basegeneration import BaseGeneration
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.cardflow.gitops import FlowGitError, worktree_for
from tide_daemon.cardflow.state import fold, ticket_of


class EmptyGenerationTest(StartBaseSetup):
    call = preview_tests.BaseChangeTest.call
    ask = preview_tests.BaseChangeTest.ask
    commit = preview_tests.BaseChangeTest.commit

    def setUp(self):
        super().setUp()
        self.feature_sha = self.feature()
        self.new_card()

    def new_card(self):
        self.ident = self.card()
        self.started = self.start(self.ident)
        self.assertTrue(self.started["ok"], self.started)

    def preview(self, **extra):
        reply = self.call("preview_card_base_change", {"baseRef": "refs/heads/feature/start",
                                                      "baseCommit": self.feature_sha, **extra})
        self.assertTrue(reply["canChange"], reply)
        return reply

    def request(self, preview):
        return {"choice": "change-base", "baseRef": preview["newBase"]["baseRef"],
                "baseCommit": preview["newBase"]["baseCommit"], "previewId": preview["previewId"],
                "step": preview["decisionStep"]}

    def intent(self):
        return next(e for e in self.events(self.ident) if e["type"] == "intent" and e.get("action") == "change-base")

    def crash(self, request, kind, *, stage=None, action=None, before=False):
        journal = self.flow.engine.journal
        original = journal.append

        def append(card, event, **data):
            hit = event == kind and (stage is None or data.get("stage") == stage) and (
                action is None or data.get("action") == action)
            if hit and before:
                raise PowerLoss()
            result = original(card, event, **data)
            if hit:
                raise PowerLoss()
            return result

        with mock.patch.object(journal, "append", append), self.assertRaises(PowerLoss):
            self.flow.engine.resolve_step(self.ident, request)

    def assert_active(self, count=2, ignored_collision=False):
        engine = self.flow.engine
        st = fold(self.events(self.ident))
        intent = self.intent()
        self.assertEqual(st.base_version, 2)
        self.assertEqual(st.base["baseCommit"], self.feature_sha)
        self.assertEqual(st.initial_base["baseCommit"], self.master)
        self.assertTrue(st.base_validation_required)
        self.assertEqual(len(intent["generation"]), count)
        self.assertEqual(len(self.of(self.ident, "card.base-changed")), 1)
        self.assertEqual(len(self.results(self.ident, "change-base")), 1)
        for row in intent["generation"]:
            self.assertEqual(git(self.repo, "rev-parse", "refs/heads/" + row["oldBranch"]).strip(), self.master)
            self.assertEqual(git(row["oldWorktree"], "rev-parse", "HEAD").strip(), self.master)
            self.assertEqual(git(row["worktree"], "rev-parse", "HEAD").strip(), self.feature_sha)
            self.assertEqual(git(self.repo, "rev-parse", row["archiveRef"]).strip(), self.master)
            self.assertEqual(Path(row["worktree"], "feature.txt").read_text(), "tylko feature\n")
            if not ignored_collision:
                self.assertFalse(Path(row["oldWorktree"], "feature.txt").exists())
            self.assertNotEqual(row["oldWorktree"], row["worktree"])
            self.assertIn(row["oldWorktree"], engine.busy_places())
        info = ticket_of(st, self.started["ticket"])
        self.assertNotEqual(info["branch"], self.started["branch"])
        self.assertNotEqual(info["cardBranch"], self.started["cardBranch"])
        self.assertEqual(info["id"], self.started["ticket"])
        self.assertEqual(git(self.repo, "rev-list", self.feature_sha + ".." + info["branch"]).strip(), "")
        self.board.publish()
        shown = self.shown(self.ident)
        flow = shown["derived"]["flow"]
        self.assertEqual(flow["cardBranch"], info["cardBranch"])
        self.assertEqual(flow["branch"], info["branch"])
        self.assertEqual(flow["generation"], intent["generation"])
        self.assertTrue(flow["baseValidationRequired"])
        self.assertEqual(shown["derived"]["column"], "waiting")
        self.assertEqual(flow["decisions"], [])
        self.assertEqual(shown["baseCommit"], self.feature_sha)
        self.assertEqual(shown["baseVersion"], 2)

    def test_public_question_switches_all_empty_tickets_and_preserves_ignored_data(self):
        engine = self.flow.engine
        second = "PT-999"
        branch = "ticket/pt-999-empty"
        path = worktree_for(self.repo, branch)
        engine._act(self.ident, "reserve", second, lambda: {"ticket": second, "title": "Drugi"})
        engine._act(self.ident, "ticket-branch", second,
                    lambda: {**engine.git.branch_worktree(self.repo, branch, path, self.master), "ticket": second},
                    repo=self.repo, branch=branch, worktree=path, base=self.master, ticket=second)
        self.ask()
        excludes = Path(self.base, "excludes")
        excludes.write_text("cache/\nignored.bin\n")
        git(self.repo, "config", "core.excludesFile", str(excludes))
        for path in (self.started["worktree"], self.started["cardWorktree"]):
            Path(path, "cache").mkdir()
            Path(path, "cache", "keep.bin").write_bytes(b"dane wewnatrz")
            Path(path, "ignored.bin").write_bytes(b"dane")
        preview = self.preview(step="ask")
        self.assertFalse(preview["canExecute"], "UI czeka na PT-213")
        # Dowiedzione granice, bez zaakceptowanego ticketu ani review nieistniejącej pracy.
        for row in preview["branches"]:
            self.assertEqual(row["startCommit"], self.master)
            self.assertEqual(row["commit"], self.master)
            self.assertEqual(row["commits"], [])
        reply = self.call("resolve_card_step", self.request(preview))
        self.assertTrue(reply["changed"], reply)
        self.assert_active(count=3)
        for row in self.intent()["generation"]:
            if row["oldWorktree"] in (self.started["worktree"], self.started["cardWorktree"]):
                self.assertEqual(row["ignored"], ["cache/", "ignored.bin"])
                self.assertEqual(Path(row["oldWorktree"], "cache", "keep.bin").read_bytes(), b"dane wewnatrz")
                self.assertEqual(Path(row["oldWorktree"], "ignored.bin").read_bytes(), b"dane")
                self.assertFalse(Path(row["worktree"], "cache").exists())
        self.assertEqual(ticket_of(fold(self.events(self.ident)), second)["id"], second)
        self.restart()
        engine = self.flow.engine
        engine.tick()
        self.assert_active(count=3)
        self.assertTrue(self.call("resolve_card_step", self.request(preview))["changed"])
        self.assert_active(count=3)

    def test_user_initiative_uses_same_operation(self):
        reply = self.call("request_card_base_change", {"baseRef": "refs/heads/feature/start",
                                                      "baseCommit": self.feature_sha})
        request = self.request(reply["preview"])
        self.assertTrue(self.call("resolve_card_step", request)["changed"])
        self.assert_active()

    def test_remote_ref_and_ignored_collision_preserve_old_content(self):
        git(self.repo, "update-ref", "refs/remotes/team/topic", self.feature_sha)
        excludes = Path(self.base, "excludes")
        excludes.write_text("feature.txt\n")
        git(self.repo, "config", "core.excludesFile", str(excludes))
        old = Path(self.started["worktree"], "feature.txt")
        old.write_text("ignorowane dane użytkownika")
        reply = self.call("preview_card_base_change", {"baseRef": "refs/remotes/team/topic",
                                                      "baseCommit": self.feature_sha})
        self.assertTrue(reply["canChange"], reply)
        self.assertTrue(self.call("resolve_card_step", self.request(reply))["changed"])
        self.assert_active(ignored_collision=True)
        self.assertEqual(old.read_text(), "ignorowane dane użytkownika")
        self.assertEqual(fold(self.events(self.ident)).base["baseRef"], "refs/remotes/team/topic")

    def test_plan_metadata_and_not_started_ticket_remain_in_snapshot(self):
        journal = self.flow.engine.journal
        started = fold(self.events(self.ident)).started
        journal.append(self.ident, "card.started", **{k: v for k, v in started.items()
                       if k not in ("type", "card", "seq", "ts", "v", "entry")}, entry="planning")
        rows = [{"id": self.started["ticket"], "title": "Pusty", "blockers": []},
                {"id": "PT-999", "title": "Nierozpoczęty", "blockers": [self.started["ticket"]]}]
        journal.append(self.ident, "plan.approved", rows=rows, path="plan.md")
        before = fold(self.events(self.ident)).plan_approved
        self.assertTrue(self.call("resolve_card_step", self.request(self.preview()))["changed"])
        st = fold(self.events(self.ident))
        self.assertEqual(st.plan_approved, before)
        self.assertEqual(ticket_of(st, "PT-999")["branch"], "")
        self.assertNotEqual(ticket_of(st, "PT-999")["cardBranch"], self.started["cardBranch"])
        self.board.publish()
        shown = self.shown(self.ident)["derived"]
        self.assertEqual([row["id"] for row in shown["tickets"]], [r["id"] for r in rows])
        self.assertEqual([row["blockers"] for row in shown["flow"]["plan"]["rows"]], [r["blockers"] for r in rows])

    def test_crashes_at_durable_boundaries_recover_exactly_once(self):
        cases = [("intent", None, "change-base", False),
                 ("base-change.checkpoint", "resource-prepared", None, False),
                 ("base-change.checkpoint", "prepared", None, False),
                 ("card.base-changed", None, None, True),
                 ("card.base-changed", None, None, False),
                 ("result", None, "change-base", False)]
        for n, (kind, stage, action, before) in enumerate(cases):
            with self.subTest(kind=kind, stage=stage, before=before):
                if n:
                    self.new_card()
                self.ask()
                request = self.request(self.preview(step="ask"))
                self.crash(request, kind, stage=stage, action=action, before=before)
                key = self.intent()["key"]
                old = fold(self.events(self.ident))
                if not old.base_change:
                    self.assertEqual(ticket_of(old)["cardWorktree"], self.started["cardWorktree"])
                    self.assertEqual(old.base_version, 1)
                self.restart()
                self.flow.engine.tick()
                self.flow.engine.tick()
                self.assertTrue(self.call("resolve_card_step", request)["changed"])
                self.assertEqual(self.intent()["key"], key)
                self.assert_active()

    def test_crash_after_ref_transaction_and_worktree_creation_before_checkpoint(self):
        for command in ("update-ref", "worktree"):
            with self.subTest(command=command):
                if command == "worktree":
                    self.new_card()
                request = self.request(self.preview())
                original = self.flow.engine.git.run

                def run(cwd, *args, **kwargs):
                    result = original(cwd, *args, **kwargs)
                    if args[0] == command and (command != "worktree" or args[1] == "add") and result.ok:
                        raise PowerLoss()
                    return result

                with mock.patch.object(self.flow.engine.git, "run", run), self.assertRaises(PowerLoss):
                    self.flow.engine.resolve_step(self.ident, request)
                self.restart()
                self.flow.engine.tick()
                self.assert_active()

    def test_changes_after_preview_refuse_without_intent_or_activation(self):
        changes = [lambda: Path(self.started["worktree"], "untracked").write_text("praca"),
                   lambda: self.commit(self.started["cardWorktree"], "docs.md"),
                   lambda: self.flow.engine.journal.append(self.ident, "step.started", step="new-process", kind="ticket"),
                   lambda: git(self.repo, "branch", "-f", "feature/start", self.master)]
        for n, change in enumerate(changes):
            with self.subTest(n=n):
                if n:
                    self.new_card()
                request = self.request(self.preview())
                change()
                reply = self.call("resolve_card_step", request)
                self.assertEqual(reply["error"], "stale-preview", reply)
                self.assertFalse(any(e.get("action") == "change-base" for e in self.events(self.ident)))
                st = fold(self.events(self.ident))
                self.assertEqual(st.base_version, 1)
                self.assertEqual(ticket_of(st)["cardBranch"], self.started["cardBranch"])

    def test_late_local_work_after_intent_keeps_old_generation(self):
        request = self.request(self.preview())
        self.crash(request, "intent", action="change-base")
        Path(self.started["worktree"], "new.txt").write_text("nowa praca")
        self.restart()
        self.flow.engine.tick()
        self.assert_waiting()
        self.assertEqual(Path(self.started["worktree"], "new.txt").read_text(), "nowa praca")

    def test_new_resource_work_after_preparation_refuses_activation(self):
        request = self.request(self.preview())
        self.crash(request, "base-change.checkpoint", stage="prepared")
        row = self.intent()["generation"][0]
        changed = self.commit(row["worktree"], "new-work.txt")
        self.restart()
        self.flow.engine.tick()
        self.assert_waiting()
        self.assertEqual(git(self.repo, "rev-parse", row["branch"]).strip(), changed)
        self.assertEqual(Path(row["worktree"], "new-work.txt").read_text(), "wkład\n")

    def test_process_becomes_active_after_intent_without_new_journal_event(self):
        self.ask()
        request = self.request(self.preview(step="ask"))
        self.crash(request, "intent", action="change-base")
        self.restart()
        engine = self.flow.engine
        original = engine._base_change_processes

        def active(st):
            return [{**row, "active": True, "state": "active"} for row in original(st)]

        with mock.patch.object(engine, "_base_change_processes", active):
            BaseGeneration(engine).recover(self.ident)
        self.assert_waiting()

    def assert_waiting(self):
        st = fold(self.events(self.ident))
        self.assertEqual(st.base_version, 1)
        self.assertFalse(st.base_change)
        self.assertTrue(st.base_generation_wait)
        self.assertEqual(ticket_of(st)["branch"], self.started["branch"])
        self.assertEqual(st.base_generation_wait["generation"], self.intent()["generation"])
        self.board.publish()
        self.assertEqual(self.shown(self.ident)["derived"]["column"], "waiting")

    def test_foreign_ref_and_partial_preparation_error_preserve_all_resources(self):
        for case in ("foreign", "failure"):
            with self.subTest(case=case):
                if case == "failure":
                    self.new_card()
                request = self.request(self.preview())
                self.crash(request, "base-change.checkpoint", stage="resource-prepared")
                intent = self.intent()
                second = intent["generation"][1]
                if case == "foreign":
                    git(self.repo, "branch", second["branch"], self.feature_sha)
                    self.restart()
                    self.flow.engine.tick()
                else:
                    original = BaseGeneration.prepare

                    def fail(operation, card, saved, row):
                        if row["branch"] == second["branch"]:
                            raise FlowGitError("błąd przygotowania")
                        return original(operation, card, saved, row)

                    with mock.patch.object(BaseGeneration, "prepare", fail):
                        self.flow.engine.tick()
                self.assert_waiting()
                first = intent["generation"][0]
                self.assertTrue(Path(first["worktree"]).is_dir())
                self.assertEqual(git(self.repo, "rev-parse", first["branch"]).strip(), self.feature_sha)
                for row in intent["generation"]:
                    self.assertTrue(Path(row["oldWorktree"]).is_dir())
                    self.assertEqual(git(self.repo, "rev-parse", row["oldBranch"]).strip(), self.master)

    def test_dangling_foreign_symbolic_refs_are_never_followed_or_adopted(self):
        for n, field in enumerate(("branch", "ownerRef", "archiveRef")):
            with self.subTest(field=field):
                if n:
                    self.new_card()
                request = self.request(self.preview())
                self.crash(request, "intent", action="change-base")
                row = self.intent()["generation"][0]
                ref = "refs/heads/" + row[field] if field == "branch" else row[field]
                target = "refs/heads/foreign-target-" + str(n)
                git(self.repo, "symbolic-ref", ref, target)
                self.restart()
                self.flow.engine.tick()
                self.assert_waiting()
                self.assertEqual(git(self.repo, "symbolic-ref", ref).strip(), target)
                self.assertEqual(self.flow.engine.git.sha(self.repo, target), "")
                self.assertFalse(Path(row["worktree"]).exists())

    def test_projection_retry_never_repeats_git_or_version_and_all_old_apis_are_blocked(self):
        self.ask()
        self.flow.engine.journal.append(self.ident, "card.closed", commit=self.master, summary="bez wkładu")
        request = self.request(self.preview(step="ask"))
        engine = self.flow.engine
        with mock.patch.object(self.store, "project_card_base", side_effect=OSError("projekcja")):
            self.assertTrue(self.call("resolve_card_step", request)["changed"])
        self.restart()
        engine = self.flow.engine
        # Znacznik blokuje samodzielnie, także bez starego pytania i przy historycznym domknięciu.
        engine.journal.append(self.ident, "step.decision", step="ask", choice="answer", baseDecision="keep")
        with mock.patch.object(BaseGeneration, "prepare", side_effect=AssertionError("ponowny git")), \
                mock.patch.object(engine, "_advance_close", side_effect=AssertionError("domknięcie")), \
                mock.patch.object(engine, "_merge", side_effect=AssertionError("integracja")):
            engine.tick()
            engine.tick()
        for operation in (lambda: engine.start(self.ident, {"entry": "now", "executor": "Sonnet"}),
                          lambda: engine.resolve_step(self.ident, {"choice": "answer", "step": "ask", "baseDecision": "keep"}),
                          lambda: engine.resolve_review(self.ident, "accept"),
                          lambda: engine.enter(self.ident, "ask"),
                          lambda: engine.approve_plan(self.ident),
                          lambda: engine.edit_plan(self.ident, {"action": "return", "feedback": "zmiana"}),
                          lambda: engine.merge_card_to_main(self.ident)):
            with self.assertRaises(FlowError):
                operation()
        self.assert_active()
        engine.moved(self.ident, "done", "waiting", "manual-move")
        self.assertFalse(fold(self.events(self.ident)).card_finished)
        engine.tick()
        self.assert_active()
        # Zwykły cleanup nie może nawet przenieść plików w starym worktree.
        row = self.intent()["generation"][1]
        with self.assertRaises(FlowGitError):
            engine.git.remove_worktree(self.repo, row["oldBranch"], row["oldWorktree"])
        from tide_daemon.worktrees.scanner import WorktreeScanner
        scanner = WorktreeScanner(self.repo)
        items = scanner.scan_repo(self.repo)
        self.assertTrue(next(i for i in items if i.path == row["oldWorktree"]).locked)
        scanner.items = items
        removed = scanner.remove([row["oldWorktree"]], [], force=True, delete_unmerged=True)
        self.assertFalse(removed[0]["removed"])
        self.assertEqual(removed[0]["reason"], "locked")

    def test_activation_persisted_in_mirror_before_board_journal_projection_error(self):
        journal = self.flow.engine.journal
        restore = journal._restore
        failed = False

        def failing_restore(card):
            nonlocal failed
            if fold(journal.read(card)).base_validation_required and not failed:
                failed = True
                raise OSError("zapis pliku dziennika tablicy")
            return restore(card)

        request = self.request(self.preview())
        with mock.patch.object(journal, "_restore", failing_restore):
            reply = self.call("resolve_card_step", request)
        self.assertTrue(failed)
        self.assertTrue(reply["changed"], reply)
        self.restart()
        self.flow.engine.tick()
        self.assert_active()
        self.assertFalse(fold(self.events(self.ident)).base_generation_wait)

    def test_fold_rejects_partial_activation_atomically(self):
        request = self.request(self.preview())
        self.crash(request, "base-change.checkpoint", stage="prepared")
        intent = self.intent()
        original = self.events(self.ident)
        for rows in (intent["generation"][:1], [{**r, "base": self.master} for r in intent["generation"]]):
            event = {"seq": len(original) + 1, "type": "card.base-changed", "base": intent["newBase"],
                     "previousBaseVersion": 1, "baseVersion": 2, "generation": copy.deepcopy(rows),
                     "operationId": intent["key"], "validationRequired": True}
            st = fold(original + [event])
            self.assertEqual(st.base_version, 1)
            self.assertEqual(ticket_of(st)["cardBranch"], self.started["cardBranch"])

    def test_text_and_option_titles_never_execute_change(self):
        self.ask()
        before = git(self.repo, "branch", "--format=%(refname)")
        for request in ({"choice": "answer", "step": "ask", "answer": "change-base refs/heads/feature/start"},
                        {"choice": "change-base", "baseRef": "refs/heads/feature/start"},
                        {"choice": "answer", "step": "ask", "baseDecision": "change"}):
            self.call("resolve_card_step", request)
        self.assertFalse(self.of(self.ident, "card.base-changed"))
        self.assertEqual(git(self.repo, "branch", "--format=%(refname)"), before)
