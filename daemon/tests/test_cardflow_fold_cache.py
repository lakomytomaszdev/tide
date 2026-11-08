"""K-65: pamięć projekcji dziennika i izolacja lokalnych zmian stanu."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold


class FoldCacheTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="tide-fold-cache-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.journal = Journal(str(self.root / "board"), mirror=str(self.root / "mirror"), clock=lambda: 1000)
        self.card = "K-1"
        self.journal.append(self.card, "card.started", entry="now", executor="Tester")
        self.journal.append(self.card, "intent", action="merge", key="PT-1")
        self.journal.append(self.card, "step.started", step="ticket-1", ticket="PT-1", kind="ticket")

    def test_unchanged_journal_is_folded_once(self):
        # Szpieg wykonuje prawdziwy fold, więc test sprawdza koszt przy zachowaniu wyniku.
        with mock.patch("tide_daemon.cardflow.journal.fold", wraps=fold) as actual:
            for _ in range(10):
                self.journal.state(self.card)
            self.assertEqual(actual.call_count, 1)

    def test_local_state_mutations_do_not_escape(self):
        expected = fold(self.journal.read(self.card))
        state = self.journal.state(self.card)
        state.caretaker_setup = {"enabled": False}
        state.last_step = "inny"
        state.intents.pop(("merge", "PT-1"))
        state.rejected.add("PT-1")
        state.step_notices.add("ticket-1")
        state.flow_notices.add("problem")
        state.timeline.clear()
        state.log.append({"type": "lokalne"})
        self.assertEqual(self.journal.state(self.card), expected)

    def test_matches_fresh_fold_before_and_after_append(self):
        previous = self.journal.state(self.card)
        self.assertEqual(previous, fold(self.journal.read(self.card)))
        self.journal.append(self.card, "step.finished", step="ticket-1", result="done")
        state = self.journal.state(self.card)
        self.assertEqual(state, fold(self.journal.read(self.card)))
        self.assertEqual(state.finished["ticket-1"]["result"], "done")
        self.assertEqual(state.last_seq, 4)
        self.assertEqual(previous.last_seq, 3)
        self.assertNotIn("ticket-1", previous.finished)

    def test_external_change_invalidates_mirror_and_board_fallback(self):
        import json
        import os

        for mirror in (False, True):
            with self.subTest(mirror=mirror):
                journal = Journal(str(self.root / str(mirror)), mirror=str(self.root / (str(mirror) + "-mirror")))
                path = Path(journal.mirror_path(self.card) if mirror else journal.path(self.card))
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({"type": "card.paused", "seq": 1}), encoding="utf-8")
                self.assertTrue(journal.state(self.card).paused)
                before = path.stat()
                # Taki sam rozmiar: unieważnienie musi wykryć również samą zmianę mtime.
                path.write_text(json.dumps({"type": "card.closed", "seq": 1}), encoding="utf-8")
                self.assertEqual(path.stat().st_size, before.st_size)
                os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
                state = journal.state(self.card)
                self.assertFalse(state.paused)
                self.assertEqual(state, fold(journal.read(self.card)))

    def test_writing_journal_through_file_api_invalidates_even_same_version(self):
        import json
        import os

        journal = Journal(str(self.root / "files"), mirror=str(self.root / "files-mirror"))
        original = json.dumps({"type": "card.paused", "seq": 1})
        changed = json.dumps({"type": "card.closed", "seq": 1})
        journal.write_file(self.card, "journal.jsonl", original.encode())
        self.assertTrue(journal.state(self.card).paused)
        path = Path(journal.path(self.card))
        before = path.stat()
        journal.write_file(self.card, "journal.jsonl", changed.encode())
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertFalse(journal.state(self.card).paused)

    def test_parallel_reads_fold_once_and_append_refreshes_once(self):
        from concurrent.futures import ThreadPoolExecutor

        with mock.patch("tide_daemon.cardflow.journal.fold", wraps=fold) as actual:
            with ThreadPoolExecutor(max_workers=8) as pool:
                states = list(pool.map(self.journal.state, [self.card] * 32))
            self.assertEqual(actual.call_count, 1)
            self.assertTrue(all(state == states[0] for state in states))
            self.journal.append(self.card, "card.paused")
            with ThreadPoolExecutor(max_workers=8) as pool:
                states = list(pool.map(self.journal.state, [self.card] * 32))
            self.assertEqual(actual.call_count, 2)
            self.assertTrue(all(state.paused for state in states))

    def test_source_switch_removal_and_restore(self):
        import os
        import shutil

        journal = Journal(str(self.root / "switch"), mirror=str(self.root / "switch-mirror"))
        board = Path(journal.path(self.card))
        board.parent.mkdir(parents=True)
        board.write_text('{"type":"card.paused","seq":1}\n', encoding="utf-8")
        self.assertTrue(journal.state(self.card).paused)
        mirror = Path(journal.mirror_path(self.card))
        mirror.parent.mkdir(parents=True)
        shutil.copy2(board, mirror)
        # Ta sama wielkość i mtime, ale inna ścieżka źródła.
        version = mirror.stat()
        mirror.write_text('{"type":"card.closed","seq":1}\n', encoding="utf-8")
        os.utime(mirror, ns=(version.st_atime_ns, version.st_mtime_ns))
        self.assertFalse(journal.state(self.card).paused)
        self.assertTrue(journal.check(self.card))
        self.assertEqual(journal.state(self.card), fold(journal.read(self.card)))
        mirror.unlink()
        journal.remove_file(self.card, "journal.jsonl")
        self.assertEqual(journal.state(self.card), fold([]))
        board.write_text('{"type":"card.paused","seq":1}\n', encoding="utf-8")
        self.assertTrue(journal.state(self.card).paused)

    def test_engine_reuses_projection_for_base_and_view(self):
        from types import SimpleNamespace
        from tide_daemon.cardflow.engine import FlowEngine

        engine = FlowEngine(SimpleNamespace(root=self.journal.root), journal=self.journal,
                            settings=str(self.root / "settings.json"), codex_roots=[])
        with mock.patch("tide_daemon.cardflow.journal.fold", wraps=fold) as actual:
            base = engine.card_base(self.card)
            view = engine.derived({"id": self.card, "column": "todo"})
            self.assertEqual(base["baseVersion"], 0)
            self.assertIsNotNone(view)
            self.assertEqual(actual.call_count, 1)
        self.assertEqual(self.journal.state(self.card), fold(self.journal.read(self.card)))
