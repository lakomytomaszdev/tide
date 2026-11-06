"""Dowody sprawy: publiczna granica, odporność zapisu i niezaufane pliki."""
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import mock

from . import DAEMON_DIR
from .test_board_dbus import git_config
from tide_daemon.board.store import BoardStore
from tide_daemon.issues import IssueService, IssueStore
from tide_daemon import issue_attachments as att


def png(width, height, *, rgb=False):
    def chunk(kind, raw):
        return struct.pack(">I", len(raw)) + kind + raw + struct.pack(">I", zlib.crc32(kind + raw))
    compressor = zlib.compressobj()
    row = b"\0" * (width * (3 if rgb else 1) + 1)
    compressed = b"".join(compressor.compress(row) for _ in range(height)) + compressor.flush()
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2 if rgb else 0, 0, 0, 0)) +
            chunk(b"IDAT", compressed) + chunk(b"IEND", b""))


def raster(fmt, width=8, height=7):
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QImage
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0xFF123456)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, fmt):
        raise AssertionError("Brak kodera testowego " + fmt)
    return bytes(buffer.data())


class AttachmentCoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="tide-attachment-")
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.dict(os.environ, git_config(self.tmp.name))
        patch.start()
        self.addCleanup(patch.stop)
        self.root = Path(self.tmp.name) / "board"
        self.service = IssueService(BoardStore(str(self.root)))
        self.store = self.service.store
        self.call("Create", {"title": "Dowody", "report": "Raport"})

    def call(self, method, data, raw=None, ident="S-1"):
        args = [json.dumps(data)] if method == "Create" else [ident, json.dumps(data)]
        if raw is not None:
            args.append(raw)
        result = self.service.call(method, *args)
        return (json.loads(result[0]), result[1]) if method == "ReadAttachment" else json.loads(result)

    def attach(self, version, raw=b"dowod", name="plik.txt"):
        result = self.call("Attach", {"name": name, "expectedVersion": version}, raw)
        self.assertTrue(result["ok"], result)
        return result["issue"]

    def read(self, entry, version):
        return self.call("ReadAttachment", {"attachmentId": entry["id"], "expectedVersion": version})

    def test_closed_requests_missing_versions_and_empty_error_data(self):
        issue = self.attach(1)
        entry = issue["attachments"][0]
        good = {"attachmentId": entry["id"], "expectedVersion": 2}
        invalid = [{}, {"expectedVersion": 2}, {**good, "path": "/etc/passwd"},
                   {**good, "attachmentId": "file:///etc/passwd"},
                   *({**good, "expectedVersion": value} for value in (None, True, 0, -1, 1.5, "2"))]
        for method in ("Detach", "ReadAttachment"):
            for data in invalid:
                with self.subTest(method=method, data=data):
                    result = self.call(method, data)
                    if method == "ReadAttachment":
                        result, raw = result
                        self.assertEqual(raw, b"")
                    self.assertEqual(result["error"], "bad-request")
        for data in ({"name": "../a", "expectedVersion": 2}, {"name": "file://a", "expectedVersion": 2},
                     {"name": "a"}, {"name": "a", "expectedVersion": True},
                     {"name": "a", "expectedVersion": 2, "path": "a"}):
            self.assertEqual(self.call("Attach", data, b"x")["error"], "bad-request")
        for ident, version, expected in (("S-2", 2, "not-found"), ("S-1", 1, "version-conflict")):
            result, raw = self.call("ReadAttachment", {**good, "expectedVersion": version}, ident=ident)
            self.assertEqual((result["error"], raw), (expected, b""))
        self.assertEqual(self.store.show("S-1"), issue)

    def test_decode_real_images_and_exact_pixel_limit(self):
        version = 1
        for fmt, suffix, mime in (("PNG", ".png", "image/png"), ("JPEG", ".jpg", "image/jpeg"),
                                  ("WEBP", ".webp", "image/webp")):
            raw = raster(fmt)
            issue = self.attach(version, raw, "obraz" + suffix)
            version += 1
            self.assertEqual(issue["attachments"][-1]["type"], mime)
            self.assertEqual(self.read(issue["attachments"][-1], version)[1], raw)
            for damaged in (raw[:20], raw[:-10], b"obcy plik"):
                self.assertEqual(self.call("Attach", {"name": "bad" + suffix, "expectedVersion": version},
                                           damaged)["error"], "bad-request")
        # Nagłówek i końcówka są poprawne, uszkodzenie wykrywa rzeczywisty dekoder.
        corrupt = bytearray(png(5, 5))
        corrupt[48] ^= 0xFF
        self.assertEqual(self.call("Attach", {"name": "bad.png", "expectedVersion": version},
                                   bytes(corrupt))["error"], "bad-request")
        self.attach(version, png(8000, 5000, rgb=True), "limit.png")
        version += 1
        result = self.call("Attach", {"name": "bomb.png", "expectedVersion": version}, png(8001, 5000))
        self.assertEqual(result["error"], "bad-request")
        for name in ("strona.html", "obraz.svg"):
            raw = b'<svg onload="alert(1)"><script>exit()</script></svg>'
            issue = self.attach(version, raw, name)
            version += 1
            self.assertNotIn(issue["attachments"][-1]["type"], ("image/png", "image/jpeg", "image/webp"))
            self.assertEqual(self.read(issue["attachments"][-1], version)[1], raw)

    def test_jpeg_truncated_scan_with_eoi_does_not_publish(self):
        raw = raster("JPEG", 256, 256)
        original = self.attach(1, raw, "good.jpg")
        manifest = self.root / original["paths"]["manifest"]
        manifest_raw = manifest.read_bytes()
        for removed in (10, 50, 100, 300):
            with self.subTest(removed=removed):
                damaged = raw[:-removed] + b"\xff\xd9"
                with mock.patch.dict(os.environ, {"QT_LOGGING_RULES": "*.warning=false"}):
                    result = self.call("Attach", {"name": "broken.jpg", "expectedVersion": 2}, damaged)
                self.assertFalse(result["ok"], result)
                self.assertEqual(result["error"], "bad-request")
                self.assertEqual(self.store.show("S-1"), original)
                self.assertEqual(manifest.read_bytes(), manifest_raw)

    def test_jpeg_decoder_failure_and_timeout_do_not_publish(self):
        raw = raster("JPEG")
        original = self.store.show("S-1")
        failures = (OSError("brak procesu"), subprocess.TimeoutExpired("dekoder", 15))
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(att.subprocess, "run", side_effect=failure):
                    result = self.call("Attach", {"name": "good.jpg", "expectedVersion": 1}, raw)
                self.assertEqual(result["error"], "bad-request")
                self.assertEqual(self.store.show("S-1"), original)

    def test_submit_and_read_refuse_missing_changed_symlink_and_traversal_copies(self):
        issue = self.attach(1)
        entry = issue["attachments"][0]
        path = self.root / "issues/S-1" / entry["path"]
        source = path.read_bytes()
        for damage in ("missing", "hash", "symlink", "symlink-loop", "parent-symlink", "traversal"):
            with self.subTest(damage=damage):
                manifest_path = self.root / issue["paths"]["manifest"]
                manifest_raw = manifest_path.read_bytes()
                path.unlink()
                if damage == "hash":
                    path.write_bytes(b"zly hash")
                elif damage == "symlink":
                    path.symlink_to("/etc/passwd")
                elif damage == "symlink-loop":
                    path.symlink_to(path.name)
                elif damage == "parent-symlink":
                    path.parent.rmdir()
                    path.parent.symlink_to(Path(self.tmp.name))
                elif damage == "traversal":
                    changed = json.loads(manifest_raw)
                    changed["attachments"][0]["path"] = "../../etc/passwd"
                    manifest_path.write_text(json.dumps(changed))
                result, raw = self.read(entry, 2)
                self.assertEqual((result["error"], raw), ("broken-file", b""))
                self.assertEqual(self.call("Submit", {"expectedVersion": 2})["error"], "broken-file")
                report = (self.root / "issues/S-1/report.md").read_bytes()
                self.assertIn(b'version: 2', report)
                if path.parent.is_symlink():
                    path.parent.unlink()
                    path.parent.mkdir()
                if path.is_symlink() or path.exists():
                    path.unlink()
                path.write_bytes(source)
                manifest_path.write_bytes(manifest_raw)
        self.assertEqual(self.call("Submit", {"expectedVersion": 2})["issue"]["state"], "ready")

    def test_manifest_path_validation_does_not_depend_on_digest_failure(self):
        entry = self.attach(1)["attachments"][0]
        for path in ("../secret", "/etc/passwd", "file:///etc/passwd", "attachments/other"):
            with self.assertRaises(att.BoardError) as error:
                att.manifest_valid({"version": 1, "attachments": [{**entry, "path": path}]})
            self.assertEqual(error.exception.code, "broken-file")

    def test_independent_stores_race_update_attach_detach_with_one_winner(self):
        issue = self.attach(1)
        entry = issue["attachments"][0]
        barrier = Barrier(3)
        def run(operation):
            service = IssueService(BoardStore(str(self.root)))
            data = {"expectedVersion": 2}
            args = []
            if operation == "Update":
                data["title"] = "Zwycięzca"
            elif operation == "Attach":
                data["name"] = "drugi.txt"
                args = [b"drugi"]
            else:
                data["attachmentId"] = entry["id"]
            barrier.wait(timeout=10)
            return json.loads(service.call(operation, "S-1", json.dumps(data), *args))
        with ThreadPoolExecutor(max_workers=3) as pool:
            replies = list(pool.map(run, ("Update", "Attach", "Detach")))
        self.assertEqual(sum(r["ok"] for r in replies), 1)
        for result in replies:
            if not result["ok"]:
                self.assertEqual((result["error"], result["currentVersion"]), ("version-conflict", 3))
        self.assertEqual(self.store.show("S-1"), next(r["issue"] for r in replies if r["ok"]))

    def test_space_failure_before_blob_manifest_and_publication_keeps_old_version(self):
        original = self.store.show("S-1")
        write = IssueStore._write_blob
        for stage in ("attachments", "manifests", "report"):
            def fail_write(path, raw):
                if path.parent.name == stage:
                    raise OSError(errno.ENOSPC, "wymuszony brak miejsca")
                write(path, raw)
            context = (mock.patch("tide_daemon.issues._atomic", side_effect=OSError(errno.ENOSPC, "brak miejsca"))
                       if stage == "report" else mock.patch.object(IssueStore, "_write_blob", side_effect=fail_write))
            with context:
                self.assertEqual(self.call("Attach", {"name": "plik", "expectedVersion": 1}, b"x")["error"], "io-error")
            self.service = IssueService(BoardStore(str(self.root)))
            self.assertEqual(self.service.store.show("S-1"), original)
        self.attach(1)


class AttachmentSourceTest(unittest.TestCase):
    def test_source_mutation_and_replacement_during_copy_are_refused(self):
        spec = importlib.util.spec_from_file_location("standalone_tide", Path(DAEMON_DIR).parent / "cli/tide.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source"
            original_stat = os.fstat
            for replacement in (False, True):
                path.write_bytes(b"pierwotne")
                calls = []
                def changed(fd):
                    before = original_stat(fd)
                    if not calls:
                        calls.append(True)
                        if replacement:
                            path.unlink()
                        path.write_bytes(b"zmiana!!")
                    return before
                with mock.patch.object(cli.os, "fstat", side_effect=changed):
                    with self.assertRaises(cli.CliError) as error:
                        cli.read_attachment_source(str(path))
                self.assertEqual(error.exception.reply["error"], "bad-request")
