"""K-102 #5: reproduktor czasu zatwierdzania planu (tools/approval_benchmark.py)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(ROOT, "tools", "approval_benchmark.py")
SMALL = ["--plans", "20", "--branches", "3", "--tickets", "3", "--worktrees", "2"]


class ApprovalBenchmarkTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.TemporaryDirectory(prefix="approval-bench-test-")
        self.addCleanup(self.base.cleanup)
        self.tmp = os.path.join(self.base.name, "tmp")        # katalog tymczasowy skryptu
        self.home = os.path.join(self.base.name, "home")
        os.makedirs(self.tmp)
        os.makedirs(self.home)

    def run_script(self, *args):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "TIDE_"))}
        env.update(TMPDIR=self.tmp, HOME=self.home, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, env=env, timeout=300)

    def test_prints_one_json_line_and_approves_with_at_most_one_plan_read(self):
        done = self.run_script(*SMALL)
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1, done.stdout)
        result = json.loads(lines[0])
        self.assertEqual({"seconds", "planReads", "tickets", "tideRoot", "firstTicketSeconds"}, set(result))
        self.assertEqual(result["tickets"], 3)
        self.assertIsInstance(result["seconds"], float)
        self.assertGreaterEqual(result["planReads"], 1)
        self.assertLessEqual(result["planReads"], 1)
        self.assertEqual(result["tideRoot"], os.path.realpath(ROOT))

    def test_leaves_no_real_state_and_no_temporary_files(self):
        self.assertEqual(self.run_script(*SMALL).returncode, 0)
        self.assertEqual(os.listdir(self.home), [])           # HOME zostaje nietknięty (rejestr nie trafił do ~)
        self.assertEqual(os.listdir(self.tmp), [])            # katalog roboczy pomiaru sprzątnięty

    def test_refuses_state_home_outside_the_temporary_directory_before_any_write(self):
        outside = os.path.join(self.base.name, "outside-state")
        done = self.run_script(*SMALL, "--state-home", outside)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("XDG_STATE_HOME", done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertFalse(os.path.exists(outside))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_refuses_repository_outside_the_temporary_directory_before_any_write(self):
        outside = os.path.join(self.base.name, "outside-repo")
        done = self.run_script(*SMALL, "--repo", outside)
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertIn("repozytorium", done.stderr)
        self.assertFalse(os.path.exists(outside))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_refuses_a_symlink_escaping_the_temporary_directory(self):
        elsewhere = os.path.join(self.base.name, "elsewhere")
        os.makedirs(elsewhere)
        link = os.path.join(self.tmp, "link")
        os.symlink(elsewhere, link)
        done = self.run_script(*SMALL, "--state-home", os.path.join(link, "state"))
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertEqual(os.listdir(elsewhere), [])

    def test_base_ref_measures_in_a_temporary_worktree_and_removes_it(self):
        before = subprocess.run(["git", "-C", ROOT, "worktree", "list", "--porcelain"], capture_output=True,
                                text=True).stdout
        done = self.run_script(*SMALL, "--base-ref", "HEAD")
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout)
        self.assertEqual(result["baseRef"], "HEAD")
        self.assertNotEqual(result["tideRoot"], os.path.realpath(ROOT))
        self.assertFalse(os.path.exists(result["tideRoot"]))
        after = subprocess.run(["git", "-C", ROOT, "worktree", "list", "--porcelain"], capture_output=True,
                               text=True).stdout
        self.assertEqual(before, after)

    def test_rejects_a_directory_that_is_not_a_tide_tree(self):
        done = self.run_script(*SMALL, "--tide-root", self.home)
        self.assertEqual(done.returncode, 2, done.stderr)


if __name__ == "__main__":
    unittest.main()
