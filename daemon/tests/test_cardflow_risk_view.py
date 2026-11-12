"""K-105 #4: ocena ryzyka outside w widoku decyzji z pamięci prezentacji, bramki wykonania świeżo."""
import os
import tempfile
import unittest
from unittest import mock

from tide_daemon.board import commands
from tide_daemon.cardflow import decisions
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.riskview import RiskMemo, outside_risk

STEP = {"step": "s1", "kind": "ticket", "ticket": "PT-1"}


class Counted:
    """Licznik na granicy `os.path.realpath` w module poleceń."""

    def __init__(self):
        self.calls = 0
        self._real = os.path.realpath

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self._real(*args, **kwargs)


class RiskViewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.worktree = os.path.join(os.path.realpath(self.tmp.name), "wt")
        os.makedirs(self.worktree)
        with open(os.path.join(self.worktree, "skrypt.sh"), "w") as fh:
            fh.write("echo hi\n")
        self.roots = (self.worktree, "", "")
        self.memo = RiskMemo()
        self.count = Counted()
        patcher = mock.patch.object(commands.os.path, "realpath", self.count)
        patcher.start()
        self.addCleanup(patcher.stop)

    def risk(self, card="K-1", version=("j", 1, 1)):
        with self.memo.scope(card, version):
            return outside_risk("bash skrypt.sh", self.roots)

    def decision(self, scope=True, version=("j", 1, 1)):
        done = {"command": "bash skrypt.sh", "canAllow": True, "pattern": "bash *", "outside": True}
        step = {**STEP, "worktree": self.worktree}
        if not scope:
            return decisions._outside(None, "s1", step, done)
        with self.memo.scope("K-1", version):
            return decisions._outside(None, "s1", step, done)

    def test_view_description_and_options_equal_fresh_evaluation(self):
        fresh = self.decision(scope=False)
        viewed = self.decision()
        self.assertIn("skrypt powłoki", viewed["why"])
        self.assertEqual([o["id"] for o in viewed["options"]], ["run", "run-ticket", "deny"])
        self.assertEqual(fresh, viewed)

    def test_next_publications_without_journal_change_do_not_resolve_paths(self):
        first = self.risk()
        calls = self.count.calls
        self.assertGreater(calls, 0)
        for _ in range(3):
            self.assertEqual(self.risk(), first)
        self.assertEqual(self.count.calls, calls)

    def test_new_journal_version_evaluates_again(self):
        self.risk()
        calls = self.count.calls
        self.risk(version=("j", 2, 2))
        self.assertGreater(self.count.calls, calls)

    def test_card_without_outside_decision_loses_entries(self):
        self.risk()
        self.assertEqual(self.memo.entries("K-1"), 1)
        with self.memo.scope("K-1", ("j", 1, 1)):
            pass
        self.assertEqual(self.memo.entries("K-1"), 0)

    def test_no_scope_and_no_version_mean_fresh_evaluation(self):
        outside_risk("bash skrypt.sh", self.roots)
        calls = self.count.calls
        outside_risk("bash skrypt.sh", self.roots)
        self.assertGreater(self.count.calls, calls)
        with self.memo.scope("K-1", None):
            calls = self.count.calls
            outside_risk("bash skrypt.sh", self.roots)
            self.assertGreater(self.count.calls, calls)

    def test_execution_gates_are_fresh_after_symlink_change(self):
        """Bramki wołają `outside_risky` bezpośrednio: zmiana dowiązania po publikacji zmienia wynik."""
        link = os.path.join(self.worktree, "tool.sh")
        target = os.path.join(self.worktree, "skrypt.sh")
        os.symlink(target, link)
        roots = (self.worktree,)
        self.assertTrue(commands.outside_risky("bash tool.sh", roots))
        with self.memo.scope("K-1", ("j", 1, 1)):
            viewed = outside_risk("bash tool.sh", roots)
        os.unlink(link)
        outside = os.path.join(os.path.realpath(self.tmp.name), "inny.txt")
        with open(outside, "w") as fh:
            fh.write("x\n")
        os.symlink(outside, link)
        with self.memo.scope("K-1", ("j", 1, 1)):
            self.assertEqual(outside_risk("bash tool.sh", roots), viewed)   # widok: pamięć do zmiany dziennika
        self.assertEqual(commands.outside_risky("bash tool.sh", roots), commands.outside_risky("bash tool.sh", roots))
        before = self.count.calls
        commands.outside_risky("bash tool.sh", roots)
        self.assertGreater(self.count.calls, before)    # bramka nie czyta pamięci


class JournalViewVersionTest(unittest.TestCase):
    def test_public_version_changes_with_journal_and_matches_presentation_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Journal(os.path.join(tmp, "board"), mirror=os.path.join(tmp, "mirror"))
            self.assertFalse(journal.presenting())
            journal.append("K-1", "card.started", repo=tmp)
            first = journal.view_version("K-1")
            self.assertEqual(journal.view_version("K-1"), first)
            journal.append("K-1", "note", text="x")
            self.assertNotEqual(journal.view_version("K-1"), first)
            with journal.presentation():
                self.assertTrue(journal.presenting())
                self.assertEqual(journal.view_version("K-1"), journal._read_version("K-1")[0])


if __name__ == "__main__":
    unittest.main()
