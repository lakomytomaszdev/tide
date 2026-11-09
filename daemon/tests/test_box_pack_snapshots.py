"""Rdzeń paczek v1: wyłącznie sztuczne źródła i hostowy magazyn testu."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tide_daemon.box import packs


class PackSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        patch = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_STATE_HOME": str(self.home / "state"),
            "XDG_DATA_HOME": str(self.home / "data")})
        patch.start()
        self.addCleanup(patch.stop)
        self.source = self.home / "input"
        self.source.mkdir()
        self.original = self.source / "session.jsonl"
        self.original.write_text("pierwszy zapis")
        self.spec = {"name": "logs", "from": str(self.source / "*.jsonl")}

    def create(self, project="proj", card="K-38", spec=None):
        return packs.create_snapshot(project, card, self.spec if spec is None else spec)

    def copied(self, snapshot):
        return Path(snapshot.directory) / str(self.original).lstrip("/")

    def test_snapshot_contract_and_restart(self):
        snapshot = self.create()
        raw = json.loads(json.dumps(snapshot.to_dict()))
        self.assertEqual(raw["project"], "proj")
        self.assertEqual(raw["card"], "K-38")
        self.assertEqual(raw["version"], 1)
        self.assertEqual(raw["name"], "logs")
        self.assertEqual(len(raw["id"]), 32)
        self.assertEqual(raw["files"][0]["size"], self.original.stat().st_size)
        self.assertEqual(len(raw["files"][0]["sha256"]), 64)
        self.assertIn("filtr, nie gwarancja", raw["scanner"]["scanner"])
        self.assertFalse(raw["scanner"]["blocked"])
        # Odczyt nie używa żadnego stanu procesu ani źródła po utworzeniu kopii.
        self.original.unlink()
        self.assertEqual(packs.load_snapshot("proj", snapshot.id), snapshot)

    def test_same_project_cards_share_copy_but_foreign_project_cannot(self):
        snapshot = self.create(card="K-38")
        for receiving_card in ("K-38", "K-39"):
            with self.subTest(card=receiving_card):
                # Id karty nie jest uprawnieniem API; obie karty dostają kopię projektu.
                with packs.snapshot_access("proj", snapshot.id) as shared:
                    self.assertEqual(self.copied(shared).read_text(), "pierwszy zapis")
        with self.assertRaises(FileNotFoundError):
            packs.load_snapshot("foreign", snapshot.id)
        copied = self.copied(snapshot)
        self.assertNotEqual((copied.stat().st_dev, copied.stat().st_ino),
                            (self.original.stat().st_dev, self.original.stat().st_ino))
        self.assertEqual(copied.stat().st_nlink, 1)
        self.assertEqual(copied.stat().st_mode & 0o777, 0o400)
        copied.chmod(0o600)
        copied.write_text("zmiana w domenie projektu")
        self.assertEqual(self.original.read_text(), "pierwszy zapis")
        with self.assertRaises(packs.PackError):
            packs.load_snapshot("proj", snapshot.id)

    def test_source_changed_after_scan_copies_only_scanned_bytes(self):
        write = packs._copy_at

        def change(parent, path, content):
            self.original.write_text("password=nie-kopiuj")
            return write(parent, path, content)

        with mock.patch.object(packs, "_copy_at", side_effect=change):
            snapshot = self.create()
        self.assertEqual(self.copied(snapshot).read_text(), "pierwszy zapis")
        self.assertFalse(snapshot.scanner["blocked"])

    def test_symlink_swapped_in_during_copy_is_never_read(self):
        write = packs._copy_at
        secret = self.home / "outside"
        secret.write_text("password=nie-czytaj")

        def change(parent, path, content):
            self.original.unlink()
            self.original.symlink_to(secret)
            return write(parent, path, content)

        with mock.patch.object(packs, "_copy_at", side_effect=change):
            snapshot = self.create()
        self.assertEqual(self.copied(snapshot).read_text(), "pierwszy zapis")

    def test_source_parent_replaced_by_symlink_before_read_fails_closed(self):
        read = packs._read
        outside = self.home / "outside"
        outside.mkdir()
        (outside / self.original.name).write_text("nie wolno odczytać")

        def change(path):
            self.source.rename(self.home / "previous")
            self.source.symlink_to(outside, target_is_directory=True)
            return read(path)

        with mock.patch.object(packs, "_read", side_effect=change):
            with self.assertRaises(packs.PackError):
                self.create()
        self.assertFalse(Path(packs.snapshot_root("proj")).exists())

    def test_change_during_source_read_is_rejected(self):
        fstat = os.fstat
        calls = 0

        def change(fd):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.original.write_text("dłuższa zmieniona zawartość")
            return fstat(fd)

        with mock.patch.object(packs.os, "fstat", side_effect=change):
            with self.assertRaisesRegex(packs.PackError, "zmieniło"):
                self.create()

    def test_all_rules_block_without_secret_values_and_manual_choice_works(self):
        values = ["sk-ant-TAJNE1", "ghp_TAJNE2", "glpat-TAJNE3",
                  "-----BEGIN EC PRIVATE KEY-----\nTAJNE4",
                  "Authorization: Bearer TAJNE5", "password=TAJNE6"]
        self.original.write_text("\n".join(values))
        report = packs.describe(self.spec)
        self.assertEqual(len(report["hits"]), 6)
        for n in range(1, 7):
            self.assertNotIn(f"TAJNE{n}", json.dumps(report))
        with self.assertRaises(packs.PackError):
            self.create()
        safe = self.source / "safe.jsonl"
        safe.write_text("bez trafień")
        snapshot = self.create(spec={**self.spec, "files": [str(safe)]})
        self.assertEqual(snapshot.scanner["count"], 1)
        self.assertEqual(len(snapshot.files), 1)

    def test_since_and_forbidden_sources_preserved(self):
        os.utime(self.original, (0, 0))
        snapshot = self.create(spec={**self.spec, "since": "2026-11-01"})
        self.assertEqual(snapshot.files, ())
        for folder in (".ssh", ".gnupg", ".config", ".claude"):
            with self.subTest(folder=folder), self.assertRaises(packs.PackError):
                self.create(spec={"name": "forbidden", "from": str(self.home / folder / "*")})

    def test_partial_copy_is_not_published_and_does_not_damage_previous_pack(self):
        previous = self.create()
        (self.source / "second.jsonl").write_text("drugi zapis")
        write = packs._copy_at
        calls = 0

        def fail(parent, path, content):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("sztuczna awaria dysku")
            write(parent, path, content)

        with mock.patch.object(packs, "_copy_at", side_effect=fail):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual(os.listdir(packs.snapshot_root("proj")), [previous.id])
        self.assertEqual(packs.load_snapshot("proj", previous.id), previous)

    def test_publication_is_one_rename_of_complete_copy(self):
        rename = os.rename
        observed = []

        def publish(source, target, **kwargs):
            store = kwargs["src_dir_fd"]
            self.assertNotIn(target, os.listdir(store))
            stage = Path(packs.snapshot_root("proj")) / source
            raw = json.loads((stage / "snapshot.json").read_text())
            self.assertEqual(raw["id"], target)
            self.assertEqual((stage / "data" / raw["files"][0]["path"]).read_text(),
                             "pierwszy zapis")
            observed.append(target)
            return rename(source, target, **kwargs)

        with mock.patch.object(packs.os, "rename", side_effect=publish):
            snapshot = self.create()
        self.assertEqual(observed, [snapshot.id])
        self.assertEqual(packs.load_snapshot("proj", snapshot.id), snapshot)

    def test_id_collision_never_replaces_existing_snapshot(self):
        snapshot = self.create()
        with mock.patch.object(packs.uuid, "uuid4", return_value=mock.Mock(hex=snapshot.id)):
            with self.assertRaisesRegex(packs.PackError, "zajęty"):
                self.create()
        self.assertEqual(packs.load_snapshot("proj", snapshot.id), snapshot)

    def test_restart_does_not_expose_abandoned_stage_or_partial_copy(self):
        root = Path(packs.snapshot_root("proj"))
        root.mkdir(parents=True)
        stage = root / (".stage-" + "a" * 32)
        stage.mkdir()
        (stage / "data").mkdir()
        partial = root / ("b" * 32)
        partial.mkdir()
        with self.assertRaises(packs.PackError):
            packs.load_snapshot("proj", stage.name)
        with self.assertRaises(FileNotFoundError):
            packs.load_snapshot("proj", partial.name)
        good = self.create()
        self.assertEqual(packs.load_snapshot("proj", good.id), good)
        self.assertTrue(stage.exists())  # Cudzy/niedokończony zapis nie jest sprzątany przy okazji.

    def test_tampering_metadata_missing_file_symlink_and_hardlink_are_rejected(self):
        for kind in ("version", "project", "scanner", "missing", "symlink", "hardlink"):
            with self.subTest(kind=kind):
                snapshot = self.create()
                copied = self.copied(snapshot)
                if kind in ("version", "project", "scanner"):
                    metadata = Path(snapshot.directory).parent / "snapshot.json"
                    raw = json.loads(metadata.read_text())
                    if kind == "scanner":
                        raw["scanner"]["hits"] = [{"path": "x", "rule": "password="}]
                    else:
                        raw[kind] = 999 if kind == "version" else "foreign"
                    metadata.chmod(0o600)
                    metadata.write_text(json.dumps(raw))
                else:
                    copied.unlink()
                    if kind == "symlink":
                        copied.symlink_to(self.original)
                    elif kind == "hardlink":
                        os.link(self.original, copied)
                with self.assertRaises((packs.PackError, OSError)):
                    packs.load_snapshot("proj", snapshot.id)

    def test_symlinked_store_is_rejected_without_writing_through_alias(self):
        outside = self.home / "outside"
        outside.mkdir()
        (self.home / "state").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.create()
        self.assertEqual(list(outside.iterdir()), [])

    def test_revocation_persists_and_delete_requires_exact_detachment_confirmation(self):
        snapshot = self.create()
        second = self.create(card="K-39")
        foreign = self.create(project="foreign")
        for answer in (False, None, 1):
            with self.assertRaises(packs.PackError):
                packs.delete_snapshot("proj", snapshot.id, confirm_detached=lambda *_: answer)
            self.assertTrue(self.copied(snapshot).exists())
            with self.assertRaisesRegex(packs.PackError, "wycofana"):
                packs.load_snapshot("proj", snapshot.id)
        packs.revoke_snapshot("proj", snapshot.id)
        callback = mock.Mock(return_value=True)
        self.assertTrue(packs.delete_snapshot("proj", snapshot.id, confirm_detached=callback))
        callback.assert_called_once_with("proj", snapshot.id)
        self.assertFalse(packs.delete_snapshot("proj", snapshot.id, confirm_detached=callback))
        self.assertEqual(packs.load_snapshot("proj", second.id), second)
        self.assertEqual(packs.load_snapshot("foreign", foreign.id), foreign)
        self.assertEqual(self.original.read_text(), "pierwszy zapis")

    def test_failed_deletion_after_detach_stays_unavailable_and_retry_is_idempotent(self):
        snapshot = self.create()
        with mock.patch.object(packs.shutil, "rmtree", side_effect=OSError("awaria usuwania")):
            with self.assertRaises(OSError):
                packs.delete_snapshot("proj", snapshot.id, confirm_detached=lambda *_: True)
        with self.assertRaises(FileNotFoundError):
            packs.load_snapshot("proj", snapshot.id)
        self.assertTrue(packs.delete_snapshot("proj", snapshot.id, confirm_detached=lambda *_: True))
        self.assertFalse(packs.delete_snapshot("proj", snapshot.id, confirm_detached=lambda *_: True))

    def test_access_serializes_mount_with_revocation(self):
        snapshot = self.create()
        started, finished = threading.Event(), threading.Event()
        errors = []

        def revoke():
            started.set()
            try:
                packs.revoke_snapshot("proj", snapshot.id)
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        with packs.snapshot_access("proj", snapshot.id):
            worker = threading.Thread(target=revoke)
            worker.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(finished.wait(0.05))
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        with self.assertRaises(packs.PackError):
            packs.load_snapshot("proj", snapshot.id)

    def test_delete_rejects_path_injection_and_foreign_project(self):
        snapshot = self.create()
        callback = mock.Mock(return_value=True)
        for name in ("..", "../" + snapshot.id, "/" + snapshot.id, ".stage-" + snapshot.id):
            with self.subTest(name=name), self.assertRaises(packs.PackError):
                packs.delete_snapshot("proj", name, confirm_detached=callback)
        self.assertFalse(packs.delete_snapshot("foreign", snapshot.id, confirm_detached=callback))
        callback.assert_not_called()
        self.assertEqual(packs.load_snapshot("proj", snapshot.id), snapshot)
