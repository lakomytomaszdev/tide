"""Anulowanie rzeczywistego gita, jego potomków i kolejki monitora bez magistrali pulpitu."""
import json
import os
import select
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import CancelledError, ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from tide_daemon.worktrees.cancellation import Cancellation
from tide_daemon.worktrees.git import Git
from tide_daemon.worktrees.scanner import WorktreeScanner
from . import flowfakes
from .privatebus import app


# Bezpiecznik, nie synchronizacja: test czeka na zdarzenia (zapis atrapy do FIFO, wyjście procesu przez
# pidfd, koniec future). Limit tylko przerywa zawieszony test; atrapa śpi 120 s przy timeout=120, więc
# 20 s nadal odróżnia anulowanie od naturalnego końca gita, a zostawia zapas na kolejkę planisty pod obciążeniem.
GUARD = 20


def _read_until_writer_closes(fd, timeout):
    """Czyta FIFO otwarte bez blokowania, aż piszący je zamknie; cała treść naraz, bez pustego odczytu."""
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    chunks, deadline = [], time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not poller.poll(remaining * 1000):
            raise AssertionError("atrapa gita nie zgłosiła gotowości (slow-pid)")
        try:
            chunk = os.read(fd, 4096)
        except BlockingIOError:
            continue
        if not chunk:
            return b"".join(chunks).decode()
        chunks.append(chunk)


def _exited(pidfd, timeout):
    """pidfd staje się czytelny, gdy proces zakończy się (także wnuk, na którego nikt nie czeka)."""
    poller = select.poll()
    poller.register(pidfd, select.POLLIN)
    return bool(poller.poll(timeout * 1000))


class CancellationTest(unittest.TestCase):
    def test_cancel_kills_git_and_its_child_and_rejects_later_commands(self):
        with tempfile.TemporaryDirectory() as folder, ThreadPoolExecutor(max_workers=1) as pool:
            root = Path(folder)
            script = root / "git"
            script.write_text(Path(__file__).with_name("slow_git.py").read_text())
            script.chmod(0o755)
            (root / "slow-arm").touch()
            # Atrapa zapisuje PID-y do `slow-pid` po starcie dziecka; FIFO zamienia ten zapis w sygnał
            # gotowości (koniec zapisu = zamknięcie przez atrapę) zamiast odpytywania pliku z terminem.
            os.mkfifo(root / "slow-pid")
            ready = os.open(root / "slow-pid", os.O_RDONLY | os.O_NONBLOCK)
            self.addCleanup(os.close, ready)
            cancel = Cancellation()
            git = Git(cancel=cancel)
            git.env["TIDE_LIFECYCLE_DIR"] = folder
            # Opóźnienie jest atrapą transportu Cancellation, nie podmianą
            # systemowego programu dopuszczonego przez granicę hostowego Git.
            future = pool.submit(cancel.run, [str(script), "worktree", "list", "--porcelain"],
                                 timeout=120, env=git.env)
            try:
                pids = [int(p) for p in _read_until_writer_closes(ready, GUARD).split()]
                self.assertEqual(len(pids), 2)  # git i jego dziecko
                # pidfd otwarte, gdy oba procesy na pewno żyją (śpią 120 s): bez pomyłki z ponownym PID-em.
                exits = [os.pidfd_open(p) for p in pids]
                for pidfd in exits:
                    self.addCleanup(os.close, pidfd)
                started = time.monotonic()
                cancel.close()
                with self.assertRaises(CancelledError):
                    future.result(timeout=GUARD)
                self.assertLess(time.monotonic() - started, GUARD)
                # Cancellation czeka tylko na lidera grupy; SIGKILL dla dziecka jest asynchroniczny
                # i pod obciążeniem dociera po powrocie future. Czekamy na jego wyjście, nie na upływ czasu.
                self.assertTrue(all(_exited(pidfd, GUARD) for pidfd in exits))
                self.assertTrue(all(not flowfakes.alive(p) for p in pids))
                with self.assertRaises(CancelledError):
                    git.run(folder, "status")
            finally:
                cancel.close()

    def test_input_and_timeout_keep_the_git_result_contract(self):
        cancel = Cancellation()
        output = cancel.run([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
                            timeout=2, env=os.environ, input_text="abc")
        self.assertEqual((output.returncode, output.stdout), (0, "ABC\n"))
        with self.assertRaises(subprocess.TimeoutExpired):
            cancel.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=.05, env=os.environ)

    def test_scanner_stop_aborts_scan_and_disk_measurement(self):
        with tempfile.TemporaryDirectory() as folder:
            scan = WorktreeScanner(folder)
            scan.stop()
            with self.assertRaises(CancelledError):
                scan.refresh()
            with self.assertRaises(CancelledError):
                scan.measure(SimpleNamespace(path=folder, nested=()))


class MonitorShutdownTest(unittest.TestCase):
    def test_flow_drains_current_write_and_commits_without_a_qt_callback(self):
        from tide_daemon.cardflow.journal import Journal
        from tide_daemon.cardflow_monitor import CardFlowMonitor
        app()
        with tempfile.TemporaryDirectory() as folder:
            journal = Journal(folder, mirror=folder + "/mirror")
            committed, ran, callbacks = [], [], []
            engine = SimpleNamespace(journal=journal, store=SimpleNamespace(
                repo=SimpleNamespace(commit=lambda message, paths: committed.append(paths))))
            monitor = CardFlowMonitor(engine)
            entered, released = threading.Event(), threading.Event()

            def work():
                entered.set()
                self.assertTrue(released.wait(2))
                journal.append("K-1", "step.finished", step="s1", result="question", question="Wariant?")
                journal.write_file("K-1", "steps/s1/result.json", b'{"result":"question"}')

            monitor._submit(work, callbacks.append)
            self.assertTrue(entered.wait(2))
            monitor._submit(lambda: ran.append("queued"), callbacks.append)
            release = threading.Timer(.05, released.set)
            release.start()
            monitor.stop()
            release.join()
            self.assertFalse(monitor._submit(lambda: ran.append("late"), callbacks.append))
            app().processEvents()
            self.assertEqual((ran, callbacks), ([], []))
            self.assertEqual(len(committed), 1)
            self.assertIn("flow/K-1/journal.jsonl", committed[0])
            recovered = Journal(folder, mirror=folder + "/mirror")
            self.assertEqual(recovered.read("K-1")[0]["question"], "Wariant?")
            self.assertEqual(json.loads(Path(folder, "flow/K-1/steps/s1/result.json").read_text()),
                             {"result": "question"})

    def test_queue_is_cancelled_no_new_work_or_callbacks_after_stop(self):
        from tide_daemon.worktree_monitor import WorktreeMonitor
        app()
        entered, released = threading.Event(), threading.Event()
        scanner = SimpleNamespace(stop=released.set)
        monitor = WorktreeMonitor(scanner, lambda: [])
        ran, callbacks = [], []

        def active():
            entered.set()
            self.assertTrue(released.wait(2))

        monitor._submit(active, callbacks.append)
        self.assertTrue(entered.wait(2))
        monitor._submit(lambda: ran.append("queued"), callbacks.append)
        monitor.stop()
        monitor._submit(lambda: ran.append("late"), callbacks.append)
        monitor.refresh()
        monitor.start()
        app().processEvents()
        self.assertEqual((ran, callbacks), ([], []))
        replies = []
        monitor.remove(["/unused"], lambda data: replies.append(json.loads(data)))
        self.assertEqual(replies[0][0]["reason"], "unavailable")
        self.assertFalse(monitor._timer.isActive())


if __name__ == "__main__":
    unittest.main()
