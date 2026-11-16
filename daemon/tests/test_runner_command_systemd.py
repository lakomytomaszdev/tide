"""K-109 PT-479 — moduł integracyjny z PRAWDZIWYM `systemd-run --user`: `HostRunner.run_command` (ten sam kod co
w usłudze, nie kopia argv) uruchamia przejściową usługę `tide-cmd-<attempt>` z opakowaniem `tide-step --command`.

Przypadki z kartki:
  a) zwykłe zakończenie — kod z pliku `exit-code` (i środowisko oraz `${…}` przechodzą do polecenia nietknięte),
  b) `systemctl --user stop` jednostki — `exit-code` zapisany, wynik `zatrzymane`; polecenie przeczekujące SIGTERM
     (`b-term`) — wynik dopiero po końcu jednostki, nie przy samym zapisie `zatrzymane`,
  c) przekroczenie `RuntimeMaxSec` (krótki limit) — wynik rozstrzygnięty (`zatrzymane`),
  d) zabicie klienta `systemd-run --wait` w trakcie (SIGTERM jak przy restarcie usługi i SIGKILL) — jednostka
     biegnie dalej, wynik z pliku po jej końcu,
  e) brak jednostki po `--collect` nie gubi wyniku.

W piaskownicy kroku agenta nie ma menedżera użytkownika — moduł się wtedy pomija (pominięcie nie jest dowodem;
autor uruchamia go poza piaskownicą). Każdy przypadek wypisuje na stderr wiersz `PT479 <przypadek>: …` z jednostką,
kodem klienta, treścią `exit-code` i wynikiem — do notki kroku.
"""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from tide_daemon.tasks import runner as runner_mod
from tide_daemon.tasks.runner import CommandRequest, HostRunner


def _user_manager() -> str:
    """Pusty napis — menedżer użytkownika dostępny; inaczej powód pominięcia."""
    if not shutil.which("systemd-run") or not shutil.which("systemctl"):
        return "brak systemd-run/systemctl"
    try:
        probe = subprocess.run(["systemctl", "--user", "show", "--property=Version", "--value"],
                               capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"systemctl --user: {exc}"
    if probe.returncode != 0 or not probe.stdout.strip():
        return "brak menedżera użytkownika systemd (piaskownica kroku?): " + (probe.stderr.strip() or "")[:200]
    return ""


SKIP = _user_manager()


@unittest.skipIf(SKIP, SKIP)
class RealSystemdCommandTest(unittest.TestCase):
    GRACE = 5

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pt479-systemd-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.step_dir = os.path.join(self.tmp, "wt.step")
        self.worktree = os.path.join(self.tmp, "wt")
        os.makedirs(self.step_dir)
        os.makedirs(self.worktree)
        self.runner = HostRunner()

    def start(self, command, timeout=120, **env):
        handle = self.runner.run_command(CommandRequest(
            command=command, folder=self.step_dir, cwd=self.worktree, env={**os.environ, **env}, timeout=timeout))
        self.started = time.monotonic()
        self.addCleanup(subprocess.run, ["systemctl", "--user", "stop", handle.unit + ".service"],
                        capture_output=True, timeout=30)
        self.addCleanup(lambda: handle.proc.poll() is None and handle.proc.kill())
        return handle

    def result(self, handle):
        return self.runner.command_result(handle.unit, handle.exit_file, age=time.monotonic() - self.started,
                                          grace=self.GRACE, state_timeout=3)

    def wait_result(self, handle, timeout=60):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            value = self.result(handle)
            if value is not None:
                return value
            time.sleep(0.1)
        self.fail(f"brak wyniku {handle.unit} po {timeout} s")

    def wait_state(self, handle, state, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.runner.command_state(handle.unit) == state:
                return True
            time.sleep(0.05)
        return self.runner.command_state(handle.unit) == state

    def exit_text(self, handle):
        try:
            with open(handle.exit_file, encoding="ascii") as fh:
                return fh.read().strip()
        except OSError:
            return "(brak pliku)"

    def log_text(self, handle):
        with open(handle.log, encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def report(self, case, handle, value, client):
        print(f"PT479 {case}: unit={handle.unit} client={client} exit-code={self.exit_text(handle)!r} "
              f"wynik={value!r} state={self.runner.command_state(handle.unit)}", file=sys.stderr, flush=True)

    def test_a_normal_end_code_from_exit_file(self):
        handle = self.start('X=wewnatrz; printf "%s %s %s\\n" "$PT479_MARK" "${X}" "$(pwd)"; exit 7',
                            PT479_MARK="znak-srodowiska")
        self.assertNotIn("--scope", handle.argv)
        self.assertIn("--wait", handle.argv)
        value = self.wait_result(handle)
        client = handle.proc.wait(timeout=30)
        self.report("a", handle, value, client)
        self.assertEqual(value, 7)
        self.assertEqual(self.exit_text(handle), "7")
        # Środowisko usługi z `--setenv=NAZWA`, `${X}` rozwija powłoka polecenia (nie systemd), katalog roboczy.
        self.assertEqual(self.log_text(handle), f"znak-srodowiska wewnatrz {self.worktree}\n")

    def test_b_unit_stop_writes_stopped(self):
        handle = self.start("sleep 300")
        self.assertTrue(self.wait_state(handle, "active"))
        stop = subprocess.run(["systemctl", "--user", "stop", handle.unit + ".service"], capture_output=True,
                              timeout=60)
        value = self.wait_result(handle)
        client = handle.proc.wait(timeout=30)
        self.report("b", handle, value, client)
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertEqual(self.exit_text(handle), runner_mod.STOPPED)
        self.assertEqual(value, runner_mod.STOPPED)

    def test_b_stopped_waits_for_processes_ignoring_term(self):
        # Review PT-479 r1, MAJOR 1: polecenie przeczekuje SIGTERM 3 s. `exit-code` ma `zatrzymane` od razu, ale
        # wynik końcowy dopiero po końcu jednostki (deactivating → brak), jak PT-315 na dawnej drodze.
        handle = self.start('trap "sleep 3; exit 0" TERM; while :; do sleep 0.1; done')
        self.assertTrue(self.wait_state(handle, "active"))
        stop = subprocess.run(["systemctl", "--user", "stop", "--no-block", handle.unit + ".service"],
                              capture_output=True, timeout=60)
        self.assertEqual(stop.returncode, 0, stop.stderr)
        end = time.monotonic() + 10
        while time.monotonic() < end and self.exit_text(handle) != runner_mod.STOPPED:
            time.sleep(0.05)
        alive = self.runner.command_state(handle.unit)
        during = self.result(handle)
        value = self.wait_result(handle)
        elapsed = time.monotonic() - self.started
        client = handle.proc.wait(timeout=30)
        self.report("b-term", handle, value, client)
        print(f"PT479 b-term: exit-code=zatrzymane przy jednostce {alive}, wynik w trakcie={during!r}, "
              f"rozstrzygnięte po {elapsed:.1f} s", file=sys.stderr, flush=True)
        self.assertEqual(alive, "active")
        self.assertIsNone(during)
        self.assertEqual(value, runner_mod.STOPPED)
        self.assertGreaterEqual(elapsed, 2.5)

    def test_c_runtime_max_sec_resolves(self):
        handle = self.start("sleep 300", timeout=2)
        self.assertIn("RuntimeMaxSec=2", handle.argv)
        value = self.wait_result(handle, timeout=60)
        elapsed = time.monotonic() - self.started
        client = handle.proc.wait(timeout=30)
        self.report("c", handle, value, client)
        print(f"PT479 c: rozstrzygnięte po {elapsed:.1f} s (RuntimeMaxSec=2)", file=sys.stderr, flush=True)
        self.assertEqual(value, runner_mod.STOPPED)
        self.assertEqual(self.exit_text(handle), runner_mod.STOPPED)
        self.assertLess(elapsed, 30)

    def _client_dies(self, case, sig):
        handle = self.start("sleep 4; echo po-kliencie; exit 5")
        self.assertTrue(self.wait_state(handle, "active"))
        os.kill(handle.proc.pid, sig)
        client = handle.proc.wait(timeout=30)
        time.sleep(0.5)
        alive = self.runner.command_state(handle.unit)
        during = self.result(handle)
        value = self.wait_result(handle, timeout=60)
        self.report(case, handle, value, client)
        print(f"PT479 {case}: po śmierci klienta ({signal.Signals(sig).name}) jednostka={alive}, wynik w trakcie="
              f"{during!r}", file=sys.stderr, flush=True)
        self.assertEqual(alive, "active", "jednostka zatrzymana razem z klientem")
        self.assertIsNone(during)
        self.assertEqual(value, 5)
        self.assertIn("po-kliencie", self.log_text(handle))

    def test_d_client_sigterm_unit_keeps_running(self):
        self._client_dies("d-sigterm", signal.SIGTERM)

    def test_d_client_sigkill_unit_keeps_running(self):
        self._client_dies("d-sigkill", signal.SIGKILL)

    def test_e_collected_unit_does_not_lose_result(self):
        handle = self.start("exit 3")
        client = handle.proc.wait(timeout=30)
        self.assertTrue(self.wait_state(handle, "gone"))
        status = subprocess.run(["systemctl", "--user", "status", handle.unit + ".service"], capture_output=True,
                                text=True, timeout=30)
        time.sleep(self.GRACE + 0.5)        # po czasie startu: brak jednostki to dowód końca, wynik i tak z pliku
        value = self.result(handle)
        self.report("e", handle, value, client)
        print(f"PT479 e: systemctl status po --collect: kod {status.returncode} "
              f"({(status.stderr or status.stdout).strip()[:120]})", file=sys.stderr, flush=True)
        self.assertNotEqual(status.returncode, 0)
        self.assertEqual(value, 3)


if __name__ == "__main__":
    unittest.main()
