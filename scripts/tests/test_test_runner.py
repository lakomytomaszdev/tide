"""PT-215: dobór zmian, kompletność discover, błędy wszystkich modułów i izolacja."""
from collections import Counter
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import test_runner as runner
from tools.test_suites import SUITES


_POOL = tempfile.TemporaryDirectory()


def setUpModule():
    # Testy uruchamiacza mają własną pulę: uruchomione wprost nie czekają na miejsca przebiegów kart (06.11).
    patcher = patch.dict("os.environ", {"TIDE_TEST_SLOTS_DIR": _POOL.name})
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


class SelectionTest(unittest.TestCase):
    def test_component_changes_and_more_specific_cardflow_mapping(self):
        self.assertEqual(runner.changed_suites(["apps/board/qml/Main.qml", "daemon/tide_daemon/main.py"]),
                         ["board", "daemon"])
        self.assertEqual(runner.changed_suites(["daemon/tide_daemon/cardflow/gitops.py",
                                               "daemon/tests/test_cardflow_new.py"]), ["cardflow"])

    def test_unknown_path_falls_back_to_full_and_empty_diff_is_empty(self):
        for path in ("Makefile", "mk/daemon.mk", "tools/test_runner.py", "theme/shared.py", "README.md"):
            self.assertIsNone(runner.changed_suites(["apps/board/qml/Main.qml", path]))
        self.assertEqual(runner.changed_suites([]), [])

    def test_cardflow_changes_include_direct_tests_outside_cardflow_filename_pattern(self):
        for source, test in (("basesuggestions.py", "test_base_suggestions.py"),
                             ("hostingcli.py", "test_hostingcli_real.py")):
            with self.subTest(source=source):
                selected = runner.changed_suites(["daemon/tide_daemon/cardflow/" + source])
                self.assertEqual(selected, ["cardflow"])
                entries = [entry for name in selected for entry in SUITES[name]]
                modules = runner.modules_for(entries)
                self.assertIn("daemon/tests/" + test, modules)
                self.assertIn("daemon/tests/test_cardflow_fixture.py", modules)

    def test_registry_covers_current_make_directories(self):
        dirs = []
        for fragment in (runner.ROOT / "mk").glob("*.mk"):
            for line in fragment.read_text().splitlines():
                if line.startswith("TEST_DIRS +="):
                    dirs.extend(line.split("+=", 1)[1].split())
        registered = {d for entries in SUITES.values() for d, _ in entries}
        self.assertEqual(set(dirs), registered)
        self.assertEqual(len(dirs), len(set(dirs)))

    def test_jobs_reserves_two_cores_and_at_least_one_worker(self):
        with patch.object(runner.os, "sched_getaffinity", return_value=set(range(16))):
            self.assertEqual(runner.default_jobs(), 14)
        with patch.object(runner.os, "sched_getaffinity", return_value={0}):
            self.assertEqual(runner.default_jobs(), 1)


class RunnerContractTest(unittest.TestCase):
    def setUp(self):
        (runner.ROOT / "build").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=runner.ROOT / "build")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root / "example"
        self.package.mkdir()
        (self.package / "__init__.py").write_text("")

    def write(self, name, text):
        path = self.package / name
        path.write_text(text, encoding="utf-8")
        return path.relative_to(self.root).as_posix()

    def test_all_failures_errors_subtests_and_crashes_are_reported_without_stopping(self):
        self.write("test_first.py", '''import unittest
class Example(unittest.TestCase):
    def test_fail(self): self.fail("pierwszy")
    def test_subtests(self):
        for n in range(2):
            with self.subTest(n=n): self.assertEqual(n, 7)
''')
        self.write("test_second.py", '''import unittest
class Example(unittest.TestCase):
    def test_error(self): raise ValueError("drugi")
    @unittest.expectedFailure
    def test_unexpected(self): pass
''')
        self.write("test_crash.py", 'import os\nos._exit(0)\n')
        self.write("test_good.py", '''import unittest
class Example(unittest.TestCase):
    def test_ok(self): pass
''')
        output = io.StringIO()
        with redirect_stdout(output):
            results = runner.execute(runner.modules_for([("example", "test*.py")], self.root),
                                     2, self.root / "logs", self.root)
            status = runner.summarize(results, 0)
        self.assertEqual(status, 1)
        self.assertEqual(len(results), 4)
        report = output.getvalue().split("Wszystkie nieudane testy:", 1)[1]
        for name in ("example/test_first.py", "test_fail", "(n=0)", "(n=1)",
                     "example/test_second.py", "test_error", "test_unexpected", "example/test_crash.py"):
            self.assertIn(name, report)
        self.assertNotIn("example/test_good.py", report)
        self.assertEqual(sum(r["tests"] for r in results), 5)

    def test_separate_processes_cannot_leak_environment_to_next_module(self):
        self.write("test_a.py", '''import os, unittest
class Example(unittest.TestCase):
    def test_mutate(self): os.environ["TIDE_RUNNER_LEAK"] = "yes"
''')
        self.write("test_b.py", '''import os, unittest
class Example(unittest.TestCase):
    def test_isolated(self): self.assertNotIn("TIDE_RUNNER_LEAK", os.environ)
''')
        with redirect_stdout(io.StringIO()):
            results = runner.execute(runner.modules_for([("example", "test*.py")], self.root),
                                     1, self.root / "logs", self.root)
        self.assertTrue(all(r["returncode"] == 0 for r in results))

    def test_modules_do_not_write_the_qml_disk_cache_to_the_user_home(self):
        # 08.11: ~/.cache/QtProject/Qml Runtime/qmlcache urósł do 100 GB (3 mln plików, ~10 GB na dobę) —
        # każdy przebieg qml6 na plikach z nowych ścieżek tymczasowych dopisywał własny wpis pamięci QML.
        self.write("test_qml.py", '''import os, unittest
class Example(unittest.TestCase):
    def test_cache_off(self): self.assertEqual(os.environ.get("QML_DISABLE_DISK_CACHE"), "1")
''')
        with redirect_stdout(io.StringIO()):
            results = runner.execute(runner.modules_for([("example", "test*.py")], self.root),
                                     1, self.root / "logs", self.root)
        self.assertTrue(all(r["returncode"] == 0 for r in results), results)

    def test_serial_modules_run_after_parallel_work_and_one_at_a_time(self):
        modules = ["a", "serial1", "b", "serial2"]
        calls = []
        def run(module, *args):
            calls.append(module)
            return dict(module=module, returncode=0, tests=1, seconds=0)
        with patch.object(runner, "SERIAL_MODULES", {"serial1": "wspólny zasób", "serial2": "ten sam zasób"}), \
                patch.object(runner, "run_module", side_effect=run), redirect_stdout(io.StringIO()):
            runner.execute(modules, 2, self.root / "logs", self.root)
        self.assertEqual(set(calls[:2]), {"a", "b"})
        self.assertEqual(calls[2:], ["serial1", "serial2"])

    def inventory(self, module, directory=False):
        report = self.root / "inventory.json"
        command = [sys.executable, str(runner.ROOT / "tools/test_runner.py"), "--worker", module,
                   "--worker-report", str(report), "--inventory"]
        if directory:
            command.append("--worker-directory")
        proc = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(report.read_text())

    def test_module_split_keeps_discover_multiplicity_including_imported_tests(self):
        self.write("test_a.py", '''import unittest
class Example(unittest.TestCase):
    def test_ok(self): pass
''')
        self.write("test_b.py", 'from .test_a import Example\n')
        modules = runner.modules_for([("example", "test*.py"), ("example", "test_a.py")], self.root)
        old = self.inventory("example", directory=True)
        split = [test for module in modules for test in self.inventory(module)]
        self.assertEqual(Counter(old), Counter(split))
        self.assertEqual(len(split), 2)

    def test_git_diff_includes_branch_staged_working_untracked_deleted_and_both_sides_of_rename(self):
        def git(*args):
            return subprocess.check_output(["git", *args], cwd=self.root, stderr=subprocess.DEVNULL)
        git("init", "-b", "base")
        git("config", "user.name", "Test")
        git("config", "user.email", "test@example.invalid")
        for name in ("branch", "staged", "working", "deleted", "renamed"):
            (self.root / name).write_text("old\n")
        git("add", ".")
        git("commit", "-qm", "Initial")
        git("checkout", "-b", "ticket")
        (self.root / "branch").write_text("new\n")
        git("commit", "-qam", "Branch change")
        (self.root / "staged").write_text("new\n")
        git("add", "staged")
        (self.root / "working").write_text("new\n")
        (self.root / "deleted").unlink()
        (self.root / "renamed").rename(self.root / "destination")
        (self.root / "untracked").write_text("new\n")
        self.assertEqual(runner.git_paths("base", self.root),
                         ["branch", "deleted", "destination", "renamed", "staged", "untracked", "working"])

    def test_package_load_tests_is_rejected_instead_of_silently_losing_tests(self):
        (self.package / "__init__.py").write_text("def load_tests(loader, tests, pattern): return tests\n")
        with self.assertRaisesRegex(ValueError, "load_tests"):
            runner.modules_for([("example", "test*.py")], self.root)

    def test_make_public_targets_use_runner_selection_and_forward_jobs_and_base(self):
        for target, expected in (("test", "--dirs"), ("test-cardflow", "--suite cardflow"),
                                 ("test-daemon", "--suite daemon"), ("test-board", "--suite board"),
                                 ("test-changed", '--changed --base "feature/base"')):
            command = subprocess.check_output(["make", "-n", target, "JOBS=3", "BASE=feature/base"],
                                              cwd=runner.ROOT, text=True)
            self.assertIn("tools/test_runner.py", command)
            self.assertIn(expected, command)
            self.assertIn('--jobs "3"', command)

    def test_cli_rejects_nonpositive_jobs_and_unknown_base_before_running_tests(self):
        script = str(runner.ROOT / "tools/test_runner.py")
        for args, expected in ((["--jobs", "0"], "JOBS"),
                               (["--changed", "--base", "refs/heads/nonexistent-pt215"], "BASE")):
            proc = subprocess.run([sys.executable, script, *args], cwd=self.root,
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2)
            self.assertIn(expected, proc.stderr)


if __name__ == "__main__":
    unittest.main()


class MachineSlotsTest(unittest.TestCase):
    """06.11: równoległe przebiegi `make test` dzielą jedną pulę miejsc maszyny zamiast brać po rdzenie−2 każdy."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "slots"

    def test_two_runs_together_never_exceed_the_shared_slots(self):
        import threading
        import time as clock
        lock, active, peak = threading.Lock(), [0], [0]

        def module(name, *args):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            clock.sleep(0.05)
            with lock:
                active[0] -= 1
            return dict(module=name, returncode=0, tests=1, seconds=0)

        with patch.dict("os.environ", {}, clear=False) as env, patch.object(runner, "_run_module", side_effect=module), \
                redirect_stdout(io.StringIO()):
            env.pop(runner.SLOT_HELD, None)
            runs = [threading.Thread(target=runner.execute, args=([f"{run}-{i}" for i in range(6)], 4,
                                                                  Path(self.tmp.name) / f"logs{run}", self.tmp.name,
                                                                  runner.MachineSlots(3, self.dir)))
                    for run in ("a", "b")]
            for run in runs:
                run.start()
            for run in runs:
                run.join(30)
        self.assertEqual(peak[0], 3, "dwa przebiegi po 4 wątki razem najwyżej 3 moduły naraz")

    def test_slot_of_a_dead_process_returns_to_the_pool(self):
        import fcntl
        code = ("import sys, os; sys.path.insert(0, sys.argv[1]); from tools import test_runner as r; "
                "os.environ.pop(r.SLOT_HELD, None); handle = r.MachineSlots(1, sys.argv[2]).acquire(); "
                "print('locked', flush=True); sys.stdin.readline(); os._exit(0)")
        with subprocess.Popen([sys.executable, "-c", code, str(runner.ROOT), str(self.dir)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                              env={k: v for k, v in __import__("os").environ.items() if k != runner.SLOT_HELD}) as proc:
            self.assertEqual(proc.stdout.readline().strip(), "locked")
            try:
                with open(self.dir / "slot-0.lock") as contender:
                    with self.assertRaises(BlockingIOError, msg="uchwyt nadal trzyma blokadę w żywym procesie"):
                        fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                proc.stdin.write("exit\n")
                proc.stdin.flush()
                self.assertEqual(proc.wait(timeout=5), 0)
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop(runner.SLOT_HELD, None)
            slots = runner.MachineSlots(1, self.dir)
            handle = slots.acquire()
        self.assertIsNotNone(handle)
        runner.MachineSlots.release(handle)

    def test_module_inside_a_module_does_not_wait_for_the_pool(self):
        with patch.dict("os.environ", {runner.SLOT_HELD: "1"}):
            self.assertIsNone(runner.MachineSlots(1, self.dir).acquire())

    def test_unwritable_pool_directory_runs_without_the_pool(self):
        blocker = Path(self.tmp.name) / "plik"
        blocker.write_text("nie katalog")
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop(runner.SLOT_HELD, None)
            slots = runner.MachineSlots(1, blocker / "slots")
            self.assertIsNone(slots.acquire())
            self.assertTrue(slots.disabled)

    def test_read_only_pool_from_the_host_still_limits_a_sandboxed_run(self):
        import os, stat
        with patch.dict("os.environ", {}, clear=False) as env:
            env.pop(runner.SLOT_HELD, None)
            host = runner.MachineSlots(1, self.dir)
            held = host.acquire()                       # host zakłada plik i trzyma jedyne miejsce
            os.chmod(self.dir / "slot-0.lock", stat.S_IRUSR)
            os.chmod(self.dir, stat.S_IRUSR | stat.S_IXUSR)    # jak katalog tylko do odczytu w piaskownicy
            self.addCleanup(os.chmod, self.dir, stat.S_IRWXU)
            self.addCleanup(os.chmod, self.dir / "slot-0.lock", stat.S_IRUSR | stat.S_IWUSR)
            sandboxed = runner.MachineSlots(1, self.dir)
            import threading
            got = []
            worker = threading.Thread(target=lambda: got.append(sandboxed.acquire()), daemon=True)
            with redirect_stdout(io.StringIO()):
                worker.start()
                worker.join(0.6)
                self.assertFalse(got, "piaskownica czeka na miejsce trzymane przez hosta")
                self.assertFalse(sandboxed.disabled)
                runner.MachineSlots.release(held)
                worker.join(5)
            self.assertTrue(got and got[0] is not None)
            runner.MachineSlots.release(got[0])


class ModulesOptionTest(unittest.TestCase):
    """06.11: ponowienie padniętych modułów (cardflow/testrerun.py) idzie przez uruchamiacz — pula i izolacja stanu."""

    def run_main(self, *argv):
        out = io.StringIO()
        with patch.object(sys, "argv", ["test_runner.py", *argv]), redirect_stdout(out), \
                patch("sys.stderr", io.StringIO()):
            try:
                code = runner.main()
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue()

    def test_modules_selects_exactly_the_given_known_modules(self):
        known = runner.modules_for([(d, "test*.py") for d in sorted({d for p in SUITES.values() for d, _ in p})])
        chosen = sorted(known)[:2]
        code, out = self.run_main("--modules", *reversed(chosen), "--list")
        self.assertIn(code, (0, None))
        self.assertEqual(out.split(), chosen)

    def test_unknown_module_and_mixed_selection_are_rejected(self):
        self.assertEqual(self.run_main("--modules", "daemon/tests/test_nie_ma.py", "--list")[0], 2)
        self.assertEqual(self.run_main("--modules", "x.py", "--changed")[0], 2)
