"""Równoległy zapis przepływu i synchronizacja Tablicy nie mogą obsłużyć tego samego commita."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
import unittest
from unittest import mock

from tide_daemon.board.repo import BoardRepo
from tide_daemon.worktrees.git import Result


class CommitQueueConcurrencyTest(unittest.TestCase):
    def test_commit_and_other_queue_consumers_do_not_publish_the_same_entry_twice(self):
        for other in ("flush", "sync", "commit"):
            with self.subTest(other=other):
                repo = BoardRepo("/nieistniejaca-testowa-tablica")
                first_inside, release_first, second_started, overlap = Event(), Event(), Event(), Event()
                calls = []
                observation = Lock()

                def publish(message, paths):
                    with observation:
                        calls.append(message)
                        first = len(calls) == 1
                        if not first and not release_first.is_set():
                            overlap.set()
                    if first:
                        first_inside.set()
                        if not release_first.wait(5):
                            raise AssertionError("Nie zwolniono pierwszego zapisu.")
                    return "", False

                def consume():
                    second_started.set()
                    if other == "commit":
                        return repo.commit("second", ["b"])
                    return repo.sync("external") if other == "sync" else repo.flush()

                with mock.patch.object(repo, "_commit_now", side_effect=publish), \
                        mock.patch.object(repo, "initialized", return_value=True), \
                        mock.patch.object(repo, "busy", return_value=""), \
                        mock.patch.object(repo.git, "run", return_value=Result(0, "", "")), \
                        ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(repo.commit, "first", ["a"])
                    try:
                        self.assertTrue(first_inside.wait(5))
                        second = pool.submit(consume)
                        self.assertTrue(second_started.wait(5))
                        # Otwieramy okno wyścigu po pobraniu pending[0], przed usunięciem wpisu.
                        overlap.wait(0.1)
                    finally:
                        release_first.set()
                    first.result(timeout=5)
                    second.result(timeout=5)
                self.assertFalse(overlap.is_set(), "Dwóch konsumentów weszło do zapisu tego samego wpisu.")
                self.assertEqual(calls, ["first", "second"] if other == "commit" else ["first"])
                self.assertEqual(repo.pending, [])
