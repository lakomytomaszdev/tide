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


class OutsideStepDirTest(unittest.TestCase):
    def test_own_step_directory_is_not_outside_the_worktree(self):
        # 04.11, review KAMP-16: testy recenzenta z jego katalogu out były „ścieżką spoza worktree”.
        step_dir = WT + ".review-1.step"
        command = f"python3 -m unittest discover -s {step_dir}/out -p 'test_review_*.py' -v"
        self.assertEqual(outside_risky(command, (WT, REPO, step_dir)), "")
        self.assertIn("spoza worktree", outside_risky(command, ROOTS))


class OutsideScriptTest(unittest.TestCase):
    def test_worktree_script_is_allowed_but_always_needs_consent(self):
        # 04.11, KAMP-1: `bash core/…/extract_incoming.sh` był odrzucany jak `bash -c`.
        self.assertEqual(outside_argv("bash core/tests/extract.sh", ROOTS), ["bash", "core/tests/extract.sh"])
        self.assertTrue(outside_allowed("bash core/tests/extract.sh", [], ROOTS))
        self.assertIn("przejrzyj", outside_risky("bash core/tests/extract.sh", ROOTS))
        self.assertIsNone(outside_argv("bash -c 'rm -rf ~'", ROOTS))
        self.assertIsNone(outside_argv("bash /etc/evil.sh", ROOTS))
        self.assertIsNone(outside_argv("bash ../../evil.sh", ROOTS))
        self.assertIsNone(outside_argv("bash core/tests/extract.sh"))
