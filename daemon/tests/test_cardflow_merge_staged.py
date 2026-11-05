"""PT-207: publiczne Preview/Merge zachowują wpisy indeksu i bajty lokalnej pracy."""
import os
import unittest
from pathlib import Path
from unittest import mock

from tide_daemon.cardflow import gitops
from tide_daemon.cardflow.mergeop import MergeWorktree
from tide_daemon.tasks.stepstop import StepGit
from tide_daemon.worktrees.git import Git, Result

from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git
from . import test_cardflow_merge_confirm as confirm
from . import test_cardflow_merge_recovery as recovery
from .test_cardflow_merge_preview import fingerprint
from .test_cardflow_merge_recovery import PowerLoss


class StagedMergeTest(recovery.DurableMergeFixture, FlowInProcess):
    call = confirm.ConfirmMergeTest.call
    preview = confirm.ConfirmMergeTest.preview
    request = confirm.ConfirmMergeTest.request
    assert_refused = confirm.ConfirmMergeTest.assert_refused
    assert_waiting = recovery.DurableMergeTest.assert_waiting

    def stage_work(self):
        Path(self.repo, "local.txt").write_bytes(b"staged\x00\xff\r\n")
        Path(self.repo, "executable").write_bytes(b"#!/bin/sh\nexit 0\n")
        os.chmod(Path(self.repo, "executable"), 0o755)
        os.symlink("local.txt", Path(self.repo, "staged-link"))
        git(self.repo, "add", "local.txt", "executable", "staged-link")
        Path(self.repo, "local.txt").write_bytes(b"unstaged\x00\xfe\r\n")
        self.user_paths = ["local.txt", "executable", "staged-link"]
        return self.user_work()

    def user_work(self):
        files = {}
        for name in self.user_paths:
            path = Path(self.repo, name)
            files[name] = ((os.lstat(path).st_mode, os.readlink(path) if path.is_symlink() else path.read_bytes())
                           if path.exists() or path.is_symlink() else None)
        return {"files": files,
                "index": git(self.repo, "ls-files", "--stage", "-z", "--", *self.user_paths),
                "delta": git(self.repo, "diff", "--cached", "--raw", "--no-abbrev", "--no-renames", "-z",
                             "HEAD", "--", *self.user_paths)}

    def test_staged_and_unstaged_bytes_modes_and_delta_survive_public_merge(self):
        before = self.stage_work()
        self.write(self.repo, "notes.txt", "nieśledzone\n")
        p = self.preview()
        self.assertTrue(p["canMerge"], p)
        self.assertEqual(p["localCollisionCount"], 0)
        r = self.call("merge_card", self.request(p))
        self.assertTrue(r["ok"], r)
        self.assert_success()
        self.assertEqual(self.user_work(), before)
        self.assertEqual(Path(self.repo, "notes.txt").read_bytes(), b"nie\xc5\x9bledzone\n")
        self.assertEqual(git(self.repo, "show", "HEAD:local.txt"), "baza lokalnej pracy\n")
        self.assertEqual(git(self.repo, "ls-tree", "HEAD", "--", "executable", "staged-link"), "")

    def test_unrelated_staged_rename_and_deletion_keep_both_levels(self):
        git(self.repo, "mv", "local.txt", "renamed\tlocal\n.txt")
        git(self.repo, "rm", "CLAUDE.md")
        self.write(self.repo, "local.txt", "nowy plik pod starą nazwą\n")
        Path(self.repo, "renamed\tlocal\n.txt").write_bytes(b"unstaged\x00\xff\n")
        self.user_paths = ["local.txt", "renamed\tlocal\n.txt", "CLAUDE.md"]
        before = self.user_work()
        p = self.preview()
        self.assertTrue(p["canMerge"], p)
        self.assertTrue(self.call("merge_card", self.request(p))["ok"])
        self.assertEqual(self.user_work(), before)

    def test_split_index_keeps_staged_and_unstaged(self):
        user = self.stage_work()
        git(self.repo, "config", "core.splitIndex", "true")
        git(self.repo, "update-index", "--split-index")
        result = self.call("merge_card", self.request())
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.user_work(), user)

    @unittest.skipIf(os.geteuid() == 0, "root omija prawa zapisu katalogu gita")
    def test_split_index_with_stepgit_environment_preserves_read_only_checks(self):
        case = self

        class ReadOnlyObjectsGit:
            def __init__(self, *args, objects_only=False):
                self.sandboxed = StepGit(*args, objects_only=objects_only)
                self.objects_only, self.git_dir, self.worktree = objects_only, args[1], args[2]

            def run(self, *args, timeout=120, index_dir=None):
                runner = Git()
                argv = self.sandboxed.sandbox(index_dir)
                # Prawdziwe opcje i środowisko StepGit; mounty symulujemy prawami katalogu.
                runner.env = {argv[i + 1]: argv[i + 2] for i, arg in enumerate(argv) if arg == "--setenv"}
                if index_dir is not None:
                    runner.env["GIT_INDEX_FILE"] = os.path.join(index_dir, "index")
                index = Path(self.git_dir, "index")
                previous = index.read_bytes() if args[0] == "status" else None
                mode = os.stat(self.git_dir).st_mode
                if self.objects_only:
                    os.chmod(self.git_dir, 0o555)
                try:
                    result = runner.run(self.worktree, *self.sandboxed.opts, *args, timeout=timeout)
                    if previous is not None:
                        case.assertEqual(previous, index.read_bytes(), "odczyt status zmienił indeks źródła")
                    return result
                finally:
                    os.chmod(self.git_dir, mode)

        with mock.patch.object(gitops, "sandbox_available", return_value=True), \
                mock.patch.object(gitops, "StepGit", ReadOnlyObjectsGit):
            self.test_split_index_keeps_staged_and_unstaged()

    def test_linked_target_worktree_preserves_both_levels(self):
        target_tree = os.path.join(self.base, "ws", "target-checkout")
        git(self.repo, "checkout", "--detach", "-q")
        git(self.repo, "worktree", "add", target_tree, self.target)
        self.repo = target_tree
        user = self.stage_work()
        p = self.preview()
        self.assertEqual(p["targetWorktree"], target_tree)
        self.assertTrue(self.call("merge_card", self.request(p))["ok"])
        self.assertEqual(self.user_work(), user)

    def test_already_merged_preserves_staged_and_unstaged_without_new_commit(self):
        git(self.repo, "merge", "--ff-only", self.source)
        user = self.stage_work()
        before = self.sha(self.target)
        r = self.call("merge_card", self.request())
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["already"], r)
        self.assertEqual(self.sha(self.target), before)
        self.assertEqual(self.user_work(), user)

    def test_edits_during_preparation_are_stale_without_mutating_user_index(self):
        self.stage_work()
        req = self.request()
        original = MergeWorktree.prepare
        after_edit = []

        def prepare(worktree, *args, **kwargs):
            result = original(worktree, *args, **kwargs)
            Path(self.repo, "local.txt").write_bytes(b"new work during preparation\x00")
            after_edit.append(fingerprint(self.repo))
            return result

        with mock.patch.object(MergeWorktree, "prepare", prepare):
            r = self.call("merge_card", req)
        self.assertEqual(r.get("error"), "stale-preview", r)
        self.assertEqual(fingerprint(self.repo), after_edit[0])
        self.assertFalse(any(e.get("action") == "main-merge" for e in self.flow.engine.journal.read(self.card_id)))

    def test_staged_and_unstaged_changes_after_preview_are_stale_before_intent(self):
        self.stage_work()
        mutations = [
            lambda: Path(self.repo, "local.txt").write_bytes(b"new unstaged\x00"),
            lambda: git(self.repo, "add", "local.txt"),
            lambda: git(self.repo, "update-index", "--chmod=-x", "executable"),
            lambda: git(self.repo, "mv", "executable", "renamed-executable"),
            lambda: git(self.repo, "rm", "-f", "staged-link"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                req = self.request()
                mutate()
                self.assert_refused(req)
        self.assertFalse(any(e.get("action") == "main-merge" for e in self.flow.engine.journal.read(self.card_id)))

    def assert_collision(self):
        p = self.preview()
        self.assertFalse(p["canMerge"], p)
        self.assertIn("local-collision", [r["reason"] for r in p["blockedReasons"]])
        self.assert_refused(self.request(p), "local-collision")
        self.assertFalse(any(e.get("action") == "main-merge" for e in self.flow.engine.journal.read(self.card_id)))

    def test_incoming_rename_blocks_staged_old_and_untracked_new_path(self):
        git(self.source_tree, "mv", "local.txt", "new-name.txt")
        self.commit(self.source_tree, "rename source file")
        self.write(self.repo, "local.txt", "staged\n")
        git(self.repo, "add", "local.txt")
        self.write(self.repo, "new-name.txt", "untracked\n")
        self.assert_collision()

    def test_local_rename_blocks_either_endpoint(self):
        self.write(self.source_tree, "local.txt", "source\n")
        self.commit(self.source_tree, "change rename source")
        git(self.repo, "mv", "local.txt", "result.txt")
        p = self.preview()
        hits = p["localCollisions"]["staged"][0]["mergePaths"]
        self.assertEqual(hits, ["local.txt", "result.txt"])
        self.assert_collision()

    def test_local_deletion_blocks_incoming_change(self):
        self.write(self.source_tree, "local.txt", "source\n")
        self.commit(self.source_tree, "modify source")
        git(self.repo, "rm", "local.txt")
        self.assert_collision()

    def test_incoming_deletion_blocks_staged_and_unstaged(self):
        git(self.source_tree, "rm", "local.txt")
        self.commit(self.source_tree, "delete source")
        self.write(self.repo, "local.txt", "staged\n")
        git(self.repo, "add", "local.txt")
        self.write(self.repo, "local.txt", "unstaged\n")
        self.assert_collision()

    def test_untracked_directory_blocks_incoming_file(self):
        Path(self.repo, "result.txt").mkdir()
        self.write(self.repo, "result.txt/notes", "untracked\n")
        self.assert_collision()

    def test_untracked_file_blocks_incoming_directory(self):
        Path(self.source_tree, "directory").mkdir()
        self.write(self.source_tree, "directory/result", "source\n")
        self.commit(self.source_tree, "add directory")
        self.write(self.repo, "directory", "untracked file\n")
        self.assert_collision()

    def test_staged_directory_blocks_incoming_file(self):
        Path(self.repo, "result.txt").mkdir()
        self.write(self.repo, "result.txt/notes", "staged\n")
        git(self.repo, "add", "result.txt")
        self.assert_collision()

    def test_materialization_failure_before_write_preserves_exact_work_and_index(self):
        self.materialization_failure("before")

    def test_materialization_failure_after_index_write_recovers_same_operation(self):
        self.materialization_failure("after")

    def test_partial_files_with_old_index_wait_without_overwriting_new_work(self):
        self.materialization_failure("partial")

    def materialization_failure(self, when):
        user = self.stage_work()
        req = self.request()
        before = fingerprint(self.repo)
        original = gitops.StepGit.run

        def fail(runner, *args, **kwargs):
            if args[:3] == ("read-tree", "-m", "-u") and not kwargs.get("index_dir"):
                if when == "after":
                    self.assertTrue(original(runner, *args, **kwargs).ok)
                if when == "partial":
                    self.write(self.repo, "result.txt", "wynik karty\n")
                return Result(1, "", "injected materialization failure")
            return original(runner, *args, **kwargs)

        with mock.patch.object(gitops.StepGit, "run", fail):
            r = self.call("merge_card", req)
        self.assertFalse(r["ok"], r)
        intent = self.intent()
        self.assert_waiting(intent)
        self.assertEqual(self.user_work(), user)
        self.assertEqual(self.sha(self.target), self.before)
        if when == "before":
            self.assertEqual(fingerprint(self.repo), before)
        if when == "partial":
            self.write(self.repo, "result.txt", "nowa cudza praca\n")
            before = fingerprint(self.repo)
        self.restart_stopped()
        r = self.call("merge_card", req)
        if when == "partial":
            self.assertFalse(r["ok"], r)
            self.assert_waiting(intent)
            self.assertEqual(fingerprint(self.repo), before)
        else:
            self.assertTrue(r["ok"], r)
            self.assert_success()
        self.assertEqual(self.user_work(), user)

    def test_new_foreign_staged_after_materialization_waits_with_original_work_intact(self):
        user = self.stage_work()
        req = self.request()
        journal = self.flow.engine.journal
        original = journal.append

        def append(card, event, **data):
            result = original(card, event, **data)
            if event == "main-merge.checkpoint" and data.get("stage") == "materialized":
                raise PowerLoss()
            return result

        with mock.patch.object(journal, "append", append), self.assertRaises(PowerLoss):
            self.flow.engine.merge_card(self.card_id, req)
        self.write(self.repo, "foreign.txt", "new staged\n")
        git(self.repo, "add", "foreign.txt")
        before = fingerprint(self.repo)
        self.restart_stopped()
        self.assertFalse(self.call("merge_card", req)["ok"])
        self.assert_waiting(self.intent())
        self.assertEqual(self.user_work(), user)
        self.assertEqual(fingerprint(self.repo), before)

    def abandon_materialized_with_staged(self):
        user = self.stage_work()
        self.crash_after("main-merge.checkpoint", stage="materialized")
        self.flow.engine.moved(self.card_id, "todo", "waiting", "revision")
        return user

    def test_new_close_inherits_materialization_without_consuming_user_staged(self):
        user = self.abandon_materialized_with_staged()
        self.write(self.source_tree, "later.txt", "revision\n")
        self.commit(self.source_tree, "revise card")
        self.flow.engine.journal.append(self.card_id, "card.closed", summary="Po poprawkach")
        self.assertTrue(self.merge()["ok"])
        self.assertEqual(self.user_work(), user)
        self.assertEqual(Path(self.repo, "later.txt").read_bytes(), b"revision\n")

    def test_inherited_materialization_still_blocks_new_overlap_with_user_staged(self):
        self.abandon_materialized_with_staged()
        # Nawet identyczny staged blob nie może zostać po cichu skonsumowany przez nowy HEAD.
        Path(self.repo, "local.txt").write_bytes(b"staged\x00\xff\r\n")
        user = self.user_work()
        Path(self.source_tree, "local.txt").write_bytes(b"staged\x00\xff\r\n")
        self.commit(self.source_tree, "overlap local staged")
        self.flow.engine.journal.append(self.card_id, "card.closed", summary="Po poprawkach")
        before = fingerprint(self.repo)
        r = self.merge()
        self.assertFalse(r["ok"], r)
        self.assertEqual(self.user_work(), user)
        self.assertEqual(fingerprint(self.repo), before)


@unittest.skipUnless(gitops.sandbox_available(), "brak zagnieżdżonej piaskownicy gita")
class SandboxedStagedMergeTest(StagedMergeTest):
    local_git = False
