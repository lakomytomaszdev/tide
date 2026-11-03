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
        allowed = ["git fetch origin"]
        self.assertTrue(commands.is_allowed("git fetch origin", allowed))
        self.assertFalse(commands.is_allowed("git fetch", allowed))
        self.assertFalse(commands.is_allowed("git push origin", allowed))
        self.assertFalse(commands.is_allowed("git fetch origin && make install", allowed))
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

    def test_locale_prefix_can_be_allowed_but_does_not_evade_denial(self):
        # K-33/PT-108: plan wymaga `LC_ALL=pl_PL.UTF-8 make test`, a zgoda była niemożliwa (canAllow=false).
        command = "LC_ALL=pl_PL.UTF-8 make test"
        self.assertTrue(commands.single_command(command))
        self.assertFalse(commands.is_forbidden(command, ["make install"]))
        self.assertTrue(commands.is_allowed(command, [command]))
        self.assertFalse(commands.is_allowed(command, ["make test"]), "zgoda dotyczy pełnego polecenia")
        self.assertTrue(commands.is_forbidden("LC_ALL=pl_PL.UTF-8 make install", ["make install"]))
        for bad in ("PATH=/tmp make test", "LD_PRELOAD=x.so make test", "LC_ALL=pl_PL.UTF-8",
                    "LC_ALL=pl_PL.UTF-8 bash -c 'make test'", "LC_ALL=pl_PL.UTF-8 FOO=1 make test",
                    "LC_ALL=$(id) make test"):
            self.assertFalse(commands.single_command(bad), bad)
            self.assertTrue(commands.is_forbidden(bad, []), bad)

    def test_read_only_tools_are_allowed_in_every_project_but_denial_wins(self):
        # K-35/K-15: kroki stawały na `cd … && ls … | head`, bo `cd` i `head` nie były na liście projektu.
        allowed, denied = commands.claude_tools([], ["make install"])
        for rule in ("Bash(cd *)", "Bash(head *)", "Bash(tail *)", "Bash(wc *)", "Bash(git log *)"):
            self.assertIn(rule, allowed)
        self.assertIn("Bash(make install)", denied)
        self.assertNotIn("Bash(make *)", allowed)
        self.assertTrue(commands.is_allowed("head -80 plik.py", []))
        self.assertTrue(commands.is_allowed("git clean -f -- apps/board/tests/test_zz_debug.py", []))
        self.assertFalse(commands.is_allowed("git clean -fdx", []))
        self.assertFalse(commands.is_allowed("rm -rf apps", []))
        self.assertFalse(commands.is_allowed("make install", []))
        self.assertTrue(commands.is_forbidden("make install", ["make install"]))

    def test_claude_rules_keep_exact_grant(self):
        allowed, denied = commands.claude_tools(["git status *", "git fetch origin"], ["make install"])
        self.assertIn("Bash(git status)", allowed)
        self.assertIn("Bash(git status *)", allowed)
        self.assertIn("Bash(git fetch origin)", allowed)
        self.assertNotIn("Bash(git fetch *)", allowed)
        self.assertIn("Bash(make install *)", denied)
