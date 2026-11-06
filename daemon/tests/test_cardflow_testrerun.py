"""06.11 (etap A deterministycznie): Tide sam zamienia pełny przebieg testów na ponowienie padniętych modułów,
gdy kod się nie zmienił od przebiegu, który padł."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

from tide_daemon.cardflow import testrerun


class SubstituteTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(os.path.join(self.repo, "tools"))
        os.makedirs(os.path.join(self.repo, "pkg", "tests"))
        for part in ("pkg", "pkg/tests"):
            open(os.path.join(self.repo, part, "__init__.py"), "w").close()
        self.write("tools/test_runner.py", "")
        self.write("pkg/tests/test_good.py", "import unittest\nclass T(unittest.TestCase):\n"
                                              "    def test_ok(self):\n        pass\n")
        self.write("pkg/tests/test_bad.py", "import unittest\nclass T(unittest.TestCase):\n"
                                             "    def test_ok(self):\n        pass\n")
        self.write(".gitignore", "build/\n")
        git = ["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        subprocess.run(git + ["add", "-A"], check=True)
        subprocess.run(git + ["commit", "-qm", "init"], check=True)
        self.git = git

    def write(self, rel, text):
        with open(os.path.join(self.repo, rel), "w", encoding="utf-8") as fh:
            fh.write(text)

    def failed_run(self, step="K-1-PT-1-3", head=None, exit_code=2, failed=("pkg/tests/test_bad.py",)):
        run = os.path.join(self.repo, "build", "test-results", "run-x")
        os.makedirs(run, exist_ok=True)
        results = [{"module": "pkg/tests/test_good.py", "returncode": 0}] + \
                  [{"module": m, "returncode": 1} for m in failed]
        with open(os.path.join(run, "summary.json"), "w", encoding="utf-8") as fh:
            json.dump({"results": results}, fh)
        log = os.path.join(self.root, step + ".log")
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(f"Moduły: 2, JOBS=2, logi: {run}\n")
        sha = head or testrerun.head(self.repo)
        return [{"type": "outside.started", "step": step, "command": "make test-changed BASE=abc",
                 "worktree": self.repo, "head": sha, "log": log},
                {"type": "outside.finished", "step": step, "exit": exit_code}]

    def test_same_head_after_failure_runs_only_failed_modules(self):
        command, head, rerun_of = testrerun.substitute(self.failed_run(), self.repo, "make test-changed BASE=abc")
        self.assertEqual(rerun_of, "K-1-PT-1-3")
        self.assertTrue(head)
        self.assertIn("-s pkg/tests -p test_bad.py", command)
        self.assertNotIn("test_good.py", command)
        run = subprocess.run(["/bin/sh", "-c", command], cwd=self.repo, capture_output=True, text=True,
                             env={**os.environ, "PYTHONPATH": ""}, timeout=60)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("Ran 1 test", run.stderr)

    def test_failing_module_keeps_nonzero_exit(self):
        self.write("pkg/tests/test_bad.py", "import unittest\nclass T(unittest.TestCase):\n"
                                             "    def test_no(self):\n        self.fail()\n")
        subprocess.run(self.git + ["commit", "-qam", "bad"], check=True)
        command, _, _ = testrerun.substitute(self.failed_run(), self.repo, "make test")
        run = subprocess.run(["/bin/sh", "-c", command], cwd=self.repo, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 1)

    def test_no_substitution_when_code_changed_tree_dirty_or_forced(self):
        events = self.failed_run()
        self.write("pkg/tests/test_good.py", "# zmiana\n")
        self.assertEqual(testrerun.substitute(events, self.repo, "make test")[0], "make test", "brudne drzewo")
        subprocess.run(self.git + ["commit", "-qam", "change"], check=True)
        self.assertEqual(testrerun.substitute(events, self.repo, "make test")[0], "make test", "nowy HEAD")
        events = self.failed_run(step="K-1-PT-1-4")
        self.assertEqual(testrerun.substitute(events, self.repo, "TIDE_FULL_RUN=1 make test")[0],
                         "TIDE_FULL_RUN=1 make test")

    def test_no_substitution_after_success_without_report_or_for_other_commands(self):
        self.assertEqual(testrerun.substitute(self.failed_run(exit_code=0), self.repo, "make test")[0], "make test")
        self.assertEqual(testrerun.substitute(self.failed_run(failed=()), self.repo, "make test")[0], "make test")
        self.assertEqual(testrerun.substitute(self.failed_run(), self.repo, "make install")[0], "make install")
        self.assertEqual(testrerun.substitute(self.failed_run(failed=("../x.py",)), self.repo, "make test")[0],
                         "make test")

    def test_projects_without_tide_runner_are_untouched(self):
        events = self.failed_run()
        os.remove(os.path.join(self.repo, "tools", "test_runner.py"))
        subprocess.run(self.git + ["commit", "-qam", "no runner"], check=True)
        self.assertEqual(testrerun.substitute(events, self.repo, "make test"), ("make test", "", ""))

    def test_full_test_recognition(self):
        for command in ("make test", "make test-changed BASE=abc", "JOBS=2 make test-changed BASE=abc JOBS=4"):
            self.assertTrue(testrerun.is_full_test(command), command)
        for command in ("make test-daemon", "make test && rm -rf x", "bash run.sh", "make test-changed; ls"):
            self.assertFalse(testrerun.is_full_test(command), command)


if __name__ == "__main__":
    unittest.main()
