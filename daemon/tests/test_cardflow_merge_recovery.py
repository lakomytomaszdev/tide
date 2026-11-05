"""PT-212: publiczne MergeCardToMain, prawdziwy git i restart między trwałymi granicami."""
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tide_daemon.cardflow import gitops
from tide_daemon.cardflow.state import fold

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git
from .test_cardflow_gitops import LocalStepGit
from . import test_cardflow_close as legacy_closing


class PowerLoss(BaseException):
    """Nagłe przerwanie, bez obsługi błędu i bez rollbacku procesu usługi."""


class DurableMergeTest(FlowInProcess):
    local_git = True
    def make_flow(self):
        # Sterujemy tickiem jawnie: restart nie może wyprzedzić miejsca wstrzyknięcia awarii w teście.
        from tide_daemon.cardflow_monitor import CardFlowMonitor
        with mock.patch.object(CardFlowMonitor, "start"):
            return super().make_flow()

    def setUp(self):
        super().setUp()
        self.flow.stop()

        class ObjectGit(LocalStepGit):
            def __init__(self, *args, objects_only=False):
                super().__init__(*args)

        for patch in ((mock.patch.object(gitops, "sandbox_available", return_value=True),
                       mock.patch.object(gitops, "StepGit", ObjectGit)) if self.local_git else ()):
            patch.start()
            self.addCleanup(patch.stop)
        self.target = "release/full-target"
        git(self.repo, "branch", "-m", self.target)
        git(self.repo, "config", "tide.mainBranch", self.target)
        self.write(self.repo, "local.txt", "baza lokalnej pracy\n")
        self.commit(self.repo, "local baseline")
        self.before = self.sha(self.target)
        self.card_id = self.card()
        self.source = "card/k-1-durable"
        self.source_tree = os.path.join(self.base, "ws", "source")
        git(self.repo, "worktree", "add", "-qb", self.source, self.source_tree)
        self.write(self.source_tree, "result.txt", "wynik karty\n")
        self.source_commit = self.commit(self.source_tree, "card change")
        self.seed()

    @staticmethod
    def write(root, path, text):
        Path(root, path).write_text(text, encoding="utf-8")

    @staticmethod
    def commit(root, message):
        git(root, "add", "-A")
        git(root, "commit", "-qm", message)
        return git(root, "rev-parse", "HEAD").strip()

    def sha(self, ref):
        return git(self.repo, "rev-parse", ref).strip()

    def seed(self):
        journal = self.flow.engine.journal
        journal.append(self.card_id, "card.started", entry="now", executor="Sonnet", repo=self.repo,
                       baseRef="refs/heads/" + self.target, baseBranch=self.target,
                       baseCommit=self.before, baseVersion=1)
        journal.append(self.card_id, "result", action="card-branch", key="card", ok=True,
                       branch=self.source, worktree=self.source_tree)
        journal.append(self.card_id, "card.closed", summary="Gotowe")

    def merge(self):
        box = []
        self.board.merge_card_to_main(self.card_id, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20), "MergeCardToMain bez odpowiedzi")
        return json.loads(box[0])

    def crash_after(self, kind, *, stage=None, action=None):
        journal = self.flow.engine.journal
        original = journal.append

        def append(card, event, **data):
            result = original(card, event, **data)
            if event == kind and (stage is None or data.get("stage") == stage) and (
                    action is None or data.get("action") == action):
                raise PowerLoss()
            return result

        # Ta sama metoda silnika co publiczne /Board, bez wątku monitora połykającego wyjątki.
        with mock.patch.object(journal, "append", append), self.assertRaises(PowerLoss):
            self.flow.engine.merge_card_to_main(self.card_id)

    def restart_stopped(self):
        self.flow.stop()
        self.flow = self.make_flow()
        self.flow.stop()

    def intent(self):
        return next(e for e in self.flow.engine.journal.read(self.card_id)
                    if e["type"] == "intent" and e.get("action") == "main-merge")

    def assert_success(self):
        events = self.flow.engine.journal.read(self.card_id)
        intent = self.intent()
        results = [e for e in events if e["type"] == "result" and e.get("action") == "main-merge"]
        self.assertEqual(len([e for e in events if e["type"] == "intent" and e.get("action") == "main-merge"]), 1)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["ok"])
        for key in ("targetRef", "targetBranch", "sourceCommit", "targetCommitBefore", "resultCommit", "baseVersion", "operationId"):
            self.assertEqual(results[0][key], intent[key])
        self.assertEqual(intent["baseVersion"], 1)
        self.assertEqual(intent["targetRef"], "refs/heads/" + self.target)
        self.assertEqual(intent["sourceCommit"], self.source_commit)
        self.assertEqual(intent["targetCommitBefore"], self.before)
        self.assertEqual(self.sha(self.target), intent["resultCommit"])
        self.assertEqual(git(self.repo, "rev-list", "--merges", self.before + ".." + self.target).splitlines(),
                         [intent["resultCommit"]])
        self.assertEqual(len([e for e in events if e["type"] == "card.main-merged"]), 1)
        self.assertEqual(len([e for e in events if e["type"] == "card.finished"]), 1)
        self.assertEqual({e["stage"] for e in events if e["type"] == "main-merge.checkpoint"},
                         {"prepared", "materialized", "ref-moved", "result-saved", "projected"})
        self.assertEqual(Path(intent["targetWorktree"], "result.txt").read_text(), "wynik karty\n")
        self.assertEqual(self.store.load().cards[int(self.card_id[2:])].column, "done")
        self.assertEqual(self.shown(self.card_id)["boardColumn"], "done")
        self.assertEqual(git(intent["targetWorktree"], "write-tree").strip(), intent["resultTree"])

    def test_intent_before_any_mutation_and_public_retry_after_restart(self):
        old_index = git(self.repo, "write-tree")
        self.crash_after("intent", action="main-merge")
        intent = self.intent()
        self.assertEqual(self.sha(self.target), self.before)
        self.assertEqual(git(self.repo, "write-tree"), old_index)
        self.assertFalse(Path(self.repo, "result.txt").exists())
        self.assertFalse(self.flow.engine.git.sha(self.repo, intent["resultCommit"]))
        self.restart_stopped()
        r = self.merge()
        self.assertEqual(r, {"ok": True, "id": self.card_id, "main": self.target, "already": False})
        self.assert_success()
        self.assertTrue(self.merge()["already"])
        self.assert_success()

    def test_materialized_before_ref_restarts_without_another_commit(self):
        self.crash_after("main-merge.checkpoint", stage="materialized")
        self.assertEqual(self.sha(self.target), self.before)
        self.assertEqual(git(self.repo, "write-tree").strip(), self.intent()["resultTree"])
        self.assertTrue(Path(self.repo, "result.txt").exists())
        self.restart_stopped()
        self.assertTrue(self.merge()["ok"])
        self.assert_success()
        self.assertEqual(git(self.repo, "status", "--porcelain").strip(), "")

    def test_power_loss_after_git_materialization_before_its_checkpoint(self):
        original = gitops.StepGit.run

        def run(runner, *args, **kwargs):
            result = original(runner, *args, **kwargs)
            if args[:3] == ("read-tree", "-m", "-u"):
                raise PowerLoss()
            return result

        with mock.patch.object(gitops.StepGit, "run", run), self.assertRaises(PowerLoss):
            self.flow.engine.merge_card_to_main(self.card_id)
        self.restart_stopped()
        self.assertTrue(self.merge()["ok"])
        self.assert_success()

    def test_power_loss_after_git_ref_update_before_its_checkpoint(self):
        original = self.flow.engine.git.run

        def run(cwd, *args, **kwargs):
            result = original(cwd, *args, **kwargs)
            if args[0] == "update-ref":
                raise PowerLoss()
            return result

        with mock.patch.object(self.flow.engine.git, "run", run), self.assertRaises(PowerLoss):
            self.flow.engine.merge_card_to_main(self.card_id)
        self.restart_stopped()
        self.assertTrue(self.merge()["ok"])
        self.assert_success()

    def test_ref_moved_before_result_is_recovered_by_tick_then_public_retry(self):
        self.crash_after("main-merge.checkpoint", stage="ref-moved")
        self.assertEqual(self.sha(self.target), self.intent()["resultCommit"])
        self.assertFalse(fold(self.flow.engine.journal.read(self.card_id)).main_merged)
        self.restart_stopped()
        self.flow.engine.tick()
        self.assert_success()
        self.assertTrue(self.merge()["already"])
        self.assert_success()

    def test_result_before_success_projection_recovers_without_duplicate_events(self):
        self.crash_after("result", action="main-merge")
        self.assertIn("scalenie karty", fold(self.flow.engine.journal.read(self.card_id)).busy_reason())
        self.restart_stopped()
        self.flow.engine.tick()
        self.assertTrue(self.merge()["already"])
        self.assert_success()

    def test_success_event_before_column_projection_recovers_once(self):
        self.crash_after("card.main-merged")
        self.restart_stopped()
        self.assertTrue(self.merge()["already"])
        self.assert_success()

    def test_finished_before_last_checkpoint_does_not_duplicate_completion(self):
        self.crash_after("card.finished")
        self.restart_stopped()
        self.flow.engine.tick()
        self.assertTrue(self.merge()["already"])
        self.assert_success()

    def test_column_projection_before_finished_recovers_once(self):
        original = self.store.move_card

        def move(*args, **kwargs):
            result = original(*args, **kwargs)
            raise PowerLoss()

        with mock.patch.object(self.store, "move_card", move), self.assertRaises(PowerLoss):
            self.flow.engine.merge_card_to_main(self.card_id)
        self.assertEqual(self.store.load().cards[1].column, "done")
        self.restart_stopped()
        self.flow.engine.tick()
        self.assert_success()

    def test_foreign_work_after_column_write_before_finished_is_visible_as_waiting(self):
        original = self.store.move_card

        def move(*args, **kwargs):
            original(*args, **kwargs)
            raise PowerLoss()

        with mock.patch.object(self.store, "move_card", move), self.assertRaises(PowerLoss):
            self.flow.engine.merge_card_to_main(self.card_id)
        intent = self.intent()
        self.write(self.repo, "local.txt", "nowa praca po zmianie kolumny\n")
        self.restart_stopped()
        self.flow.engine.tick()
        self.assert_waiting(intent)
        self.assertEqual(Path(self.repo, "local.txt").read_text(), "nowa praca po zmianie kolumny\n")

    def test_foreign_ref_after_materialization_waits_without_rollback_or_second_id(self):
        self.crash_after("main-merge.checkpoint", stage="materialized")
        intent = self.intent()
        external = git(self.repo, "commit-tree", self.before + "^{tree}", "-p", self.before, "-m", "external").strip()
        git(self.repo, "update-ref", intent["targetRef"], external, self.before)
        index = git(self.repo, "write-tree")
        contents = Path(self.repo, "result.txt").read_bytes()
        self.restart_stopped()
        for _ in range(2):
            r = self.merge()
            self.assertFalse(r["ok"], r)
            self.flow.engine.tick()
        self.assert_waiting(intent)
        self.assertEqual(self.sha(self.target), external)
        self.assertEqual(git(self.repo, "write-tree"), index)
        self.assertEqual(Path(self.repo, "result.txt").read_bytes(), contents)

    def assert_waiting(self, intent):
        events = self.flow.engine.journal.read(self.card_id)
        self.assertEqual([e["key"] for e in events if e["type"] == "intent" and e.get("action") == "main-merge"], [intent["key"]])
        self.assertFalse(any(e["type"] == "card.finished" for e in events))
        self.assertEqual(self.shown(self.card_id)["boardColumn"], "waiting")
        self.assertIn("Scalenie czeka", self.shown(self.card_id)["derived"]["note"])

    def test_new_local_edits_after_materialization_wait_and_keep_every_byte(self):
        self.crash_after("main-merge.checkpoint", stage="materialized")
        intent = self.intent()
        self.write(self.repo, "result.txt", "cudza praca po awarii\n")
        self.write(self.repo, "untracked.txt", "nowa praca\n")
        self.restart_stopped()
        self.assertFalse(self.merge()["ok"])
        self.flow.engine.tick()
        self.assert_waiting(intent)
        self.assertEqual(self.sha(self.target), self.before)
        self.assertEqual(Path(self.repo, "result.txt").read_text(), "cudza praca po awarii\n")
        self.assertEqual(Path(self.repo, "untracked.txt").read_text(), "nowa praca\n")

    def test_new_unrelated_untracked_content_after_ref_move_also_waits(self):
        self.write(self.repo, "notes.txt", "wcześniejsza notatka\n")
        self.crash_after("main-merge.checkpoint", stage="ref-moved")
        intent = self.intent()
        self.write(self.repo, "notes.txt", "nowa treść tej samej notatki\n")
        self.restart_stopped()
        self.flow.engine.tick()
        self.assert_waiting(intent)
        self.assertEqual(self.sha(self.target), intent["resultCommit"])
        self.assertEqual(Path(self.repo, "notes.txt").read_text(), "nowa treść tej samej notatki\n")

    def test_new_staged_work_after_intent_waits_without_changing_index(self):
        self.crash_after("intent", action="main-merge")
        intent = self.intent()
        self.write(self.repo, "local.txt", "nowa praca staged\n")
        git(self.repo, "add", "local.txt")
        index = git(self.repo, "write-tree")
        self.restart_stopped()
        self.assertFalse(self.merge()["ok"])
        self.assert_waiting(intent)
        self.assertEqual(git(self.repo, "write-tree"), index)
        self.assertEqual(Path(self.repo, "local.txt").read_text(), "nowa praca staged\n")
        self.assertEqual(self.sha(self.target), self.before)

    def test_initial_staged_work_is_refused_without_expanding_supported_scope(self):
        self.write(self.repo, "local.txt", "wcześniejsza praca staged\n")
        git(self.repo, "add", "local.txt")
        index = git(self.repo, "write-tree")
        self.assertFalse(self.merge()["ok"])
        self.assertEqual(git(self.repo, "write-tree"), index)
        self.assertEqual(self.sha(self.target), self.before)
        self.assertFalse(any(e.get("action") == "main-merge" for e in self.flow.engine.journal.read(self.card_id)))

    def test_source_change_during_recovery_does_not_merge_another_commit(self):
        self.crash_after("intent", action="main-merge")
        intent = self.intent()
        self.write(self.source_tree, "later.txt", "późniejsza praca\n")
        self.commit(self.source_tree, "later source")
        self.restart_stopped()
        self.assertFalse(self.merge()["ok"])
        self.assert_waiting(intent)
        self.assertEqual(self.sha(self.target), self.before)
        self.assertFalse(Path(self.repo, "later.txt").exists())

    def test_failure_of_ref_update_leaves_materialized_state_for_the_same_operation(self):
        from tide_daemon.worktrees.git import Result
        original = self.flow.engine.git.run

        def fail(cwd, *args, **kwargs):
            if args[0] == "update-ref":
                return Result(1, "", "injected failure")
            return original(cwd, *args, **kwargs)

        with mock.patch.object(self.flow.engine.git, "run", fail):
            self.assertFalse(self.merge()["ok"])
        intent = self.intent()
        self.assert_waiting(intent)
        self.assertEqual(self.sha(self.target), self.before)
        self.assertEqual(git(self.repo, "write-tree").strip(), intent["resultTree"])
        self.restart_stopped()
        self.assertTrue(self.merge()["ok"])
        self.assert_success()

    def test_k43_unrelated_work_survives_materialization_and_restart(self):
        self.write(self.repo, "local.txt", "niezacommitowana praca użytkownika\n")
        self.write(self.repo, "notes.txt", "notatka\n")
        os.symlink("brak-celu", os.path.join(self.repo, "local-link"))
        self.crash_after("main-merge.checkpoint", stage="materialized")
        self.restart_stopped()
        self.assertTrue(self.merge()["ok"])
        self.assert_success()
        self.assertEqual(Path(self.repo, "local.txt").read_text(), "niezacommitowana praca użytkownika\n")
        self.assertEqual(Path(self.repo, "notes.txt").read_text(), "notatka\n")
        self.assertEqual(os.readlink(os.path.join(self.repo, "local-link")), "brak-celu")
        self.assertIn(" M local.txt", git(self.repo, "status", "--porcelain"))

    def test_unrelated_nested_untracked_repository_is_preserved(self):
        nested = Path(self.repo, "nested")
        nested.mkdir()
        git(str(nested), "init", "-q")
        self.write(nested, "notes.txt", "praca w osobnym repozytorium\n")
        self.assertTrue(self.merge()["ok"])
        self.assert_success()
        self.assertEqual(Path(nested, "notes.txt").read_text(), "praca w osobnym repozytorium\n")
        self.assertTrue(Path(nested, ".git").is_dir())

    def test_conflict_and_local_collisions_refuse_before_intent(self):
        self.write(self.repo, "result.txt", "nieśledzona praca\n")
        self.assertFalse(self.merge()["ok"])
        self.assertEqual(Path(self.repo, "result.txt").read_text(), "nieśledzona praca\n")
        self.commit(self.repo, "conflicting target")
        r = self.merge()
        self.assertFalse(r["ok"], r)
        self.assertIn("Konflikty", r["message"])
        self.assertFalse(any(e.get("action") == "main-merge" for e in self.flow.engine.journal.read(self.card_id)))

    def test_legacy_missing_checkout_preserves_user_branch_and_local_work(self):
        git(self.repo, "checkout", "-qb", "feature/user")
        self.write(self.repo, "local.txt", "praca w innym checkoutcie\n")
        self.assertTrue(self.merge()["ok"])
        self.assert_success()
        self.assertEqual(git(self.repo, "branch", "--show-current").strip(), "feature/user")
        self.assertEqual(Path(self.repo, "local.txt").read_text(), "praca w innym checkoutcie\n")

    def test_ignored_file_collision_is_refused_without_losing_content(self):
        git_dir = Path(self.repo, ".git", "info", "exclude")
        git_dir.write_text("result.txt\n", encoding="utf-8")
        self.write(self.repo, "result.txt", "ignorowana lokalna praca\n")
        self.assertFalse(self.merge()["ok"])
        self.assertEqual(Path(self.repo, "result.txt").read_text(), "ignorowana lokalna praca\n")
        self.assertEqual(self.sha(self.target), self.before)

    def test_already_merged_source_records_one_success_without_a_merge_commit(self):
        git(self.repo, "merge", "--ff-only", self.source)
        head = self.sha(self.target)
        self.assertTrue(self.merge()["ok"])
        self.restart_stopped()
        self.assertTrue(self.merge()["already"])
        self.assertEqual(self.sha(self.target), head)
        self.assertEqual(self.intent()["resultCommit"], head)
        self.assertTrue(self.intent()["already"])
        events = self.flow.engine.journal.read(self.card_id)
        self.assertEqual(len([e for e in events if e["type"] == "card.main-merged"]), 1)
        self.assertEqual(len([e for e in events if e["type"] == "card.finished"]), 1)

    def test_no_explicit_merge_does_not_create_an_intent_on_tick(self):
        self.flow.engine.tick()
        self.assertEqual(self.sha(self.target), self.before)
        self.assertFalse(any(e.get("action") == "main-merge" for e in self.flow.engine.journal.read(self.card_id)))


@unittest.skipUnless(gitops.sandbox_available(), "brak zagnieżdżonej piaskownicy gita")
class SandboxedDurableMergeTest(DurableMergeTest):
    local_git = False


class LegacyMainMergeTest(FlowInProcess):
    """Niezmienione scenariusze PT-70/K-43 na istniejących atrapach i prawdziwym repo."""
    test_manual_summary_then_explicit_main_merge = legacy_closing.CardClosingTest.test_manual_summary_then_explicit_main_merge
    test_main_merge_opens_its_own_worktree_when_main_is_not_checked_out = legacy_closing.CardClosingTest.test_main_merge_opens_its_own_worktree_when_main_is_not_checked_out
    test_main_merge_refuses_dirty_checkout_without_losing_changes = legacy_closing.CardClosingTest.test_main_merge_refuses_dirty_checkout_without_losing_changes
    test_main_merge_keeps_unrelated_uncommitted_work = legacy_closing.CardClosingTest.test_main_merge_keeps_unrelated_uncommitted_work
    test_return_to_todo_revokes_main_merge_until_new_close = legacy_closing.CardClosingTest.test_return_to_todo_revokes_main_merge_until_new_close

    def setUp(self):
        class ObjectGit(LocalStepGit):
            def __init__(self, *args, objects_only=False):
                super().__init__(*args)

        for patch in (mock.patch.object(gitops, "sandbox_available", return_value=True),
                      mock.patch.object(gitops, "StepGit", ObjectGit)):
            patch.start()
            self.addCleanup(patch.stop)
        super().setUp()
