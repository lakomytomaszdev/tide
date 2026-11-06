"""PT-261: kompletność inwentaryzacji na plikach Tablicy, bez prawdziwych procesów."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tide_daemon.box import lifecycle, trialgate as gate
from tide_daemon.cardflow import boxstep, trialhold
from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold
from .test_cardflow_trial_gate import HostStore


class InventoryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.board = self.root / "board"
        self.projects = self.board / "projects"
        self.projects.mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state"),
                                           "TIDE_BOARD_DIR": str(self.board)})
        env.start()
        self.addCleanup(env.stop)
        for method in ("run", "Popen", "call"):
            patcher = mock.patch("subprocess." + method, side_effect=AssertionError("zakaz procesu w regresji"))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.restart()
        self.journal.append("K-38", "card.started", project="plasma-tide")

    def restart(self):
        self.journal = Journal(str(self.board), mirror=str(self.root / "mirror"))
        self.engine = FlowEngine(SimpleNamespace(root=str(self.board)), journal=self.journal,
                                 codex_roots=[], settings=str(self.root / "settings.json"))

    def inventory(self):
        return trialhold.inventory(self.engine, "K-38", fold(self.journal.read("K-38")))

    def procedure(self):
        binding = gate.Binding("diagnostic", "1", str(self.root / "disposable"), "attempt",
                               "frozen", "manifest", "sha",
                               tuple((ticket, "sha") for ticket in sorted(gate.DEPENDENCIES)), "review-274")
        procedure = SimpleNamespace(snapshot=lambda: binding, bootstrap_reviewed=lambda: True,
                                    start=mock.Mock(return_value={"started": True}))
        store = HostStore()
        store.record = gate.HostRecord("PT-274", "review-274", binding)
        trial_gate = gate.TrialGate(store=store, procedure=procedure)
        trial_gate.register_bootstrap("new")
        return trial_gate, procedure

    def project(self):
        # BoardStore przechowuje metadane <slug>.md obok katalogu rejestrów <slug>/.
        (self.projects / "proj.md").write_text("---\nname: Projekt\nrepo: /disposable/project\n---\n")
        box = self.projects / "proj/box"
        box.mkdir(parents=True)
        path = box / "journal.jsonl"
        path.write_text('{"type":"box.ready","box":"proj"}\n')
        (self.projects / "proj/box.yaml").write_text("tide-box: 1\n")
        return path

    def test_project_metadata_allows_new_procedure(self):
        self.project()
        self.assertTrue(trialhold.public_inventory(self.engine))
        trial_gate, procedure = self.procedure()
        trial_gate.start_registered("new", consent=True, inventory=self.inventory)
        procedure.start.assert_called_once()

    def test_unrelated_project_lifecycle_still_runs(self):
        self.project()
        builder = mock.Mock()
        builder.incus.list.return_value = []
        with mock.patch.object(boxstep, "settings_of", return_value=SimpleNamespace(enabled=True)), \
                mock.patch.object(boxstep, "builder_of", return_value=builder), \
                mock.patch.object(lifecycle, "Lifecycle") as managed:
            lifecycle.tick(self.engine)
            managed.return_value.idle.assert_called_once_with("proj")

    def test_missing_corrupt_and_unreadable_box_journal_still_deny(self):
        path = self.project()
        path.unlink()
        self.assertFalse(trialhold.public_inventory(self.engine))
        path.write_text("broken\n")
        self.assertFalse(trialhold.public_inventory(self.engine))
        path.write_text('{"type":"box.ready"}\n')
        original = os.open

        def denied(name, *args, **kwargs):
            if name == str(path):
                raise PermissionError("test odmowy odczytu")
            return original(name, *args, **kwargs)

        with mock.patch.object(os, "open", side_effect=denied):
            self.assertFalse(trialhold.public_inventory(self.engine))

    def test_symlinks_are_not_project_metadata_or_registry_directories(self):
        target = self.root / "target"
        target.mkdir()
        for name in ("proj.md", "proj", "proj/box"):
            path = self.projects / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target, target_is_directory=True)
            with self.subTest(name=name):
                self.assertFalse(trialhold.public_inventory(self.engine))
            path.unlink()

    def test_corrupt_host_history_denies_both_modes_across_restart_without_rewriting(self):
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        board_before = board.read_bytes()
        # Rozgrzany tolerancyjny cache nie może ukryć błędu ścisłego odczytu.
        self.journal.read("K-38")
        original = mirror.read_bytes()
        corrupt = b'{"type":"step.started","step":"K-38-PT-176-1","ticket":"PT-176",BROKEN}\n'
        mirror.write_bytes(original + corrupt)
        for restarted in (False, True):
            if restarted:
                self.restart()
            with self.subTest(restarted=restarted):
                self.assertFalse(self.inventory())
                for bootstrap in (True, False):
                    trial_gate, procedure = self.procedure()
                    with self.subTest(bootstrap=bootstrap), self.assertRaises(gate.TrialDenied):
                        trial_gate.start_registered("new", consent=True, inventory=self.inventory,
                                                    bootstrap=bootstrap)
                    procedure.start.assert_not_called()
                self.assertEqual(mirror.read_bytes(), original + corrupt)
                self.assertEqual(board.read_bytes(), board_before)

    def test_strict_history_rejects_invalid_envelope_sequence_encoding_and_partial_line(self):
        mirror = Path(self.journal.mirror_path("K-38"))
        original = mirror.read_bytes()
        event = {"v": 1, "seq": 2, "card": "K-38", "ts": "now", "type": "step.started"}
        invalid = [b"[]\n", b"{}\n", b"\xff\n", b"\n", json.dumps(event).encode()]
        for changes in ({"seq": 3}, {"card": "K-99"}, {"v": 2}, {"type": None}):
            invalid.append((json.dumps({**event, **changes}) + "\n").encode())
        for suffix in invalid:
            mirror.write_bytes(original + suffix)
            with self.subTest(suffix=suffix):
                # Nie używamy tolerancyjnego read(), który sam może odrzucić złe UTF-8.
                self.assertFalse(trialhold.inventory(self.engine, "K-38", fold([])))

    def test_missing_unsafe_or_unreadable_authoritative_journal_is_not_empty_history(self):
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        mirror.unlink()
        mirror.mkdir()
        self.assertFalse(trialhold.inventory(self.engine, "K-38", fold([])))
        mirror.rmdir()
        mirror.symlink_to(board)
        self.assertFalse(self.inventory())
        mirror.unlink()
        # Brak kopii pozwala czytać prawidłową historię Tablicy; brak obu odmawia.
        self.assertTrue(self.inventory())
        with mock.patch.object(os, "open", side_effect=PermissionError("test odmowy odczytu")):
            self.assertFalse(trialhold.inventory(self.engine, "K-38", fold([])))
        board.unlink()
        self.assertFalse(self.inventory())
        board.write_text("")
        self.assertTrue(self.inventory())

    def test_inventory_refolds_complete_history_instead_of_stale_caller_state(self):
        self.journal.append("K-38", "step.started", step="K-38-PT-176-1", ticket="PT-176")
        self.assertFalse(trialhold.inventory(self.engine, "K-38", fold([])))
