"""PT-174: artefakty prawdziwych poleceń i tekstowe formaty wyników."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tide_daemon.box import test_artifacts as artifacts
from tide_daemon.cardflow import handoff

FIXTURES = Path(__file__).parent / "fixtures" / "test-output"


class ParserTest(unittest.TestCase):
    def test_polish_vstest_summary_from_legalapp(self):
        for status, failed, passed in (("Niepowodzenie!", 46, 1191), ("Powodzenie!", 0, 1237)):
            with self.subTest(status=status):
                output = (f"{status} — niepowodzenie:    {failed}, powodzenie:  {passed}, "
                          "pominięto:     1, łącznie:  1238, czas trwania: 19 m - "
                          "LegalApp.Tests.Integration.dll (net10.0)")
                self.assertEqual(artifacts.counts(output), {"passed": passed, "failed": failed})

    def test_known_fixtures(self):
        for name, passed, failed in (("pytest", 5, 2), ("unittest", 5, 2), ("dotnet", 15, 2),
                                     ("playwright", 7, 2), ("npm", 18, 2)):
            with self.subTest(name=name):
                self.assertEqual(artifacts.counts((FIXTURES / f"{name}.txt").read_text()),
                                 {"passed": passed, "failed": failed})

    def test_unknown_and_ansi_and_success(self):
        self.assertEqual(artifacts.counts((FIXTURES / "unknown.txt").read_text()), {})
        self.assertEqual(artifacts.counts("\x1b[32m4 passed\x1b[0m"), {"passed": 4, "failed": 0})
        self.assertEqual(artifacts.counts("Ran 3 tests in 0.1s\n\nOK\n"), {"passed": 3, "failed": 0})
        self.assertEqual(artifacts.counts("  3 passing (10ms)\n  1 failing\n"), {"passed": 3, "failed": 1})
        self.assertEqual(artifacts.counts("Test Suites: 2 passed, 2 total\nTests: 15 passed, 15 total"),
                         {"passed": 15, "failed": 0})

    def test_make_unittest_suites_are_added(self):
        self.assertEqual(artifacts.counts("Ran 3 tests in 0.1s\n\nOK\nRan 2 tests in 0.2s\n\nFAILED (failures=1)"),
                         {"passed": 4, "failed": 1})
        self.assertEqual(artifacts.counts("Ran 5 tests in 0.1s\n\n"
                                         "FAILED (expected failures=1, unexpected successes=1, skipped=1)"),
                         {"passed": 2, "failed": 1})


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / "step" / "out"
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()

    def invoke(self, output="obcy format", code=0):
        command = [sys.executable, "-c", "import sys; print(sys.argv[1]); sys.exit(int(sys.argv[2]))", output, str(code)]
        env = dict(os.environ, TIDE_STEP=str(self.out.parent))
        result = subprocess.run([sys.executable, artifacts.__file__, *command], cwd=self.worktree,
                                env=env, capture_output=True)
        return result, command

    def summary(self):
        return json.loads((self.out / "tests" / "summary.json").read_text())

    def test_unknown_preserves_exit_code_and_captures_log_outside_worktree(self):
        result, command = self.invoke(code=23)
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(result.stdout, b"obcy format\n")
        self.assertEqual((self.out / "tests" / "1.log").read_bytes(), result.stdout)
        row = self.summary()[0]
        self.assertEqual(row["argv"], command)
        self.assertEqual(row["exit_code"], 23)
        self.assertGreaterEqual(row["duration"], 0)
        self.assertIn("started_at", row)
        self.assertNotIn("passed", row)
        self.assertNotIn("failed", row)
        self.assertEqual(list(self.worktree.iterdir()), [])

    def test_no_calls_create_no_artifacts(self):
        self.assertFalse(self.out.exists())
        self.assertEqual(artifacts.run([], self.out), 2)
        self.assertFalse(self.out.exists())

    def test_appends_and_parallel_calls_keep_each_result(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda n: self.invoke(f"{n} passed", n), range(4)))
        self.assertEqual(sorted(row["exit_code"] for row in self.summary()), list(range(4)))
        self.assertEqual(len(list((self.out / "tests").glob("*.log"))), 4)
        for result, _ in results:
            self.assertIn(result.returncode, range(4))

    def test_child_inherits_environment_cwd_stdin_and_stderr(self):
        env = dict(os.environ, TIDE_STEP=str(self.out.parent), CUSTOM="wartość")
        command = [sys.executable, "-c", "import os,sys,json; print(json.dumps(dict(os.environ))); "
                   "print(os.getcwd()); print(input()); print('stderr', file=sys.stderr)"]
        result = subprocess.run([sys.executable, artifacts.__file__, *command], env=env,
                                cwd=self.worktree, input=b"stdin\n", capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.decode().splitlines()
        child_env = json.loads(next(line for line in lines if line.startswith("{")))
        self.assertEqual(child_env, env)
        for value in (str(self.worktree), "stdin", "stderr"):
            self.assertIn(value, lines)

    def test_parser_exception_does_not_change_exit_code(self):
        with mock.patch.object(artifacts, "counts", side_effect=ValueError("obcy parser")):
            code = artifacts.run([sys.executable, "-c", "raise SystemExit(19)"], self.out)
        self.assertEqual(code, 19)
        self.assertEqual(self.summary()[0]["exit_code"], 19)
        self.assertNotIn("passed", self.summary()[0])

    def test_damaged_summary_does_not_change_command_exit_code(self):
        (self.out / "tests").mkdir(parents=True)
        (self.out / "tests" / "summary.json").write_text("niepoprawny JSON")
        result, _ = self.invoke(code=29)
        self.assertEqual(result.returncode, 29)
        self.assertIn(b"tide-test:", result.stderr)
        self.assertTrue((self.out / "tests" / "1.log").exists())


class HandoffTest(unittest.TestCase):
    def test_host_command_policy_keeps_original_boundary(self):
        text = handoff.command_policy(["make test"], ["make install"], step_dir="/step")
        self.assertIn("Przed poleceniem spoza listy dozwolonych zapisz wynik `denied` z pełnym poleceniem i zakończ krok.", text)
        self.assertNotIn("tide-test", text)
        self.assertEqual(text, handoff.command_policy(["make test"], ["make install"], step_dir="/step", box=False))

    def test_box_instruction_includes_custom_verify(self):
        text = handoff.box_tests({"verify": ["./check-project --quick", "make test"]})
        self.assertIn("testy uruchamiaj przez `tide-test", text)
        for command in ("./check-project --quick", "npm test", "npx playwright test", "dotnet test", "pytest"):
            self.assertIn(command, text)

    def test_evidence_lists_sizes_and_no_tests_and_host_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            step = Path(tmp) / "step"
            out = step / "out"
            out.mkdir(parents=True)
            source = {"runner": "box", "stepDir": str(step)}
            self.assertIn("krok bez testów", handoff.test_evidence(source))
            (out / "tests").mkdir()
            for name, content in (("tests/1.log", "12345"), ("tests/summary.json", "[]"),
                                  ("proxy-denied.log", "denied")):
                (out / name).write_text(content)
            text = handoff.test_evidence(source)
            for name, size in (("tests/1.log", 5), ("tests/summary.json", 2), ("proxy-denied.log", 6)):
                self.assertIn(f"`{out / name}` — {size} B", text)
            self.assertIn("świeży artefakt = dowód; nie proś o ponowny przebieg", text)
            self.assertNotIn("krok bez testów", text)
            self.assertEqual(handoff.test_evidence({"stepDir": str(step)}), "")

    def test_evidence_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            step = Path(tmp) / "step"
            out = step / "out"
            out.mkdir(parents=True)
            (out / "tests").symlink_to(Path(tmp), target_is_directory=True)
            (out / "proxy-denied.log").symlink_to("/etc/passwd")
            text = handoff.test_evidence({"runner": "box", "stepDir": str(step)})
            self.assertNotIn("proxy-denied.log", text)
            self.assertIn("krok bez testów", text)
