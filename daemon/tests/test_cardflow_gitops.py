"""Scalenie ticketu: zgodność gałęzi i worktree karty oraz granica piaskownicy."""
import os
import shutil
import subprocess
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import mock

from tide_daemon.cardflow import gitops
from tide_daemon.tasks.stepstop import sandbox_available
from tide_daemon.worktrees.git import Git, Result

from .test_cardflow_dbus import test_root


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, text=True).stdout.strip()


class LocalStepGit:
    """Adapter testowy dla scenariuszy awarii, kiedy bwrap nie działa w procesie testu."""

    def __init__(self, repo, git_dir, worktree, step_dir):
        self.git = Git()
        self.worktree = worktree

    def run(self, *args, timeout=120):
        return self.git.run(self.worktree, *gitops.HARDENED, *args, timeout=timeout)


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.base = test_root()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.repo = os.path.join(self.base, "project")
        os.mkdir(self.repo)
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Tester")
        git(self.repo, "config", "user.email", "tester@example.invalid")
        self.write(self.repo, "base.txt", "base\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "base")
        self.card = os.path.join(self.base, "card")
        self.ticket = os.path.join(self.base, "ticket")
        git(self.repo, "worktree", "add", "-qb", "card/test", self.card)
        git(self.repo, "worktree", "add", "-qb", "ticket/test", self.ticket)
        self.flow = gitops.FlowGit()

    @staticmethod
    def write(root, name, value):
        with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
            fh.write(value)

    def ticket_commit(self):
        self.write(self.ticket, "ticket.txt", "ticket\n")
        git(self.ticket, "add", ".")
        git(self.ticket, "commit", "-qm", "ticket")

    def merge(self):
        return self.flow.merge(self.repo, "card/test", "ticket/test", self.card, "Merge ticket",
                               expected_tip=git(self.repo, "rev-parse", "ticket/test"))

    def test_two_ticket_merges_share_one_repo_lock(self):
        self.ticket_commit()
        second = os.path.join(self.base, "ticket-second")
        git(self.repo, "worktree", "add", "-qb", "ticket/second", second)
        self.write(second, "second.txt", "second\n")
        git(second, "add", ".")
        git(second, "commit", "-qm", "second")
        barrier = Barrier(2)

        def merge(branch):
            barrier.wait(timeout=5)
            return self.flow.merge(self.repo, "card/test", branch, self.card, f"Merge {branch}",
                                   expected_tip=git(self.repo, "rev-parse", branch))

        with mock.patch.object(gitops, "sandbox_available", return_value=True, create=True), \
                mock.patch.object(gitops, "StepGit", LocalStepGit, create=True):
            with ThreadPoolExecutor(max_workers=2) as pool:
                one = pool.submit(merge, "ticket/test")
                two = pool.submit(merge, "ticket/second")
                self.assertTrue(one.result(timeout=20)["commit"])
                self.assertTrue(two.result(timeout=20)["commit"])
        self.assertEqual(git(self.card, "status", "--porcelain"), "")
        self.assertTrue(os.path.isfile(os.path.join(self.card, "ticket.txt")))
        self.assertTrue(os.path.isfile(os.path.join(self.card, "second.txt")))

    def test_merge_rejects_tip_changed_after_approval_under_repo_lock(self):
        self.ticket_commit()
        approved = git(self.repo, "rev-parse", "ticket/test")
        before = git(self.repo, "rev-parse", "card/test")
        self.write(self.ticket, "later.txt", "późniejsza zmiana\n")
        git(self.ticket, "add", "later.txt")
        git(self.ticket, "commit", "-qm", "later change")
        with mock.patch.object(gitops, "sandbox_available", return_value=True, create=True), \
                mock.patch.object(gitops, "StepGit", LocalStepGit, create=True):
            with self.assertRaisesRegex(gitops.FlowGitError, "po zatwierdzeniu review"):
                self.flow.merge(self.repo, "card/test", "ticket/test", self.card, "Merge ticket",
                                expected_tip=approved)
        self.assertEqual(git(self.repo, "rev-parse", "card/test"), before)
        self.assertFalse(os.path.exists(os.path.join(self.card, "later.txt")))

    def test_without_sandbox_does_not_run_ticket_filter_or_move_card_branch(self):
        marker = os.path.join(self.base, "filter-ran")
        git(self.repo, "config", "filter.evil.smudge", f"sh -c 'touch {marker}; cat'")
        self.write(self.ticket, ".gitattributes", "ticket.txt filter=evil\n")
        self.ticket_commit()
        before = git(self.repo, "rev-parse", "card/test")
        with mock.patch.object(gitops, "sandbox_available", return_value=False, create=True):
            with self.assertRaisesRegex(gitops.FlowGitError, "piaskownic"):
                self.merge()
        self.assertEqual(git(self.repo, "rev-parse", "card/test"), before)
        self.assertFalse(os.path.exists(marker))
        self.assertFalse(os.path.exists(os.path.join(self.card, "ticket.txt")))

    def test_failed_ref_update_restores_card_worktree_before_reporting_error(self):
        self.ticket_commit()
        before = git(self.repo, "rev-parse", "card/test")
        before_tree = git(self.repo, "rev-parse", before + "^{tree}")
        original_run = self.flow.run
        failed = False

        def fail_once(cwd, *args, timeout=None):
            nonlocal failed
            if "update-ref" in args and not failed:
                failed = True
                return Result(1, "", "injected failure")
            return original_run(cwd, *args, timeout=timeout)

        with mock.patch.object(gitops, "sandbox_available", return_value=True, create=True), \
                mock.patch.object(gitops, "StepGit", LocalStepGit, create=True):
            with mock.patch.object(self.flow, "run", side_effect=fail_once):
                with self.assertRaises(gitops.FlowGitError):
                    self.merge()
            self.assertEqual(git(self.repo, "rev-parse", "card/test"), before)
            self.assertEqual(git(self.card, "write-tree"), before_tree)
            self.assertFalse(os.path.exists(os.path.join(self.card, "ticket.txt")))
            self.assertEqual(git(self.card, "status", "--porcelain"), "")
            merged = self.merge()
            again = self.merge()
        self.assertFalse(merged["already"])
        self.assertTrue(again["already"])
        self.assertEqual(git(self.repo, "rev-parse", "card/test"), merged["commit"])
        self.assertEqual(git(self.card, "status", "--porcelain"), "")

    def test_failed_materialization_does_not_move_ref_and_retry_completes(self):
        self.ticket_commit()
        before = git(self.repo, "rev-parse", "card/test")
        original_run = LocalStepGit.run
        failed = False

        def fail_once(step_git, *args, timeout=120):
            nonlocal failed
            if args and args[0] == "read-tree" and not failed:
                failed = True
                return Result(1, "", "injected checkout failure")
            return original_run(step_git, *args, timeout=timeout)

        with mock.patch.object(gitops, "sandbox_available", return_value=True, create=True), \
                mock.patch.object(gitops, "StepGit", LocalStepGit, create=True):
            with mock.patch.object(LocalStepGit, "run", fail_once):
                with self.assertRaisesRegex(gitops.FlowGitError, "worktree karty nie przyjął"):
                    self.merge()
            self.assertEqual(git(self.repo, "rev-parse", "card/test"), before)
            self.assertFalse(os.path.exists(os.path.join(self.card, "ticket.txt")))
            merged = self.merge()
        self.assertFalse(merged["already"])
        self.assertEqual(git(self.card, "status", "--porcelain"), "")

    def test_external_ref_change_during_merge_restores_card_worktree(self):
        self.ticket_commit()
        before = git(self.repo, "rev-parse", "card/test")
        tree = git(self.repo, "rev-parse", before + "^{tree}")
        external = git(self.repo, "commit-tree", tree, "-p", before, "-m", "external")
        original_run = self.flow.run
        changed = False

        def change_ref(cwd, *args, timeout=None):
            nonlocal changed
            if "update-ref" in args and not changed:
                changed = True
                git(self.repo, "update-ref", "refs/heads/card/test", external, before)
                return Result(1, "", "ref changed")
            return original_run(cwd, *args, timeout=timeout)

        with mock.patch.object(gitops, "sandbox_available", return_value=True, create=True), \
                mock.patch.object(gitops, "StepGit", LocalStepGit, create=True), \
                mock.patch.object(self.flow, "run", side_effect=change_ref):
            with self.assertRaisesRegex(gitops.FlowGitError, "gałąź karty zmieniła"):
                self.merge()
        self.assertEqual(git(self.repo, "rev-parse", "card/test"), external)
        self.assertFalse(os.path.exists(os.path.join(self.card, "ticket.txt")))
        self.assertEqual(git(self.card, "status", "--porcelain"), "")

    def test_retry_repairs_stale_worktree_after_ref_advanced(self):
        self.ticket_commit()
        with mock.patch.object(gitops, "sandbox_available", return_value=True, create=True), \
                mock.patch.object(gitops, "StepGit", LocalStepGit, create=True):
            merged = self.merge()
            parent = git(self.repo, "rev-parse", merged["commit"] + "^1")
            git(self.card, "read-tree", "-m", "-u", merged["commit"], parent)
            self.assertFalse(os.path.exists(os.path.join(self.card, "ticket.txt")))
            self.write(self.card, "base.txt", "local edit\n")
            with self.assertRaisesRegex(gitops.FlowGitError, "lokalne zmiany"):
                self.merge()
            self.assertEqual(git(self.repo, "rev-parse", "card/test"), merged["commit"])
            self.write(self.card, "base.txt", "base\n")
            retry = self.merge()
        self.assertEqual(retry, {"commit": merged["commit"], "already": True})
        with open(os.path.join(self.card, "ticket.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "ticket\n")
        self.assertEqual(git(self.card, "status", "--porcelain"), "")

    @unittest.skipUnless(sandbox_available(), "bwrap niedostępny w tym środowisku")
    def test_smudge_cannot_write_outside_card_worktree(self):
        marker = os.path.join(self.base, "filter-ran")
        inside = os.path.join(self.card, "filter-ran")
        git(self.repo, "config", "filter.evil.smudge", f"sh -c 'touch {inside}; touch {marker}; cat'")
        self.write(self.ticket, ".gitattributes", "ticket.txt filter=evil\n")
        self.ticket_commit()
        merged = self.merge()
        self.assertFalse(merged["already"])
        self.assertTrue(os.path.exists(inside), "filtr smudge był faktycznie uruchomiony")
        self.assertFalse(os.path.exists(marker))
        self.assertEqual(git(self.card, "status", "--porcelain", "--untracked-files=no"), "")
