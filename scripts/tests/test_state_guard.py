"""PT-254: oczekiwana porażka osobnych scenariuszy, bez dotykania stanu użytkownika."""
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

from tools import test_guard as guard, test_isolation as isolation, test_runner as runner

MODULE = "daemon.tests.guard_probe"
FILE = "daemon/tests/guard_probe.py"


class StateGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="tide-guard-control-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.state = self.root / "user-state"
        self.roots = [self.home / ".local/state/tide", self.state / "tide"]
        for root in self.roots:
            root.mkdir(parents=True)
            (root / "existing").write_text("synthetic original")
        self.alias = self.root / "alias"
        self.alias.symlink_to(self.roots[1], target_is_directory=True)
        self.env = dict(os.environ)
        for key in (isolation.MARKER, isolation.STATE_DIR, isolation.ORIGINAL_ROOTS, guard.ACTIVE):
            self.env.pop(key, None)
        self.env.update(HOME=str(self.home), XDG_STATE_HOME=str(self.state),
                        TIDE_GUARD_PROBE_OLD_STATE=str(self.state),
                        TIDE_GUARD_PROBE_OTHER=str(self.root / "other"))
        (self.root / "other").write_text("synthetic other")

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes() if path.is_file() else None
                for root in self.roots for path in [root, *root.rglob("*")]}

    def command(self, entry):
        if entry == "pytest":
            return [sys.executable, "-m", "pytest", "-q", "-s", FILE]
        if entry == "discover":
            return [sys.executable, "-m", "unittest", "discover", "-s", "daemon/tests", "-t", ".",
                    "-p", "guard_probe.py"]
        return [sys.executable, "-m", "unittest", MODULE]

    def probe_env(self, action, target=None):
        return dict(self.env, TIDE_GUARD_PROBE_ACTION=action,
                    TIDE_GUARD_PROBE_TARGET=str(target or self.roots[1] / "existing"))

    def probe(self, action, target=None, entry="unittest", **kwargs):
        before = self.snapshot()
        result = subprocess.run(self.command(entry), cwd=runner.ROOT,
                                env=self.probe_env(action, target), capture_output=True,
                                text=True, timeout=40, **kwargs)
        self.assertEqual(self.snapshot(), before, result.stdout + result.stderr)
        self.assertEqual((self.root / "other").read_text(), "synthetic other")
        self.assertEqual(result.returncode, guard.FAILURE, result.stdout + result.stderr)
        self.assertIn("INNER GREEN", result.stdout)
        records = [json.loads(line.split("TIDE TEST GUARD: ", 1)[1])
                   for line in result.stderr.splitlines() if "TIDE TEST GUARD: " in line]
        self.assertTrue(records, result.stderr)
        self.assertTrue(all(record.get("scenario") and record.get("pid") for record in records), records)
        return result, records

    def test_python_mutations_of_both_roots_and_symlink_alias_are_blocked(self):
        for root in [*self.roots, self.alias]:
            for action in ("create", "overwrite", "unlink", "rename", "rename-into", "dirfd"):
                with self.subTest(root=root, action=action):
                    self.probe(action, root / ("missing" if action in ("create", "dirfd") else "existing"))

    def test_shell_really_creates_overwrites_removes_and_renames_through_symlink(self):
        for root in [*self.roots, self.alias]:
            for action in ("create", "overwrite", "unlink", "rename", "rename-into"):
                with self.subTest(root=root, action=action):
                    self.probe("shell-" + action, root / ("missing" if action == "create" else "existing"))

    def test_service_and_grandchild_default_journal_cannot_turn_violation_green(self):
        for action in ("service", "grandchild", "cleanenv"):
            with self.subTest(action=action):
                result, records = self.probe(action)
                self.assertIn("SERVICE SWALLOWED", result.stdout)
                self.assertTrue(any("cardflow" in path for record in records for path in record["targets"]))
                self.assertTrue(any(record["pid"] != record["scenario"] for record in records))

    def test_detached_service_stays_protected_after_its_parent_exits(self):
        result, records = self.probe("orphan")
        self.assertIn("SERVICE SWALLOWED", result.stdout)
        self.assertTrue(all("targets" in record for record in records), records)
        self.assertTrue(any("cardflow" in target for record in records for target in record["targets"]))

    def test_orphan_violation_after_green_test_process_exit_still_fails_scenario(self):
        result, records = self.probe("late-orphan")
        self.assertIn("SERVICE SWALLOWED", result.stdout)
        self.assertTrue(all("targets" in record for record in records), records)

    def test_leftover_detached_child_is_reaped_without_hanging_green_scenario(self):
        result = subprocess.run(self.command("unittest"), cwd=runner.ROOT, env=self.probe_env("leaked-child"),
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        pid = int((self.root / "leaked-pid").read_text())
        self.assertFalse(Path(f"/proc/{pid}").exists(), "pozostał potomek zakończonego scenariusza")

    def test_hup_does_not_skip_final_wait_for_adopted_zombie(self):
        code = '''
import os, select, sys
from pathlib import Path
from unittest.mock import patch
from tools import test_guard
test_guard._checked(test_guard._libc().prctl(36, 1, 0, 0, 0))
record = Path(sys.argv[1])
root = os.fork()
if root == 0:
    child = os.fork()
    if child == 0:
        os._exit(0)
    record.write_text(str(child))
    os._exit(0)
os.waitid(os.P_PID, root, os.WEXITED | os.WNOWAIT)
child = int(record.read_text())
os.waitid(os.P_PID, child, os.WEXITED | os.WNOWAIT)
real_waitpid = os.waitpid
root_reaped = False
def waitpid(pid, flags):
    global root_reaped
    if flags == os.WNOHANG:
        if not root_reaped:
            root_reaped = True
            return real_waitpid(root, 0)
        # Wymuszamy kolejność: wcześniejszy WNOHANG nie zauważa jeszcze końca
        # potomka, lecz poll już zwraca HUP. Zombie jest rzeczywistym procesem.
        return 0, 0
    return real_waitpid(pid, flags)
class Hup:
    def register(self, *args):
        pass
    def poll(self, *args):
        return [(42, select.POLLHUP)]
try:
    with patch.object(test_guard.os, "waitpid", side_effect=waitpid), patch.object(test_guard.select, "poll", Hup):
        assert test_guard._supervise(root, 42, []) == 0
    assert not Path(f"/proc/{child}").exists(), "HUP pozostawił nieodebrany status potomka"
finally:
    try:
        real_waitpid(child, 0)
    except ChildProcessError:
        pass
'''
        result = subprocess.run([sys.executable, "-c", code, str(self.root / "zombie-pid")],
                                cwd=runner.ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_detached_process_can_still_write_unprotected_state(self):
        result = subprocess.run(self.command("unittest"), cwd=runner.ROOT, env=self.probe_env("orphan-success"),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("TIDE TEST GUARD:", result.stderr)
        self.assertEqual((self.root / "other").read_text(), "legal orphan")

    def test_futimens_with_null_path_preserves_legal_fd_metadata_update(self):
        result = subprocess.run(self.command("unittest"), cwd=runner.ROOT, env=self.probe_env("futimens"),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("TIDE TEST GUARD:", result.stderr)

    def test_removing_external_symlink_does_not_mutate_its_protected_target(self):
        before = self.snapshot()
        result = subprocess.run(self.command("unittest"), cwd=runner.ROOT,
                                env=self.probe_env("unlink-alias", self.alias),
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.alias.is_symlink())
        self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "brak pytest w interpreterze")
    def test_pytest_expected_failure_python_service_and_foreign_process(self):
        for action in ("create", "unlink", "rename", "service", "grandchild",
                       "shell-create", "shell-overwrite", "shell-unlink", "shell-rename"):
            with self.subTest(action=action):
                self.probe(action, self.alias / ("missing" if action.endswith("create") else "existing"),
                           entry="pytest")

    def test_discover_cannot_swallow_violation(self):
        self.probe("overwrite", entry="discover")

    def test_fake_environment_marker_does_not_disable_preparation(self):
        self.env[guard.ACTIVE] = str(os.getpid())
        self.probe("overwrite")

    def test_real_daemon_on_private_bus_cannot_leave_green_scenario(self):
        commands = [[sys.executable, "-m", "unittest", "daemon.tests.guard_daemon_probe"]]
        if importlib.util.find_spec("pytest"):
            commands.append([sys.executable, "-m", "pytest", "-q", "-s", "daemon/tests/guard_daemon_probe.py"])
        for command in commands:
            with self.subTest(command=command):
                before = self.snapshot()
                result = subprocess.run(command, cwd=runner.ROOT, env=self.env,
                                        capture_output=True, text=True, timeout=45)
                self.assertEqual(self.snapshot(), before, result.stdout + result.stderr)
                if result.returncode == 0 and "skipped" in result.stdout + result.stderr:
                    self.skipTest("prywatna magistrala D-Bus niedostępna w tym środowisku")
                self.assertEqual(result.returncode, guard.FAILURE, result.stdout + result.stderr)
                self.assertIn("INNER GREEN", result.stdout)
                self.assertIn("TIDE TEST GUARD:", result.stderr)
                self.assertIn("/tide/cardflow", result.stderr)

    def test_original_roots_are_canonicalized_before_isolation(self):
        alias = self.root / "state-link"
        alias.symlink_to(self.state, target_is_directory=True)
        self.env["XDG_STATE_HOME"] = str(alias)
        self.probe("create", self.roots[1] / "missing")

    def test_unset_original_xdg_still_protects_default_tide_root(self):
        self.env.pop("XDG_STATE_HOME")
        self.probe("create", self.roots[0] / "missing")

    def test_file_symlink_and_dangling_file_symlink_cannot_bypass_guard(self):
        for name in ("existing", "missing"):
            alias = self.root / (name + "-link")
            alias.symlink_to(self.roots[1] / name)
            for action in ("overwrite", "shell-overwrite"):
                with self.subTest(name=name, action=action):
                    self.probe(action, alias)

    def test_renaming_ancestor_of_protected_root_is_blocked(self):
        self.probe("ancestor", self.state)

    def test_inherited_writable_descriptor_is_checked_before_write(self):
        target = self.roots[0] / "existing"
        with target.open("r+b") as stream:
            self.env["TIDE_GUARD_PROBE_FD"] = str(stream.fileno())
            self.probe("fdwrite", target, pass_fds=(stream.fileno(),))

    def test_exit_zero_without_atexit_does_not_clear_violation(self):
        isolated = self.root / "isolated"
        isolated.mkdir()
        self.env = isolation.state_environment(isolated, self.env)
        result = subprocess.run(self.command("unittest"), cwd=runner.ROOT,
                                env=self.probe_env("exit"), capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, guard.FAILURE, result.stderr)
        self.assertIn("TIDE TEST GUARD:", result.stderr)
        self.assertEqual((self.roots[1] / "existing").read_text(), "synthetic original")

    def test_public_runner_reports_scenario_failure_with_green_unittest_report(self):
        logs = self.root / "logs"
        logs.mkdir()
        with patch.dict(os.environ, self.probe_env("shell-overwrite", self.alias / "existing"), clear=True):
            result = runner.run_module(FILE, logs)
        self.assertEqual(result["returncode"], guard.FAILURE, Path(result["log"]).read_text())
        self.assertEqual(result["tests"], 1)
        self.assertEqual(result["failures"], [])
        self.assertIn("TIDE TEST GUARD:", Path(result["log"]).read_text())
        self.assertEqual((self.roots[1] / "existing").read_text(), "synthetic original")

    def test_independent_service_can_write_without_failing_unrelated_scenario(self):
        process = subprocess.Popen(self.command("unittest"), cwd=runner.ROOT, env=self.probe_env("wait"),
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, process)
        with ThreadPoolExecutor(1) as pool:
            ready = pool.submit(process.stdout.readline)
            try:
                self.assertEqual(ready.result(timeout=20).strip(), "GUARD READY")
            except BaseException:
                process.kill()
                raise
        # Ten proces jest rodzeństwem scenariusza, a nie jego potomkiem.
        legal = subprocess.run([sys.executable, "-c",
                                'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("legal service")',
                                str(self.roots[1] / "existing")], check=True, timeout=10)
        self.assertEqual(legal.returncode, 0)
        out, err = process.communicate("\n", timeout=20)
        self.assertEqual(process.returncode, 0, out + err)
        self.assertNotIn("TIDE TEST GUARD:", err)
        self.assertEqual((self.roots[1] / "existing").read_text(), "legal service")

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.kill()
        process.communicate()

    def test_preparation_failure_never_runs_product_unprotected(self):
        isolated = self.root / "isolated"
        isolated.mkdir()
        self.env = isolation.state_environment(isolated, self.env)
        code = '''
from unittest.mock import patch
from tools import test_guard
with patch.object(test_guard, "_install", side_effect=OSError("synthetic setup failure")):
    from daemon.tests import TEST_STATE_HOME
print("UNPROTECTED PRODUCT")
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=runner.ROOT, env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, guard.FAILURE, result.stderr)
        self.assertIn("setup_error", result.stderr)
        self.assertIn("synthetic setup failure", result.stderr)
        self.assertNotIn("UNPROTECTED PRODUCT", result.stdout)

    def test_listener_transfer_failure_exits_without_unprotected_product_or_deadlock(self):
        isolated = self.root / "isolated"
        isolated.mkdir()
        env = isolation.state_environment(isolated, self.env)
        code = '''
from unittest.mock import patch
from tools import test_guard
with patch.object(test_guard.socket.socket, "sendmsg", side_effect=OSError("synthetic transfer failure")):
    from daemon.tests import TEST_STATE_HOME
print("UNPROTECTED PRODUCT")
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=runner.ROOT, env=env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, guard.FAILURE, result.stderr)
        self.assertIn("setup_error", result.stderr)
        self.assertNotIn("UNPROTECTED PRODUCT", result.stdout)
