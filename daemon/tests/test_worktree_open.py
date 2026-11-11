"""Bramka `Open` Worktrees (PT-361): katalog z listy jak dotąd, plik w takim katalogu tylko dla Ridera."""
import os
import tempfile
import unittest

from tide_daemon.worktree_monitor import openable


class OpenableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        self.work = os.path.join(self.root, "wt")
        self.other = os.path.join(self.root, "other")
        os.makedirs(os.path.join(self.work, "src"))
        os.makedirs(self.other)
        self.file = os.path.join(self.work, "src", "a.py")
        self.outside = os.path.join(self.other, "b.py")
        for path in (self.file, self.outside):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("x\n")
        self.worktrees = [{"path": self.work, "missing": False}]

    def test_listed_directory_opens_in_both_programs(self):
        self.assertTrue(openable(self.worktrees, self.work, "rider"))
        self.assertTrue(openable(self.worktrees, self.work, "terminal"))

    def test_file_in_a_listed_worktree_opens_only_in_rider(self):
        self.assertTrue(openable(self.worktrees, self.file, "rider"))
        self.assertFalse(openable(self.worktrees, self.file, "terminal"))

    def test_other_paths_are_refused(self):
        self.assertFalse(openable(self.worktrees, self.outside, "rider"))
        self.assertFalse(openable(self.worktrees, self.other, "rider"))
        self.assertFalse(openable(self.worktrees, os.path.join(self.work, "nie-ma.py"), "rider"))
        self.assertFalse(openable(self.worktrees, os.path.join(self.work, "src"), "rider"), "podkatalog to nie plik")
        self.assertFalse(openable(self.worktrees, "wt/src/a.py", "rider"), "tylko ścieżki bezwzględne")

    def test_dotdot_and_symlinks_cannot_leave_the_worktree(self):
        self.assertFalse(openable(self.worktrees, os.path.join(self.work, "src", "..", "..", "other", "b.py"), "rider"))
        link = os.path.join(self.work, "link.py")
        os.symlink(self.outside, link)
        self.assertFalse(openable(self.worktrees, link, "rider"))

    def test_sibling_directory_with_a_common_prefix_is_not_inside(self):
        sibling = self.work + "-copy"
        os.makedirs(sibling)
        path = os.path.join(sibling, "c.py")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("x\n")
        self.assertFalse(openable(self.worktrees, path, "rider"))

    def test_missing_worktree_opens_nothing(self):
        gone = [{"path": self.work, "missing": True}]
        self.assertFalse(openable(gone, self.work, "rider"))
        self.assertFalse(openable(gone, self.file, "rider"))


if __name__ == "__main__":
    unittest.main()
