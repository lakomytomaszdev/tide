"""06.11 (PT-246): dwa wątki opróżniały tę samą kolejkę commitów Tablicy naraz — oba commitowały ten sam wpis,
a drugi `pop(0)` kończył się `IndexError: pop from empty list` (MergeCardToMain). Odtwarzacz z kroku PT-246."""

from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest import mock

from tide_daemon.board.repo import BoardRepo


class BoardRepoRaceTest(unittest.TestCase):
    def test_concurrent_flush_commits_each_entry_once(self):
        repo = BoardRepo("/nieuzywane-repo-testu")
        repo.pending.append(("test", ["syntetyczna-sciezka"]))
        calls = []
        entered = threading.Event()

        def finish_commit(message, paths):
            calls.append(message)
            entered.set()
            # Drugi wątek ma czas wejść do flush, zanim pierwszy zdejmie wpis.
            threading.Event().wait(0.2)
            return "", False

        with mock.patch.object(repo, "_commit_now", side_effect=finish_commit):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(repo.flush)
                entered.wait(3)
                second = pool.submit(repo.flush)
                self.assertEqual(first.result(timeout=5), "")
                self.assertEqual(second.result(timeout=5), "")
        self.assertEqual(calls, ["test"], "jeden commit na wpis kolejki")
        self.assertEqual(repo.pending, [])

    def test_commit_inside_sync_does_not_deadlock(self):
        repo = BoardRepo("/nieuzywane-repo-testu")
        with mock.patch.object(repo, "_commit_now", return_value=("", False)), \
                mock.patch.object(repo, "initialized", return_value=True), \
                mock.patch.object(repo, "busy", return_value=""), \
                mock.patch.object(repo.git, "run", return_value=mock.Mock(ok=True, out="")):
            with repo.lock:
                self.assertEqual(repo.commit("a", ["x"]), "")
                self.assertFalse(repo.sync("s") is None)


if __name__ == "__main__":
    unittest.main()


class DirectRunStateIsolationTest(unittest.TestCase):
    """06.11: bezpośredni `python3 -m unittest` nie może pisać do stanu działającej usługi (karta-widmo K-1)."""

    def test_direct_import_moves_state_to_a_temporary_directory(self):
        import os
        import subprocess
        import sys
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        for package in ("daemon.tests", "apps.board.tests"):
            with self.subTest(package=package):
                run = subprocess.run(
                    [sys.executable, "-c", f"import os, {package}; "
                     "from tide_daemon import board_monitor; print(os.environ['XDG_STATE_HOME'])"
                     if package == "daemon.tests" else f"import os, {package}; print(os.environ['XDG_STATE_HOME'])"],
                    cwd=root, capture_output=True, text=True, timeout=60,
                    env={**os.environ, "XDG_STATE_HOME": os.path.expanduser("~/.local/state")})
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertIn("tide-test-state-", run.stdout)
