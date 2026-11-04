"""Strażnik kroku opiekuna (K-37, PT-158): stan worktree, HEAD i gałęzi sprzed kroku porównany ze stanem po kroku
(`cardflow/guard.py`) i zapis Tide w trakcie kroku opiekuna, który nie jest zapisem kroku (`FlowEngine._guard_*`).
Prawdziwe repozytorium git z worktree ticketu i gałęzią karty, bez usługi."""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from tide_daemon.cardflow import guard
from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.gitops import FlowGit
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold
from tide_daemon.tasks.agentcmd import resolve_git_dir

from .gitrepos import commit, git

CARD = "K-1"


class GuardRepo(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tide-guard-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.repo = os.path.join(self.root, "proj")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "master")
        commit(self.repo, "README.md", "projekt\n")
        commit(self.repo, ".gitignore", "build/\n")
        git(self.repo, "branch", "card/k-1")
        self.wt = os.path.join(self.root, "proj-worktrees", "pt-1")
        git(self.repo, "worktree", "add", "-q", "-b", "ticket/pt-1", self.wt, "card/k-1")
        with open(os.path.join(self.wt, "brudny.txt"), "w", encoding="utf-8") as fh:
            fh.write("zmiana kroku ticketu sprzed opiekuna\n")
        self.out = self.wt + ".caretaker-K-1-care-1.step/out"
        os.makedirs(self.out)
        self.git = FlowGit()
        self.git_dir = resolve_git_dir(self.repo, self.wt)

    def snap(self):
        return guard.snapshot(self.git, repo=self.repo, worktree=self.wt, git_dir=self.git_dir,
                              branches=("ticket/pt-1", "card/k-1"))


class SnapshotTest(GuardRepo):
    def test_writing_only_the_result_file_and_reading_pass(self):
        before = self.snap()
        with open(os.path.join(self.out, "result.json"), "w", encoding="utf-8") as fh:
            fh.write('{"result": "done"}')
        git(self.wt, "status", "--porcelain")         # odczyt gitem odświeża indeks w `.git`, nie w worktree
        git(self.wt, "log", "--oneline")
        with open(os.path.join(self.wt, "README.md"), encoding="utf-8") as fh:
            fh.read()
        self.assertEqual(guard.changes(before, self.snap()), [])

    def test_tracked_untracked_and_ignored_files_are_detected(self):
        for name, mode in (("README.md", "a"), ("nowy.txt", "w"), ("build/out.o", "w"), ("brudny.txt", "a")):
            with self.subTest(name=name):
                before = self.snap()
                os.makedirs(os.path.dirname(os.path.join(self.wt, name)), exist_ok=True)
                with open(os.path.join(self.wt, name), mode, encoding="utf-8") as fh:
                    fh.write("opiekun pisze\n")
                found = guard.changes(before, self.snap())
                self.assertEqual(len(found), 1, found)
                self.assertIn(name.split("/")[0], found[0])

    def test_rewrite_with_restored_mtime_and_same_size_is_detected(self):
        path = os.path.join(self.wt, "README.md")
        info = os.stat(path)
        before = self.snap()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("PROJEKT\n")                      # ta sama długość
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(os.stat(path).st_size, info.st_size)
        self.assertTrue(guard.changes(before, self.snap()))

    def test_deleting_and_chmod_are_detected(self):
        before = self.snap()
        os.unlink(os.path.join(self.wt, "brudny.txt"))
        self.assertTrue(guard.changes(before, self.snap()))
        before = self.snap()
        os.chmod(os.path.join(self.wt, "README.md"), 0o755)
        self.assertTrue(guard.changes(before, self.snap()))

    def test_commit_moves_the_step_branch_and_card_branch_is_watched(self):
        before = self.snap()
        git(self.wt, "commit", "-q", "--allow-empty", "-m", "opiekun commituje")
        self.assertEqual(guard.changes(before, self.snap()), ["przesunięta gałąź ticket/pt-1"])
        before = self.snap()
        git(self.repo, "branch", "-f", "card/k-1", "ticket/pt-1")
        self.assertEqual(guard.changes(before, self.snap()), ["przesunięta gałąź card/k-1"])
        before = self.snap()
        git(self.wt, "checkout", "-q", "--detach")
        self.assertEqual(guard.changes(before, self.snap()), ["przesunięty HEAD worktree"])

    def test_missing_worktree(self):
        before = self.snap()
        shutil.rmtree(self.wt)
        self.assertIn(f"worktree {self.wt} zniknął", guard.changes(before, self.snap()))

    def test_symlinks_are_not_followed(self):
        outside = os.path.join(self.root, "poza")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.wt, "link"))
        before = self.snap()
        with open(os.path.join(outside, "x"), "w", encoding="utf-8") as fh:
            fh.write("poza worktree\n")
        self.assertEqual(guard.changes(before, self.snap()), [])


class EngineGuardTest(GuardRepo):
    """Metody silnika na samym dzienniku i gicie: stan bazowy, zapis Tide w trakcie kroku i werdykt."""

    def setUp(self):
        super().setUp()
        board = os.path.join(self.root, "board")
        os.makedirs(board)
        self.engine = SimpleNamespace(git=self.git, journal=Journal(board))
        for name in ("_guard_baseline", "_guard_check", "_guard_rebase", "_guard_verdict"):
            setattr(self.engine, name, getattr(FlowEngine, name).__get__(self.engine))
        self.engine.journal.append(CARD, "flow.started", provider="anthropic")

    def baseline(self):
        self.engine._guard_baseline(CARD, "K-1-care-1", self.repo, self.wt, self.git_dir,
                                    ("ticket/pt-1", "card/k-1"))

    def tide_writes(self, work):
        """Jak `_act`: sprawdzenie przed działaniem Tide, działanie, stan na nowo."""
        names = self.engine._guard_check(CARD, fold(self.engine.journal.read(CARD)))
        work()
        self.engine._guard_rebase(CARD, names, "merge")

    def test_tide_merge_into_the_card_branch_is_not_a_caretaker_write(self):
        self.baseline()
        main = os.path.join(self.root, "card")
        git(self.repo, "worktree", "add", "-q", main, "card/k-1")
        self.tide_writes(lambda: commit(main, "scalony.txt"))
        self.assertEqual(self.engine._guard_verdict(CARD, "K-1-care-1"), "")
        causes = [e.get("cause") for e in self.engine.journal.read(CARD) if e["type"] == guard.EVENT]
        self.assertEqual(causes, ["start", "merge"])

    def test_caretaker_write_before_a_tide_write_is_kept(self):
        self.baseline()
        with open(os.path.join(self.wt, "opiekun.txt"), "w", encoding="utf-8") as fh:
            fh.write("zapis kroku\n")
        self.tide_writes(lambda: git(self.repo, "branch", "-f", "card/k-1", "master"))
        verdict = self.engine._guard_verdict(CARD, "K-1-care-1")
        self.assertTrue(verdict.startswith("krok opiekuna pisał poza swoim katalogiem wyjściowym"), verdict)
        self.assertIn("opiekun.txt", verdict)

    def test_write_after_the_tide_write_is_detected(self):
        self.baseline()
        self.tide_writes(lambda: None)
        git(self.wt, "commit", "-q", "--allow-empty", "-m", "opiekun commituje")
        self.assertIn("przesunięta gałąź ticket/pt-1", self.engine._guard_verdict(CARD, "K-1-care-1"))

    def test_without_a_baseline_the_result_is_rejected(self):
        self.assertIn("brak stanu worktree sprzed kroku", self.engine._guard_verdict(CARD, "K-1-care-9"))

    def test_finished_caretaker_steps_are_no_longer_watched(self):
        self.baseline()
        self.engine.journal.append(CARD, "step.finished", step="K-1-care-1", result="done")
        self.assertEqual(self.engine._guard_check(CARD, fold(self.engine.journal.read(CARD))), [])


if __name__ == "__main__":
    unittest.main()
