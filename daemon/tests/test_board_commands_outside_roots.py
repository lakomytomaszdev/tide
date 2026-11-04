"""Polecenia poza piaskownicą w obrębie projektu (04.11, K-41: testy rdzenia przez venv z PYTHONPATH do worktree)."""
import unittest

from tide_daemon.board.commands import outside_allowed, outside_argv, outside_risky

WT = "/ws/proj-worktrees/kamp-16"
REPO = "/ws/proj"
ROOTS = (WT, REPO)
TESTS = (f"env PYTHONPATH={WT}/core/src {REPO}/core/.venv/bin/python -m pytest -q "
         f"-c {WT}/core/pyproject.toml {WT}/core/tests")


class OutsideRootsTest(unittest.TestCase):
    def test_project_tests_with_pythonpath_into_the_worktree_run_without_asking(self):
        self.assertEqual(outside_argv(TESTS, ROOTS)[:3], [f"{REPO}/core/.venv/bin/python", "-m", "pytest"])
        self.assertTrue(outside_allowed(TESTS, [], ROOTS))
        self.assertEqual(outside_risky(TESTS, ROOTS), "")

    def test_without_roots_or_outside_the_project_it_stays_refused(self):
        self.assertIsNone(outside_argv(TESTS))
        self.assertIsNone(outside_argv(f"env PYTHONPATH=/usr/lib/evil python3 -m pytest", ROOTS))
        self.assertIsNone(outside_argv(f"PYTHONPATH={WT}/src:/opt/x python3 -m pytest", ROOTS))
        self.assertIsNone(outside_argv(f"env LD_PRELOAD={WT}/x.so python3 -m pytest", ROOTS))
        self.assertIn("spoza worktree", outside_risky(f"python3 -m pytest /etc/passwd", ROOTS))
        self.assertIn("spoza worktree", outside_risky(f"python3 -m pytest {WT}/../../etc", ROOTS))
