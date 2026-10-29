"""Reguły poleceń projektu: domyślne i precyzja zgody."""
import unittest

from tide_daemon.board import commands


class CommandRulesTest(unittest.TestCase):
    def test_tide_forbids_make_install_by_default(self):
        allowed, denied = commands.defaults("plasma-tide", "/w/plasma-tide")
        self.assertIn("make install", denied)
        self.assertFalse(commands.is_forbidden("make test", denied))
        self.assertTrue(commands.is_forbidden("make install PREFIX=/tmp/x", denied))
        self.assertIn("make test", allowed)
        self.assertTrue(commands.is_forbidden("pwd; make install", denied))
        self.assertTrue(commands.is_forbidden("git status && make install", denied))
        self.assertTrue(commands.is_forbidden("git status $(make install)", denied))
        self.assertFalse(commands.is_allowed("git status && make install", list(commands.DEFAULT_ALLOWED)))

    def test_allowing_one_command_does_not_allow_whole_program_or_chain(self):
        allowed = ["git log --oneline"]
        self.assertTrue(commands.is_allowed("git log --oneline", allowed))
        self.assertFalse(commands.is_allowed("git log", allowed))
        self.assertFalse(commands.is_allowed("git status", allowed))
        self.assertFalse(commands.is_allowed("git log --oneline && make install", allowed))
        self.assertFalse(commands.is_allowed("git status; rm -rf x", list(commands.DEFAULT_ALLOWED)))
        self.assertTrue(commands.is_allowed("git status", list(commands.DEFAULT_ALLOWED)))
        self.assertTrue(commands.is_allowed("git log --oneline", list(commands.DEFAULT_ALLOWED)))

    def test_shell_operator_and_quoted_arguments_cannot_evade_denial(self):
        denied = ["make install"]
        self.assertFalse(commands.single_command("pwd |& make install"))
        self.assertTrue(commands.is_forbidden("pwd |& make install", denied))
        self.assertFalse(commands.is_allowed("pwd |& make install", ["pwd *"]))
        self.assertTrue(commands.is_forbidden('make "install"', denied))
        self.assertTrue(commands.is_forbidden(r"make \install", denied))
        self.assertTrue(commands.is_forbidden("make install PREFIX=/tmp/x", denied))
        self.assertFalse(commands.is_forbidden("make installer", denied))
        self.assertTrue(commands.is_forbidden("git push origin", ["git push *"]))
        self.assertTrue(commands.is_allowed("git 'log' --oneline", ["git log --oneline"]))

    def test_claude_rules_keep_exact_grant(self):
        allowed, denied = commands.claude_tools(["git status *", "git fetch origin"], ["make install"])
        self.assertIn("Bash(git status)", allowed)
        self.assertIn("Bash(git status *)", allowed)
        self.assertIn("Bash(git fetch origin)", allowed)
        self.assertNotIn("Bash(git fetch *)", allowed)
        self.assertIn("Bash(make install *)", denied)
