"""PT-251: publiczne wejścia w świeżych procesach oraz własność i dziedziczenie izolacji."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import test_isolation as isolation, test_runner as runner

ROOT = runner.ROOT
MODULE = "daemon.tests.test_state_isolation"


class DirectIsolationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = dict(os.environ)
        for key in (isolation.MARKER, isolation.STATE_DIR, isolation.ORIGINAL_ROOTS):
            self.env.pop(key, None)
        self.home = self.root / "home"
        self.old_state = self.root / "user-state"
        self.home.mkdir()
        self.old_state.mkdir()
        self.env.update(HOME=str(self.home), XDG_STATE_HOME=str(self.old_state))

    def command(self, entry):
        if entry == "name":
            return [sys.executable, "-m", "unittest", MODULE]
        if entry == "discover":
            return [sys.executable, "-m", "unittest", "discover", "-s", "daemon/tests", "-t", ".",
                    "-p", "test_state_isolation.py"]
        return [sys.executable, "-m", "pytest", "-q", "daemon/tests/test_state_isolation.py"]

    def run_probe(self, entry="name", env=None, name="report"):
        report = self.root / (name + ".json")
        env = dict(self.env if env is None else env, TIDE_TEST_ISOLATION_REPORT=str(report))
        proc = subprocess.run(self.command(entry), cwd=ROOT, env=env, capture_output=True, text=True, timeout=45)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(report.read_text())

    def assert_probe(self, data, owned=True):
        state = Path(data["state"])
        self.assertNotEqual(state, self.old_state)
        self.assertEqual(data["home"], str(self.home), "inicjalizacja nie zmienia HOME")
        self.assertEqual(data["roots"], {"xdg_state_home": str(self.old_state),
                         "default_tide_state_home": str(self.home / ".local/state/tide")})
        self.assertTrue(all(Path(path).is_relative_to(state) for path in data["paths"]))
        self.assertEqual(data["child"]["state"], str(state))
        self.assertEqual(state.exists(), not owned, "sprzątanie należy wyłącznie do właściciela")
        self.assertEqual(list(self.old_state.iterdir()), [])
        self.assertEqual(list(self.home.iterdir()), [])

    def test_unittest_by_name_isolates_before_product_and_cleans_up(self):
        self.assert_probe(self.run_probe())

    def test_unittest_discover_isolates_before_product_and_cleans_up(self):
        self.assert_probe(self.run_probe("discover"))

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "brak pytest w tym interpreterze")
    def test_pytest_module_path_isolates_before_product_and_cleans_up(self):
        self.assert_probe(self.run_probe("pytest"))

    def test_two_simultaneous_direct_processes_have_different_live_directories(self):
        processes = []
        for index in range(2):
            env = dict(self.env, TIDE_TEST_ISOLATION_WAIT="1",
                       TIDE_TEST_ISOLATION_REPORT=str(self.root / f"parallel-{index}.json"))
            proc = subprocess.Popen(self.command("name"), cwd=ROOT, env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            processes.append(proc)
            self.addCleanup(self.stop, proc)
        # Czytanie z limitem czasu: potwierdzenie gotowości zamiast opóźnienia na ślepo.
        pool = ThreadPoolExecutor(2)
        try:
            futures = [pool.submit(proc.stdout.readline) for proc in processes]
            data = [json.loads(future.result(timeout=30)) for future in futures]
        finally:
            if any(not future.done() for future in futures):
                for proc in processes:
                    proc.kill()
            pool.shutdown(wait=True)
        self.assertNotEqual(data[0]["state"], data[1]["state"])
        for item in data:
            self.assertTrue(Path(item["state"]).is_dir())
        for proc in processes:
            _, err = proc.communicate("\n", timeout=20)
            self.assertEqual(proc.returncode, 0, err)
        for item in data:
            self.assert_probe(item)

    @staticmethod
    def stop(proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate()

    def test_explicit_runner_marker_reuses_directory_without_taking_ownership(self):
        state = self.root / "runner-state"
        state.mkdir()
        env = isolation.state_environment(state, self.env)
        self.assert_probe(self.run_probe(env=env), owned=False)
        self.assertTrue(list(state.rglob("K-1.jsonl")), "prawdziwa kopia dziennika")

    def test_marker_without_matching_directory_does_not_trust_inherited_xdg(self):
        env = dict(self.env, **{isolation.MARKER: "1", isolation.STATE_DIR: str(self.root / "missing")})
        self.assert_probe(self.run_probe(env=env))

    def test_unset_xdg_captures_default_before_replacement(self):
        env = dict(self.env)
        env.pop("XDG_STATE_HOME")
        data = self.run_probe(env=env)
        self.assertEqual(data["roots"]["xdg_state_home"], str(self.home / ".local/state"))
        self.assertFalse(Path(data["state"]).exists())
        self.assertEqual(list(self.home.iterdir()), [])

    def test_public_runner_owns_state_and_keeps_existing_log_diagnostics(self):
        output = self.root / "logs"
        output.mkdir()
        report = self.root / "runner-report.json"
        with patch.dict(os.environ, dict(self.env, TIDE_TEST_ISOLATION_REPORT=str(report)), clear=True):
            result = runner.run_module("daemon/tests/test_state_isolation.py", output)
        self.assertEqual(result["returncode"], 0, Path(result["log"]).read_text())
        self.assert_probe(json.loads(report.read_text()))
        self.assertTrue(Path(result["log"]).is_file())

    def test_failed_runner_module_cleans_state_but_retains_failure_log_and_report(self):
        package = self.root / "example"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "test_failure.py").write_text('''import json, os, unittest
from pathlib import Path
class Failure(unittest.TestCase):
    def test_failure(self):
        state = os.environ["XDG_STATE_HOME"]
        assert state == os.environ["TIDE_TEST_STATE_DIR"]
        assert os.environ["TIDE_TEST_ISOLATED"] == "1"
        Path(os.environ["TIDE_TEST_ISOLATION_REPORT"]).write_text(json.dumps({"state": state}))
        Path(state, "failure-evidence").write_text("synthetic")
        self.fail("synthetic failure diagnostic")
''')
        output = self.root / "logs"
        output.mkdir()
        report = self.root / "failure-state.json"
        with patch.dict(os.environ, dict(self.env, TIDE_TEST_ISOLATION_REPORT=str(report)), clear=True):
            result = runner.run_module("example/test_failure.py", output, root=self.root)
        self.assertEqual(result["returncode"], 1)
        self.assertFalse(Path(json.loads(report.read_text())["state"]).exists())
        self.assertIn("synthetic failure diagnostic", Path(result["log"]).read_text())
        self.assertEqual(result["failures"], ["example.test_failure.Failure.test_failure"])
        self.assertTrue((output / "example__test_failure.json").is_file())

    @unittest.skipUnless(hasattr(os, "fork"), "wymaga fork")
    def test_fork_does_not_inherit_ownership_of_direct_process_directory(self):
        report = self.root / "fork-state.json"
        code = '''
import json, os, sys
from pathlib import Path
from daemon.tests import TEST_STATE_HOME
pid = os.fork()
if pid == 0:
    sys.exit(0)
assert os.waitpid(pid, 0)[1] == 0
assert Path(TEST_STATE_HOME).is_dir(), "potomek usunął stan rodzica"
Path(sys.argv[1]).write_text(json.dumps({"state": TEST_STATE_HOME}))
'''
        proc = subprocess.run([sys.executable, "-c", code, str(report)], cwd=ROOT, env=self.env,
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(Path(json.loads(report.read_text())["state"]).exists())

    def test_patch_dict_overrides_only_with_synthetic_state_and_preserves_original_roots(self):
        state = self.root / "package-state"
        state.mkdir()
        env = isolation.state_environment(state, self.env)
        code = '''
import json, os, sys
from pathlib import Path
from unittest.mock import patch
from daemon.tests import TEST_STATE_HOME
from tide_daemon.cardflow.journal import Journal
from tools.test_isolation import state_environment
roots = os.environ["TIDE_TEST_ORIGINAL_STATE_ROOTS"]
with patch.dict(os.environ, {"XDG_STATE_HOME": sys.argv[1]}):
    journal = Journal(str(Path(sys.argv[1]) / "board"))
    journal.append("K-3", "synthetic.override")
    assert Path(journal.mirror_path("K-3")).is_relative_to(sys.argv[1])
    assert os.environ["TIDE_TEST_ORIGINAL_STATE_ROOTS"] == roots
    child_env = state_environment(sys.argv[1])
    assert child_env["TIDE_TEST_ORIGINAL_STATE_ROOTS"] == roots
assert os.environ["XDG_STATE_HOME"] == TEST_STATE_HOME
'''
        proc = subprocess.run([sys.executable, "-c", code, str(self.root / "override")], cwd=ROOT,
                              env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(list((self.root / "override").rglob("K-3.jsonl")))
        self.assertEqual(list(self.old_state.iterdir()), [])
