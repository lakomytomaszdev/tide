"""K-109 PT-479 — `Runner.run_command` w `HostRunner`: argv usługi przejściowej `tide-cmd-<attempt>`, opakowanie
`tide-step --command` z prawdziwym procesem potomnym i wynik wyłącznie z pliku `exit-code`.

Prawdziwego menedżera systemd tu nie ma (moduł integracyjny: test_runner_command_systemd.py). Atrapa klienta
`systemd-run` uruchamia argv po `--` (opakowanie) i kończy się innym kodem niż polecenie — wynik ma przyjść z pliku.
"""
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tide_daemon.tasks import runner as runner_mod
from tide_daemon.tasks import stepstop
from tide_daemon.tasks.runner import CommandRequest, HostRunner, read_command_exit

# Klient-obserwator: uruchamia argv usługi (po pierwszym `--`) i kończy się kodem 42 niezależnie od polecenia.
OBSERVER = r'''#!/usr/bin/python3
import subprocess, sys
args = sys.argv[1:]
subprocess.call(args[args.index("--") + 1:])
sys.exit(42)
'''


def _wait(predicate, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pt479-runner-")
        self.addCleanup(subprocess.run, ["rm", "-rf", self.tmp])
        self.step_dir = os.path.join(self.tmp, "wt.step")
        self.worktree = os.path.join(self.tmp, "wt")
        os.makedirs(self.step_dir)
        os.makedirs(self.worktree)

    def request(self, command="make test", **extra):
        return CommandRequest(command=command, folder=self.step_dir, cwd=self.worktree,
                              env={"PATH": "/usr/bin:/bin", "SECRET_TOKEN": "s3cr3t", "INVOCATION_ID": "abc",
                                   "NOTIFY_SOCKET": "/run/x", "bad name": "x"}, timeout=1800, **extra)


class ArgvTest(Case):
    def capture(self, request, systemd_run="/x/systemd-run"):
        calls = []

        def popen(argv, **kw):
            calls.append({"argv": argv, **kw})
            return mock.Mock(pid=1)
        with mock.patch.object(runner_mod.subprocess, "Popen", popen):
            handle = HostRunner(systemd_run).run_command(request)
        self.assertEqual(len(calls), 1)
        return handle, calls[0]

    def test_transient_service_with_wait_collect_unit_runtime_and_wrapper(self):
        handle, call = self.capture(self.request("make test-changed BASE=${BASE:-abc} 100%"))
        argv = call["argv"]
        self.assertRegex(handle.attempt, r"^[0-9a-f]{16}$")
        self.assertEqual(handle.unit, "tide-cmd-" + handle.attempt)
        folder = os.path.join(self.step_dir, "outside", handle.unit)
        self.assertEqual((handle.exit_file, handle.log),
                         (os.path.join(folder, "exit-code"), os.path.join(folder, "outside.log")))
        self.assertNotIn("--scope", argv)
        self.assertEqual(argv[:6], ["/x/systemd-run", "--user", "--wait", "--collect", "--quiet",
                                    f"--unit=tide-cmd-{handle.attempt}"])
        properties = [argv[i + 1] for i, a in enumerate(argv) if a == "-p"]
        self.assertEqual(properties, ["CPUWeight=40", "IOWeight=40", "RuntimeMaxSec=1800"])
        self.assertIn(f"--working-directory={self.worktree}", argv)
        tail = argv[argv.index("--") + 1:]
        script = os.path.join(folder, "command")
        self.assertEqual(tail, ["/bin/sh", runner_mod.TIDE_STEP_SCRIPT, "--command", handle.exit_file, handle.log,
                                "--", "/bin/sh", script])
        # Treść polecenia w pliku, nie w argv (systemd rozwija `${…}` i `%` w argumentach usługi).
        with open(script, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "make test-changed BASE=${BASE:-abc} 100%\n")
        self.assertEqual(stat.S_IMODE(os.stat(script).st_mode), 0o600)
        self.assertEqual(tuple(argv), handle.argv)
        self.assertEqual((call["cwd"], call["stdin"], call["start_new_session"]),
                         (self.worktree, subprocess.DEVNULL, True))

    def test_environment_by_name_only_without_service_manager_variables(self):
        _handle, call = self.capture(self.request())
        argv = call["argv"]
        self.assertEqual([a for a in argv if a.startswith("--setenv")], ["--setenv=PATH", "--setenv=SECRET_TOKEN"])
        self.assertFalse(any("s3cr3t" in a for a in argv), "wartość zmiennej w argv (widoczna w ps)")
        self.assertEqual(call["env"], {"PATH": "/usr/bin:/bin", "SECRET_TOKEN": "s3cr3t"})

    def test_attempt_is_random_per_start_and_each_has_own_folder(self):
        first, _ = self.capture(self.request())
        second, _ = self.capture(self.request())
        self.assertNotEqual(first.attempt, second.attempt)
        self.assertNotEqual(os.path.dirname(first.exit_file), os.path.dirname(second.exit_file))
        with self.assertRaises(FileExistsError):
            self.capture(self.request(attempt=first.attempt))    # ta sama próba drugi raz nie startuje
        with self.assertRaises(ValueError):
            self.capture(self.request(attempt="../x"))

    def test_wrapper_is_the_box_tide_step_script(self):
        from tide_daemon.box import runner as box_runner
        self.assertEqual(runner_mod.TIDE_STEP_SCRIPT, box_runner.TIDE_STEP_SCRIPT)
        self.assertTrue(os.path.isfile(runner_mod.TIDE_STEP_SCRIPT))

    def test_default_systemd_run_and_no_working_directory(self):
        _handle, call = self.capture(CommandRequest(command="true", folder=self.step_dir), systemd_run="")
        self.assertEqual(call["argv"][0], "systemd-run")
        self.assertFalse(any(a.startswith("--working-directory") for a in call["argv"]))
        self.assertIn("-p", call["argv"])
        self.assertIn("RuntimeMaxSec=3600", call["argv"])


class ExitFileTest(Case):
    def write(self, text, name="exit-code"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="ascii") as fh:
            fh.write(text)
        return path

    def test_reader_accepts_only_complete_number_or_stopped(self):
        self.assertEqual(read_command_exit(self.write("0\n")), 0)
        self.assertEqual(read_command_exit(self.write("143\n")), 143)
        self.assertEqual(read_command_exit(self.write("zatrzymane\n")), runner_mod.STOPPED)
        for text in ("", "7", "256\n", "07\n", "-1\n", "x\n", "zatrzymane"):
            with self.subTest(text=text):
                self.assertIsNone(read_command_exit(self.write(text)))
        self.assertIsNone(read_command_exit(os.path.join(self.tmp, "brak")))
        link = os.path.join(self.tmp, "link")
        os.symlink(self.write("0\n", "target"), link)
        self.assertIsNone(read_command_exit(link))
        fifo = os.path.join(self.tmp, "fifo")
        os.mkfifo(fifo)
        self.assertIsNone(read_command_exit(fifo))

    def states(self, *results):
        runs = iter(results)

        def systemctl(*args, timeout=30):
            code, out = next(runs)
            return subprocess.CompletedProcess(args, code, out.encode(), b"")
        return mock.patch.object(stepstop, "_systemctl", systemctl)

    def test_result_from_file_without_asking_systemd(self):
        path = self.write("5\n")
        with self.states():         # brak odczytu stanu: plik wystarcza
            self.assertEqual(HostRunner().command_result("tide-cmd-a", path, age=0, grace=5, state_timeout=1), 5)

    def test_missing_file_after_unit_end_is_stopped(self):
        missing = os.path.join(self.tmp, "exit-code")
        runner = HostRunner()
        with self.states():
            self.assertIsNone(runner.command_result("tide-cmd-a", missing, age=1, grace=5, state_timeout=1))
        for state in ((3, "inactive\n"), (3, "failed\n"), (4, "")):
            with self.subTest(state=state), self.states(state):
                self.assertEqual(runner.command_result("tide-cmd-a", missing, age=9, grace=5, state_timeout=1),
                                 runner_mod.STOPPED)
        for state in ((0, "active\n"), (3, "deactivating\n"), (3, "activating\n"), (1, ""), (3, "")):
            with self.subTest(state=state), self.states(state):
                self.assertIsNone(runner.command_result("tide-cmd-a", missing, age=9, grace=5, state_timeout=1))

    def test_unit_state_is_read_for_the_service_unit(self):
        seen = []

        def systemctl(*args, timeout=30):
            seen.append(args)
            return subprocess.CompletedProcess(args, 0, b"active\n", b"")
        with mock.patch.object(stepstop, "_systemctl", systemctl):
            HostRunner().command_state("tide-cmd-a")
            HostRunner().stop_command("tide-cmd-a")
        self.assertEqual(seen, [("is-active", "tide-cmd-a.service"), ("stop", "--no-block", "tide-cmd-a.service")])


class WrapperTest(Case):
    """Opakowanie `tide-step --command` z prawdziwym procesem potomnym (bez systemd)."""

    def wrapper(self, *command, **kw):
        self.exit_file = os.path.join(self.tmp, "exit-code")
        self.log = os.path.join(self.tmp, "outside.log")
        return subprocess.Popen(["/bin/sh", runner_mod.TIDE_STEP_SCRIPT, "--command", self.exit_file, self.log,
                                 "--", *command], start_new_session=True, **kw)

    def listing(self):
        return sorted(os.listdir(self.tmp))

    def test_normal_end_writes_code_atomically(self):
        with open(os.path.join(self.tmp, "exit-code.tmp"), "w") as fh:
            fh.write("9")          # resztka poprzedniego zapisu nie jest wynikiem
        proc = self.wrapper("/bin/sh", "-c", 'echo "wynik ${HOME:+jest}"; read x || echo "stdin pusty"; exit 7')
        self.assertEqual(proc.wait(timeout=20), 7)
        with open(self.exit_file, encoding="ascii") as fh:
            self.assertEqual(fh.read(), "7\n")
        self.assertEqual(read_command_exit(self.exit_file), 7)
        self.assertNotIn("exit-code.tmp", self.listing())
        with open(self.log, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "wynik jest\nstdin pusty\n")

    def test_sigterm_writes_stopped_at_once_and_stops_the_child(self):
        pid_file = os.path.join(self.tmp, "child.pid")
        # Polecenie ignoruje pierwszy SIGTERM przez chwilę: wynik `zatrzymane` jest w pliku, zanim polecenie się skończy.
        proc = self.wrapper("/bin/sh", "-c", f'trap "sleep 1; exit 0" TERM; echo $$ > {pid_file}; '
                                             'while :; do sleep 0.05; done')
        self.assertTrue(_wait(lambda: os.path.exists(pid_file) and os.path.getsize(pid_file)))
        with open(pid_file) as fh:
            child = int(fh.read())
        os.kill(proc.pid, signal.SIGTERM)       # tylko opakowanie, jak `kill` bez cgroup
        self.assertTrue(_wait(lambda: read_command_exit(self.exit_file) == runner_mod.STOPPED, timeout=5))
        self.assertIsNone(proc.poll(), "opakowanie nie czeka na koniec polecenia")
        self.assertEqual(proc.wait(timeout=20), 143)
        self.assertEqual(read_command_exit(self.exit_file), runner_mod.STOPPED)
        self.assertNotIn("exit-code.tmp", self.listing())
        with self.assertRaises(ProcessLookupError):
            os.kill(child, 0)

    def test_sigterm_to_the_whole_group_like_unit_stop(self):
        proc = self.wrapper("/bin/sleep", "30")
        self.assertTrue(_wait(lambda: os.path.exists(self.log)))
        time.sleep(0.2)
        os.killpg(proc.pid, signal.SIGTERM)     # `systemctl stop` / RuntimeMaxSec: SIGTERM do całej cgroup
        self.assertEqual(proc.wait(timeout=20), 143)
        with open(self.exit_file, encoding="ascii") as fh:
            self.assertEqual(fh.read(), "zatrzymane\n")

    def test_code_recorded_before_a_late_sigterm_stays(self):
        proc = self.wrapper("/bin/sh", "-c", "exit 3")
        self.assertEqual(proc.wait(timeout=20), 3)
        self.assertEqual(read_command_exit(self.exit_file), 3)

    def test_usage_error_without_separator(self):
        proc = subprocess.run(["/bin/sh", runner_mod.TIDE_STEP_SCRIPT, "--command", "a", "b", "true"],
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 125)
        self.assertIn("użycie", proc.stderr)


class ObserverClientTest(Case):
    """Kod klienta `systemd-run --wait` nie jest wynikiem: atrapa klienta kończy się 42, polecenie 7."""

    def test_result_comes_from_the_exit_file_not_from_the_client(self):
        fake = os.path.join(self.tmp, "systemd-run")
        with open(fake, "w", encoding="utf-8") as fh:
            fh.write(OBSERVER.replace("/usr/bin/python3", sys.executable, 1))
        os.chmod(fake, 0o755)
        runner = HostRunner(fake)
        handle = runner.run_command(CommandRequest(command="echo ok; exit 7", folder=self.step_dir,
                                                   cwd=self.worktree, env=dict(os.environ), timeout=60))
        self.assertEqual(handle.proc.wait(timeout=20), 42)
        with mock.patch.object(stepstop, "_systemctl", side_effect=AssertionError("stan jednostki niepotrzebny")):
            self.assertEqual(runner.command_result(handle.unit, handle.exit_file, age=0, grace=5, state_timeout=1), 7)
        with open(handle.log, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "ok\n")


if __name__ == "__main__":
    unittest.main()
