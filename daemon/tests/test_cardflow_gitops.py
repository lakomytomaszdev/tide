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

    @unittest.skipUnless(sandbox_available(), "brak bwrap")
    def test_review_check_ignores_untracked_files_but_not_tracked_changes(self):
        # 04.11, K-43/BO-9: wynik pomiaru recenzenta (nieśledzony plik) unieważniał review 9 razy.
        self.ticket_commit()
        tip = self.flow.clean_tip(self.repo, "ticket/test", self.ticket)
        self.write(self.ticket, ".review-result.json", "{}\n")
        self.assertEqual(self.flow.clean_tip(self.repo, "ticket/test", self.ticket, untracked=False), tip)
        with self.assertRaises(gitops.FlowGitError):     # przed review nieśledzone pliki autora dalej blokują
            self.flow.clean_tip(self.repo, "ticket/test", self.ticket)
        self.write(self.ticket, "ticket.txt", "zmienione\n")
        with self.assertRaises(gitops.FlowGitError):
            self.flow.clean_tip(self.repo, "ticket/test", self.ticket, untracked=False)

    @unittest.skipUnless(sandbox_available(), "brak bwrap")
    def test_worktree_with_only_user_agent_tooling_is_removed(self):
        os.makedirs(os.path.join(self.ticket, ".agents", "skills"))
        self.write(os.path.join(self.ticket, ".agents", "skills"), "SKILL.md", "x\n")
        self.assertTrue(self.flow.remove_worktree(self.repo, "ticket/test", self.ticket)["removed"])
        self.assertFalse(os.path.exists(self.ticket))

    @unittest.skipUnless(sandbox_available(), "brak bwrap")
    def test_worktree_with_real_leftovers_is_kept(self):
        self.write(self.ticket, "notatka.txt", "praca\n")
        with self.assertRaises(gitops.FlowGitError):
            self.flow.remove_worktree(self.repo, "ticket/test", self.ticket)
        self.assertTrue(os.path.exists(os.path.join(self.ticket, "notatka.txt")))

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


class ReviewDiffTest(unittest.TestCase):
    """K-33/PT-108: ticket scalający gałęzie ma diff rzędu milionów znaków — review dostaje podsumowanie."""

    def setUp(self):
        self.base = test_root()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.repo = os.path.join(self.base, "project")
        os.mkdir(self.repo)
        git(self.repo, "init", "-q", "-b", "master")
        git(self.repo, "config", "user.name", "Tester")
        git(self.repo, "config", "user.email", "tester@example.invalid")
        with open(os.path.join(self.repo, "a.txt"), "w") as fh:
            fh.write("a\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "start")
        git(self.repo, "branch", "card/k-1")
        git(self.repo, "checkout", "-qb", "ticket/pt-1")

    def commit(self, name, text):
        with open(os.path.join(self.repo, name), "w") as fh:
            fh.write(text)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", name)

    def test_small_diff_is_given_whole(self):
        self.commit("b.txt", "mała zmiana\n")
        out = gitops.FlowGit().review_diff(self.repo, "card/k-1", "ticket/pt-1")
        self.assertIn("+mała zmiana", out)

    def test_huge_diff_becomes_stat_with_read_only_commands(self):
        self.commit("big.txt", "x" * 80 + "\n" * 1 + ("linia\n" * 70_000))
        with self.assertRaises(gitops.FlowGitError):
            gitops.FlowGit().diff(self.repo, "card/k-1", "ticket/pt-1")
        out = gitops.FlowGit().review_diff(self.repo, "card/k-1", "ticket/pt-1")
        self.assertLess(len(out), 300_000)
        self.assertIn("big.txt", out)
        self.assertIn("git diff refs/heads/card/k-1 refs/heads/ticket/pt-1 -- <ścieżka>", out)
        self.assertNotIn("+linia", out)


class ForeignChangesTest(unittest.TestCase):
    def test_user_agent_tooling_is_not_a_ticket_change(self):
        # K-15/LEGAL-60: zewnętrzna synchronizacja rozłożyła .agents/skills i .codex/agents w worktree ticketu.
        porcelain = "?? .agents/skills/x/SKILL.md\n?? .codex/agents/a.toml\n"
        self.assertEqual(gitops._foreign_changes(porcelain), [])
        self.assertEqual(gitops._foreign_changes(porcelain + "?? debug.py\n"), ["?? debug.py"])
        self.assertEqual(gitops._foreign_changes(" M .codex/agents/a.toml\n"), [" M .codex/agents/a.toml"])


class PinnedMainBranchTest(unittest.TestCase):
    def test_repo_can_pin_the_card_base_branch(self):
        # 04.11, BO: praca toczy się na feature/vision-frames, a karta zakładała gałąź od master.
        import subprocess
        import tempfile
        base = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, base, True)
        run = lambda *a: subprocess.run(["git", "-C", base, *a], check=True, capture_output=True)
        run("init", "-q", "-b", "master")
        run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a")
        run("branch", "feature/x")
        git = gitops.FlowGit()
        self.assertEqual(git.main_branch(base), "master")
        run("config", "tide.mainBranch", "feature/x")
        self.assertEqual(git.main_branch(base), "feature/x")
        run("config", "tide.mainBranch", "nie-ma")
        self.assertEqual(git.main_branch(base), "master")
