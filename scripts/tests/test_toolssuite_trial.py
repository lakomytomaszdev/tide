"""Ocena dowodów równoległych prób PT-185, bez prawdziwego Incusa i repozytorium projektu."""
import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("toolssuite_trial", ROOT / "scripts/box/toolssuite-trial.py")
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def run(name, port, start, end):
    marker = {"port": str(port), "e2e": str(port), "base": str(port), "chrome": "/usr/local/bin/tide-chrome",
              "NO_PROXY": trial.BOX_NO_PROXY, "no_proxy": trial.BOX_NO_PROXY, "cwd": f"/repo-worktrees/{name}"}
    return {"name": name, "code": 0, "proxy_denied": "",
            "markers": [{**marker, "phase": "start", "ms": start}, {**marker, "phase": "end", "ms": end}]}


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.runs = [run("left", 4300, 100, 400), run("right", 4310, 200, 500)]

    def test_overlapping_distinct_worktrees_and_blocks_pass(self):
        self.assertEqual(trial.assess(self.runs, "parallel"), [])

    def test_clients_launched_together_do_not_prove_parallel_execution(self):
        self.runs[1]["markers"][0]["ms"] = 401
        self.assertIn("przebiegi nie nakładały się w pudełku", trial.assess(self.runs, "parallel"))

    def test_collision_or_reused_worktree_is_rejected(self):
        self.runs[1] = run("left", 4300, 200, 500)
        errors = trial.assess(self.runs, "parallel")
        self.assertIn("bloki portów nie są rozłączne", errors)
        self.assertIn("przebiegi użyły tego samego worktree", errors)

    def test_missing_evidence_failed_command_and_proxy_denials_are_not_green(self):
        for change in ({"markers": []}, {"code": 1}, {"proxy_denied": None},
                       {"proxy_denied": "localhost denied"}):
            with self.subTest(change=change):
                runs = copy.deepcopy(self.runs)
                runs[0].update(change)
                self.assertTrue(trial.assess(runs, "parallel"))

    def test_karma_requires_chrome_and_full_no_proxy(self):
        one = [self.runs[0]]
        self.assertEqual(trial.assess(one, "karma"), [])
        for key in ("chrome", "NO_PROXY", "no_proxy"):
            with self.subTest(key=key):
                broken = copy.deepcopy(one)
                broken[0]["markers"][0][key] = ""
                self.assertTrue(trial.assess(broken, "karma"))

    def test_expected_scenarios_and_only_explicit_probe_fields(self):
        shell = trial.command("parallel")[-1]
        self.assertIn("npm install --save-dev angular-eslint@^19", shell)
        self.assertIn("npm run lint", shell)
        self.assertIn("npx playwright test --workers=2", shell)
        self.assertIn("--browsers=ChromeHeadless --watch=false", trial.command("karma")[-1])
        self.assertNotIn("JSON.stringify(process.env)", shell)
        self.assertEqual(trial.markers('npm log\nPT185 {"phase":"start"}\n'), [{"phase": "start"}])


if __name__ == "__main__":
    unittest.main()
