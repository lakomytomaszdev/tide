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

    def test_entries_of_unpublished_cards_are_bounded(self):
        from tide_daemon.cardflow import riskview
        for n in range(riskview.MAX_CARDS + 20):
            self.risk(card=f"K-{n}")
        self.assertEqual(self.memo.entries("K-0"), 0)
        self.assertEqual(self.memo.entries(f"K-{riskview.MAX_CARDS + 19}"), 1)
        self.assertLessEqual(len(self.memo._cards), riskview.MAX_CARDS)

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
        """Zmiana dowiązania po publikacji zmienia wynik bramek (zgoda automatyczna, merge-check), także wołanych
        wewnątrz otwartego zakresu pamięci: widok trzyma stary opis, bramka liczy świeżo."""
        from types import SimpleNamespace
        link = os.path.join(self.worktree, "tool.sh")
        os.symlink(os.path.join(self.worktree, "skrypt.sh"), link)
        elsewhere = os.path.join(os.path.realpath(self.tmp.name), "inny.txt")
        with open(elsewhere, "w") as fh:
            fh.write("x\n")
        roots = (self.worktree, "", "")
        event = {"step": "s1", "command": "bash tool.sh", "worktree": self.worktree, "stepDir": "", "canAllow": True}
        entry = {"ticket": "PT-1", "check": 1}
        st = SimpleNamespace(started={"repo": ""})
        with self.memo.scope("K-1", ("j", 1, 1)):
            viewed = outside_risk("bash tool.sh", roots)
            self.assertIn("skrypt powłoki", viewed)
            self.assertIn("Uwaga:", decisions._merge_check_consent(st, entry, event)["why"])
            os.unlink(link)
            os.symlink(elsewhere, link)
            self.assertEqual(outside_risk("bash tool.sh", roots), viewed)    # widok: pamięć do zmiany dziennika
            before = self.count.calls
            made = decisions._merge_check_consent(st, entry, event)         # merge-check: świeżo
            self.assertGreater(self.count.calls, before)
            self.assertNotIn("Uwaga:", made["why"])
            self.assertEqual([o["id"] for o in made["options"]], ["deny"])    # skrypt spoza worktree: już nie dozwolony
            before = self.count.calls
            now = commands.outside_risky("bash tool.sh", roots)              # wspólna bramka (zgoda, run-always)
            self.assertGreater(self.count.calls, before)
            self.assertNotIn("skrypt powłoki", now)
            self.assertNotEqual(now, viewed)


class EngineRiskViewTest(unittest.TestCase):
    """Publikacja przez `FlowEngine.derived` w `journal.presentation()` (K-105 #4, kryteria 2 i 3)."""

    def setUp(self):
        from types import SimpleNamespace
        from tide_daemon.cardflow.engine import FlowEngine
        tmp = tempfile.TemporaryDirectory(prefix="tide-risk-view-")
        self.addCleanup(tmp.cleanup)
        self.root = os.path.realpath(tmp.name)
        self.worktree = os.path.join(self.root, "wt")
        os.makedirs(self.worktree)
        with open(os.path.join(self.worktree, "skrypt.sh"), "w") as fh:
            fh.write("echo hi\n")
        self.journal = Journal(os.path.join(self.root, "board"), mirror=os.path.join(self.root, "mirror"))
        self.card = "K-1"
        self.journal.append(self.card, "card.started", entry="now", executor="Tester", repo=self.root)
        self.journal.append(self.card, "step.started", step="s1", ticket="PT-1", kind="ticket",
                            worktree=self.worktree, stepDir=self.root)
        self.journal.append(self.card, "step.finished", step="s1", ticket="PT-1", result="denied", outside=True,
                            command="bash skrypt.sh", canAllow=True)
        store = SimpleNamespace(root=self.journal.root, load=lambda: SimpleNamespace(cards={}, projects={}))
        self.engine = FlowEngine(store, journal=self.journal, settings=os.path.join(self.root, "settings.json"),
                                 codex_roots=[])
        self.count = Counted()
        patcher = mock.patch.object(commands.os.path, "realpath", self.count)
        patcher.start()
        self.addCleanup(patcher.stop)

    def decisions(self, out):
        return [d for d in out["flow"]["decisions"] if d.get("kind") == "permission"]

    def publish(self):
        with self.journal.presentation():
            return self.decisions(self.engine.derived({"id": self.card, "column": "todo"}))

    def test_publications_use_memory_until_the_journal_changes(self):
        first = self.publish()
        self.assertEqual(len(first), 1)
        self.assertIn("skrypt powłoki", first[0]["why"])
        self.assertEqual([o["id"] for o in first[0]["options"]], ["run", "run-ticket", "deny"])
        calls = self.count.calls
        self.assertGreater(calls, 0)
        self.assertEqual(self.publish(), first)
        self.assertEqual(self.count.calls, calls)             # druga publikacja nie rozwiązuje ścieżek
        self.journal.append(self.card, "note", text="x")
        self.assertEqual(self.publish(), first)
        self.assertGreater(self.count.calls, calls)           # nowe zdarzenie: ponowna ocena

    def test_derived_outside_presentation_is_always_fresh(self):
        self.assertEqual(len(self.decisions(self.engine.derived({"id": self.card, "column": "todo"}))), 1)
        calls = self.count.calls
        self.assertGreater(calls, 0)
        self.engine.derived({"id": self.card, "column": "todo"})
        self.assertGreater(self.count.calls, calls)

    def test_symlink_change_between_publication_and_gate(self):
        from types import SimpleNamespace
        from tide_daemon.cardflow import consent
        link = os.path.join(self.worktree, "tool.sh")
        os.symlink(os.path.join(self.worktree, "skrypt.sh"), link)
        other = tempfile.TemporaryDirectory(prefix="tide-risk-view-other-")    # poza korzeniami polecenia
        self.addCleanup(other.cleanup)
        elsewhere = os.path.join(os.path.realpath(other.name), "inny.txt")
        with open(elsewhere, "w") as fh:
            fh.write("x\n")
        self.journal.append(self.card, "step.started", step="s2", ticket="PT-1", kind="ticket",
                            worktree=self.worktree, stepDir=self.root)
        self.journal.append(self.card, "step.finished", step="s2", ticket="PT-1", result="denied", outside=True,
                            command="bash tool.sh", canAllow=True)
        shown = lambda: next(d for d in self.publish() if d["step"] == "s2")
        before = shown()
        self.assertIn("skrypt powłoki", before["why"])
        os.unlink(link)
        os.symlink(elsewhere, link)
        self.assertEqual(shown(), before)                     # widok: pamięć do zmiany dziennika
        step = {"step": "s2", "ticket": "PT-1", "worktree": self.worktree, "stepDir": self.root}
        project = SimpleNamespace(repo=self.root, slug="p", denied_commands=())
        engine = SimpleNamespace(journal=self.journal, _auto_outside=lambda card: True, _extra_repos=lambda card: (),
                                 _outside_remembered=lambda slug: [], _project=lambda card: (None, project))
        roots = (self.worktree, self.root, self.root)
        fresh = commands.outside_risky("bash tool.sh", roots)
        self.assertNotIn("skrypt powłoki", fresh)             # bramka (run-always, zgoda): świeżo, nie z widoku
        with self.engine._risk_view.scope(self.card, self.journal.view_version(self.card)):
            self.assertEqual(commands.outside_risky("bash tool.sh", roots), fresh)   # także w otwartym zakresie
            self.assertFalse(consent.automatic(engine, self.card, step, "bash tool.sh"))   # zgoda: polecenie odrzucone


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
