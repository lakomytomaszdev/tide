"""Budżet analizy dotyczy danych, nie liczby aliasów tego samego commita."""
import unittest
from unittest import mock

from tide_daemon.cardflow import filesuggestions
from tide_daemon.worktrees.git import Result


class SuggestionBudgetTest(unittest.TestCase):
    def test_alias_branches_reuse_tree_and_history_within_the_time_budget(self):
        sha = "a" * 40
        clock = [0.0]
        commands = []
        def run(repo, *args, **kwargs):
            clock[0] += .1
            commands.append(args)
            if "for-each-ref" in args:
                out = "\n".join(f"refs/heads/alias/{i:03}\0{sha}\0\0commit" for i in range(100))
            elif "ls-tree" in args:
                out = "src/data.py\0"
            elif "rev-list" in args:
                out = sha + "\n"
            elif "log" in args:
                out = f"\0\0{sha}\0{1790000000}\0\nsrc/data.py\0"
            else:
                self.fail(str(args))
            return Result(0, out, "")
        git = mock.Mock(run=run)
        with mock.patch.object(filesuggestions.time, "monotonic", side_effect=lambda: clock[0]):
            result = filesuggestions.suggest_files(git, "/repo", "src/data.py", "")
        self.assertEqual(result["analysis"]["branches"], 100)
        self.assertEqual(len(result["candidates"]), 100)
        self.assertFalse(result["analysis"]["partial"], result)
        self.assertEqual(len(commands), 4)
