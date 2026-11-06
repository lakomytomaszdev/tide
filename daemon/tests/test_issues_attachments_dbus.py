"""CLI → rzeczywisty daemon: trwałość dowodów, limity i awarie publikacji."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from . import test_issues_dbus as base
from . import test_issues_attachments as core
from .privatebus import SERVICE, requires_dbus


@requires_dbus
class AttachmentDbusTest(unittest.TestCase):
    setUp = base.IssuesDbusTest.setUp
    start = base.IssuesDbusTest.start
    run_cli = base.IssuesDbusTest.run_cli
    call = base.IssuesDbusTest.call
    mutate = base.IssuesDbusTest.mutate

    def create(self):
        return self.run_cli("issue", "create", "--title", "Dowody", "--report", self.report)["issue"]

    def source(self, raw=b"pierwotne bajty\x00\xff", name="plik.txt", folder="source"):
        path = Path(self.tmp) / folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return str(path)

    def attach(self, issue, path, code=0):
        return self.run_cli("issue", "attach", issue["id"], "--file", path,
                            "--expected-version", str(issue["version"]), code=code)

    def read(self, issue, entry, output="odczyt", code=0, version=True):
        args = ["issue", "attachment", issue["id"], "--attachment", entry["id"],
                "--output", str(Path(self.tmp) / output)]
        if version:
            args += ["--expected-version", str(issue["version"])]
        return self.run_cli(*args, code=code)

    def stop_daemon(self):
        if self.proc.poll() is None:
            self.proc.terminate()
        self.proc.wait(10)
        deadline = time.monotonic() + 5
        while self.bus.bus.interface().isServiceRegistered(SERVICE).value() and time.monotonic() < deadline:
            time.sleep(0.02)

    def fault_start(self):
        control = Path(self.tmp) / "fault.json"
        wrapper = str(Path(__file__).parent / "fixtures/issues_fault_daemon.py")
        spawn = self.bus.spawn
        def instrument(argv, service, **kwargs):
            return spawn([argv[0], wrapper, str(control), *argv[1:]], service, **kwargs)
        with mock.patch.object(self.bus, "spawn", side_effect=instrument):
            self.start()
        return control

    def test_duplicate_names_survive_source_removal_restart_and_detach(self):
        self.start()
        issue = self.create()
        first = self.source(b"pierwszy\x00\xff", folder="one")
        second = self.source(b"drugi\x01\xfe", folder="two")
        issue = self.attach(issue, first)["issue"]
        issue = self.attach(issue, second)["issue"]
        entries = issue["attachments"]
        self.assertEqual([e["name"] for e in entries], ["plik.txt", "plik.txt"])
        self.assertNotEqual(entries[0]["id"], entries[1]["id"])
        expected = [Path(first).read_bytes(), Path(second).read_bytes()]
        Path(first).unlink()
        Path(second).unlink()
        self.stop_daemon()
        self.start()
        shown = self.run_cli("issue", "show", issue["id"])["issue"]
        self.assertEqual(shown, issue)
        for index, entry in enumerate(entries):
            reply = self.read(issue, entry, f"copy-{index}", version=False)
            self.assertEqual((reply["apiVersion"], reply["id"], reply["version"]), (1, issue["id"], 3))
            raw = (Path(self.tmp) / f"copy-{index}").read_bytes()
            self.assertEqual(raw, expected[index])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry["sha256"])
            self.assertEqual((Path(self.tmp) / f"copy-{index}").stat().st_mode & 0o777, 0o600)
        self.read(issue, entries[0], "copy-0", code=7)
        self.assertEqual((Path(self.tmp) / "copy-0").read_bytes(), expected[0])
        target = Path(self.tmp) / "symlink-output"
        target.symlink_to(Path(self.tmp) / "copy-0")
        self.read(issue, entries[0], "symlink-output", code=7)
        other = self.create()
        self.read(other, entries[0], "foreign", code=5)
        issue = self.run_cli("issue", "detach", issue["id"], "--attachment", entries[0]["id"],
                             "--expected-version", "3")["issue"]
        self.assertEqual((issue["version"], len(issue["attachments"])), (4, 1))
        self.read(issue, entries[0], "detached", code=5)
        self.assertFalse((Path(self.tmp) / "detached").exists())
        self.read({**issue, "version": 3}, entries[1], "conflict", code=4)
        self.assertFalse((Path(self.tmp) / "conflict").exists())
        self.read(issue, entries[1], "remaining")
        # Detach nie usuwa źródła, jeśli użytkownik nadal je posiada.
        source = self.source()
        issue = self.attach(issue, source)["issue"]
        self.run_cli("issue", "detach", issue["id"], "--attachment", issue["attachments"][-1]["id"],
                     "--expected-version", str(issue["version"]))
        self.assertTrue(Path(source).is_file())

    def test_cli_rejects_untrusted_sources_and_mutations_require_version(self):
        self.start()
        issue = self.create()
        source = self.source()
        link = Path(self.tmp) / "link"
        link.symlink_to(source)
        parent = Path(self.tmp) / "link-parent"
        parent.symlink_to(Path(source).parent)
        fifo = Path(self.tmp) / "fifo"
        os.mkfifo(fifo)
        invalid = ["/does-not-exist", str(link), str(parent / Path(source).name), str(fifo),
                   str(Path(self.tmp)), "/dev/null", "https://example.org/file", "file://" + source,
                   str(Path(source).parent / ".." / "source" / "plik.txt")]
        for path in invalid:
            with self.subTest(path=path):
                self.assertEqual(self.attach(issue, path, code=2)["error"], "bad-request")
                self.assertEqual(self.run_cli("issue", "show", issue["id"])["issue"], issue)
        self.run_cli("issue", "attach", issue["id"], "--file", source, code=2)
        self.run_cli("issue", "detach", issue["id"], "--attachment", "A-" + "a" * 32, code=2)

    def test_public_read_signature_and_error_have_no_bytes(self):
        from PyQt6.QtCore import QByteArray
        self.start()
        issue = self.attach(self.create(), self.source())["issue"]
        entry = issue["attachments"][0]
        good = {"attachmentId": entry["id"], "expectedVersion": 2}
        for data, error in (({**good, "expectedVersion": 1}, "version-conflict"),
                            ({**good, "path": "/etc/passwd"}, "bad-request"),
                            ({**good, "attachmentId": "A-" + "f" * 32}, "not-found")):
            reply = self.bus.call(SERVICE, "/Issues", base.IFACE, "ReadAttachment", "S-1", json.dumps(data))
            self.assertEqual(reply.signature(), "say")
            result, raw = reply.arguments()
            self.assertEqual(json.loads(result)["error"], error)
            self.assertIsInstance(raw, QByteArray)
            self.assertEqual(bytes(raw), b"")
        reply = self.bus.call(SERVICE, "/Issues", base.IFACE, "ReadAttachment", "S-1", json.dumps(good))
        self.assertEqual(reply.signature(), "say")
        self.assertEqual(bytes(reply.arguments()[1]), Path(self.tmp, "source/plik.txt").read_bytes())

    def test_source_change_during_installed_cli_import_does_not_mutate_daemon(self):
        self.start()
        issue = self.create()
        source = self.source()
        wrapper = str(Path(__file__).parent / "fixtures/issues_mutating_cli.py")
        proc = subprocess.run(["/usr/bin/python3", wrapper, self.cli, "issue", "attach", issue["id"],
                               "--file", source, "--expected-version", "1", "--json"], env=self.env,
                              capture_output=True, text=True, timeout=40)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["error"], "bad-request")
        self.assertIn("Źródło zmieniło", json.loads(proc.stdout)["message"])
        self.assertEqual(self.run_cli("issue", "show", issue["id"])["issue"], issue)

    def test_submit_and_public_read_refuse_broken_evidence_without_publication(self):
        self.start()
        issue = self.attach(self.create(), self.source())["issue"]
        entry = issue["attachments"][0]
        path = Path(self.board) / "issues/S-1" / entry["path"]
        original_copy = path.read_bytes()
        manifest = Path(self.board) / issue["paths"]["manifest"]
        original_manifest = manifest.read_bytes()
        report = Path(self.board) / "issues/S-1/report.md"
        original_report = report.read_bytes()
        for damage in ("missing", "hash", "symlink", "traversal"):
            with self.subTest(damage=damage):
                path.unlink()
                if damage == "hash":
                    path.write_bytes(b"obce bajty")
                elif damage == "symlink":
                    path.symlink_to("/etc/passwd")
                elif damage == "traversal":
                    data = json.loads(original_manifest)
                    data["attachments"][0]["path"] = "../../secret"
                    manifest.write_text(json.dumps(data))
                self.read(issue, entry, "broken-" + damage, code=7)
                self.assertFalse(Path(self.tmp, "broken-" + damage).exists())
                self.assertEqual(self.mutate("submit", 2, code=7)["error"], "broken-file")
                self.assertEqual(report.read_bytes(), original_report)
                if path.exists() or path.is_symlink():
                    path.unlink()
                path.write_bytes(original_copy)
                manifest.write_bytes(original_manifest)
        self.assertEqual(self.mutate("submit", 2)["issue"]["state"], "ready")

    def test_parallel_update_attach_detach_and_lifecycle(self):
        self.start()
        source = self.source()
        issue = self.attach(self.create(), source)["issue"]
        entry = issue["attachments"][0]
        commands = [("update", "--title", "Nowy"), ("attach", "--file", source),
                    ("detach", "--attachment", entry["id"])]
        def run(command):
            action, *args = command
            proc = subprocess.run([self.cli, "issue", action, "S-1", "--expected-version", "2", *args, "--json"],
                                  env=self.env, capture_output=True, text=True, timeout=40)
            return proc.returncode, json.loads(proc.stdout)
        with ThreadPoolExecutor(max_workers=3) as pool:
            replies = list(pool.map(run, commands))
        self.assertEqual(sorted(code for code, _ in replies), [0, 4, 4])
        winner = next(reply["issue"] for code, reply in replies if code == 0)
        self.assertEqual(self.run_cli("issue", "show", "S-1")["issue"], winner)
        ready = self.mutate("submit", 3)["issue"]
        changed = self.attach(ready, source)["issue"]
        self.assertEqual((changed["version"], changed["state"]), (5, "draft"))
        ready = self.mutate("submit", 5)["issue"]
        changed = self.mutate("detach", 6, "--attachment", changed["attachments"][-1]["id"])["issue"]
        self.assertEqual((changed["version"], changed["state"]), (7, "draft"))
        rejected = self.mutate("reject", 7)["issue"]
        self.attach(rejected, source, code=2)
        self.mutate("detach", 8, "--attachment", entry["id"], code=2)
        reopened = self.mutate("reopen", 8)["issue"]
        final = self.attach(reopened, source)["issue"]
        report = Path(self.board) / "issues/S-1/report.md"
        report.write_text(report.read_text().replace('state: "draft"', 'state: "accepted"'))
        self.attach(final, source, code=2)
        self.mutate("detach", final["version"], "--attachment", final["attachments"][-1]["id"], code=2)

    def test_limits_count_file_total_and_decoders_through_cli(self):
        from PyQt6.QtCore import QByteArray
        self.start()
        empty = self.source(b"")
        issue = self.create()
        for _ in range(20):
            issue = self.attach(issue, empty)["issue"]
        self.attach(issue, empty, code=2)
        self.assertEqual(self.run_cli("issue", "show", issue["id"])["issue"], issue)
        large = self.source(b"x" * (20 * 1024 * 1024), name="large.bin")
        issue = self.create()
        for _ in range(5):
            issue = self.attach(issue, large)["issue"]
        self.assertEqual(sum(e["size"] for e in issue["attachments"]), 100 * 1024 * 1024)
        self.read(issue, issue["attachments"][-1], "twenty-mib")
        self.assertEqual(Path(self.tmp, "twenty-mib").stat().st_size, 20 * 1024 * 1024)
        extra = self.source(b"x", name="one.bin")
        self.attach(issue, extra, code=2)
        oversized = self.source(b"x" * (20 * 1024 * 1024 + 1), name="too-large.bin")
        self.attach(issue, oversized, code=2)
        self.assertEqual(self.call("Attach", issue["id"], json.dumps({"name": "large.bin", "expectedVersion": 6}),
                                   QByteArray(Path(oversized).read_bytes()))["error"], "bad-request")
        issue = self.create()
        for raw, name in ((core.png(8000, 5000, rgb=True), "limit.png"), (b"<svg><script>x</script></svg>", "safe.svg"),
                          (b"<html><script>x</script></html>", "safe.html"),
                          (core.raster("JPEG"), "good.jpg"), (core.raster("WEBP"), "good.webp")):
            issue = self.attach(issue, self.source(raw, name=name))["issue"]
        for raw, name in ((core.png(8001, 5000), "bomb.png"), (b"not decoded", "fake.jpg"),
                          (b"RIFF\0\0\0\0WEBP", "broken.webp"), (core.png(10, 10)[:-15], "broken.png")):
            self.attach(issue, self.source(raw, name=name), code=2)

    def test_jpeg_truncated_scan_with_eoi_is_refused_through_cli(self):
        # Ustawienia logowania użytkownika nie mogą wyłączyć ścisłej kontroli JPEG.
        self.env["QT_LOGGING_RULES"] = "*.warning=false"
        self.start()
        raw = core.raster("JPEG", 256, 256)
        issue = self.attach(self.create(), self.source(raw, name="good.jpg"))["issue"]
        manifest = Path(self.board) / issue["paths"]["manifest"]
        original_manifest = manifest.read_bytes()
        report = Path(self.board) / issue["paths"]["report"]
        original_report = report.read_bytes()
        for removed in (10, 50, 100, 300):
            with self.subTest(removed=removed):
                source = self.source(raw[:-removed] + b"\xff\xd9", name="broken.jpg")
                self.assertEqual(self.attach(issue, source, code=2)["error"], "bad-request")
                self.assertEqual(self.run_cli("issue", "show", issue["id"])["issue"], issue)
                self.assertEqual(manifest.read_bytes(), original_manifest)
                self.assertEqual(report.read_bytes(), original_report)
        self.read(issue, issue["attachments"][0], "good-copy")
        self.assertEqual(Path(self.tmp, "good-copy").read_bytes(), raw)

    def test_process_crashes_and_disk_full_keep_complete_old_or_new_version(self):
        control = self.fault_start()
        source = self.source(b"trwaly dowod")
        for action in ("crash", "space"):
            for stage in ("before-copy", "during-copy", "after-copy", "before-manifest", "after-manifest",
                          "before-publish", "after-publish"):
                with self.subTest(action=action, stage=stage):
                    original = self.create()
                    control.write_text(json.dumps({"stage": stage, "action": action}))
                    self.attach(original, source, code=3 if action == "crash" else 7)
                    if action == "crash":
                        self.assertEqual(self.proc.wait(10), 91)
                    self.stop_daemon()
                    control = self.fault_start()
                    issue = self.run_cli("issue", "show", original["id"])["issue"]
                    if stage == "after-publish":
                        self.assertEqual(issue["version"], 2)
                        entry = issue["attachments"][0]
                        self.read(issue, entry, "recovered-" + original["id"])
                        self.assertEqual(Path(self.tmp, "recovered-" + original["id"]).read_bytes(), b"trwaly dowod")
                    else:
                        self.assertEqual(issue, original)
                        # Osierocone kopie i pliki tymczasowe nie są publicznymi załącznikami.
                        for blob in Path(self.board, "issues", issue["id"], "attachments").glob("A-*"):
                            self.read(issue, {"id": blob.name}, "unpublished-" + blob.name, code=5)
        # Restart i odczyt nowej wersji nie zależą od źródła także po awarii.
        Path(source).unlink()
        self.stop_daemon()
        self.start()
        self.read(issue, entry, "without-source")

    def test_detach_crash_at_manifest_and_publication_keeps_access_consistent(self):
        control = self.fault_start()
        source = self.source()
        for stage in ("before-manifest", "after-manifest", "before-publish", "after-publish"):
            issue = self.attach(self.create(), source)["issue"]
            entry = issue["attachments"][0]
            control.write_text(json.dumps({"stage": stage, "action": "crash"}))
            self.run_cli("issue", "detach", issue["id"], "--attachment", entry["id"],
                         "--expected-version", "2", code=3)
            self.assertEqual(self.proc.wait(10), 91)
            self.stop_daemon()
            control = self.fault_start()
            current = self.run_cli("issue", "show", issue["id"])["issue"]
            if stage == "after-publish":
                self.assertEqual((current["version"], current["attachments"]), (3, []))
                self.read(current, entry, "detached-" + issue["id"], code=5)
            else:
                self.assertEqual(current, issue)
                self.read(current, entry, "still-attached-" + issue["id"])
            self.assertTrue(Path(source).exists())
