"""Kontrola operacyjnej osłony PT-255 na syntetycznych kopiach i atrapach usługi."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("pt255_apply", TOOLS / "pt255_cleanup_apply.py")
operation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(operation)
cleanup = operation.cleanup


class ActivationTest(unittest.TestCase):
    def test_names_are_queued_before_stop_and_both_must_be_owned_after_stop(self):
        class Bus:
            stopped = False
            closed = False
            requests = []

            def name_has_owner(self, name):
                return True

            def get_name_owner(self, name):
                return ":maintenance" if self.stopped else ":service"

            def call_blocking(self, *args):
                return 123

            def request_name(self, name, flags):
                self.requests.append((name, flags))
                return 2

            def get_unique_name(self):
                return ":maintenance"

            def close(self):
                self.closed = True

        bus = Bus()
        with patch.dict(sys.modules, dbus=SimpleNamespace(SessionBus=lambda **kw: bus)):
            blocker = operation.ActivationBlocker(123)
        self.assertEqual(bus.requests, [(name, 0) for name in operation.NAMES])
        with self.assertRaises(ValueError):
            blocker.require_owned()
        bus.stopped = True
        blocker.require_owned()
        blocker.close()
        self.assertTrue(bus.closed)


class ApplyWindowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        folder = self.source / "123456789abc"
        folder.mkdir(parents=True)
        self.journal = folder / "K-1.jsonl"
        self.journal.write_text(json.dumps({"v": 1, "seq": 1, "card": "K-1",
            "ts": "2026-11-06T00:00:00+00:00", "type": "step.started",
            "repo": "", "worktree": str(self.root / "historical-worktree")}) + "\n")
        self.plan = cleanup.preview(str(self.source))
        self.assertEqual(self.plan["counts"], {"candidate": 1})
        self.args = argparse.Namespace(source=str(self.source), plan=str(self.root / "preview.json"),
            preview_metadata=str(self.root / "preview-host.json"), backup_dir=str(self.root / "backup"),
            report=str(self.root / "apply.json"), repeat_plan=str(self.root / "repeat.json"),
            repeat_backup_dir=str(self.root / "repeat-backup"), repeat_report=str(self.root / "repeat-apply.json"))
        cleanup.write_json(self.args.plan, self.plan)
        cleanup.write_json(self.args.preview_metadata, {"preview_returncode": 0,
            "source": str(self.source), "tool_sha256": operation.TOOL_SHA256})
        self.events = []
        self.audit = SimpleNamespace(event=lambda kind, **values: self.events.append((kind, values)))
        self.active = True
        self.blocker = None
        self.calls = []
        outer = self

        class Blocker:
            def __init__(self, pid):
                self.bus = True
                outer.blocker = self

            def require_owned(self):
                if outer.active or not self.bus:
                    raise ValueError("activation-name-not-held")

            def close(self):
                self.bus = None

        self.enterContext(patch.object(operation, "ActivationBlocker", Blocker))
        self.enterContext(patch.object(operation, "service_metadata", return_value={"main_pid": 123}))
        self.processes = self.enterContext(patch.object(operation, "check_processes", return_value={"active_tests": 0}))
        self.enterContext(patch.object(operation, "PREVIEW_COUNTS", {"candidate": 1}))
        self.enterContext(patch.object(operation.host, "systemctl", side_effect=self.control))

    def control(self, *args):
        self.calls.append(args)
        if args[0] == "stop":
            self.active = False
        if args[0] == "start":
            self.assertIsNone(self.blocker.bus, "rezerwacja musi ustąpić przed startem usługi")
            self.active = True
        if args[0] == "show":
            return "active" if self.active else "inactive"
        return ""

    def test_complete_backup_is_audited_before_deletion_and_repeat_is_empty(self):
        result = operation.run(self.args, self.audit)
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["repeat_deleted_count"], 0)
        self.assertTrue(self.active)
        self.assertFalse(self.journal.exists())
        manifest = cleanup.verify_backup(self.args.backup_dir)
        self.assertEqual(manifest["entries"], self.plan["entries"])
        events = [kind for kind, _ in self.events]
        self.assertLess(events.index("complete-backup-verified"),
                        events.index("first-deletion-authorized-after-backup"))
        self.assertLess(events.index("first-deletion-authorized-after-backup"),
                        events.index("activation-reservation-released"))
        self.assertFalse(Path(self.args.repeat_backup_dir).exists())

    def test_backup_error_preserves_source_releases_names_and_restarts(self):
        original = self.journal.read_bytes()
        with patch.object(cleanup, "create_backup", side_effect=OSError("synthetic failure")):
            with self.assertRaises(ValueError):
                operation.run(self.args, self.audit)
        self.assertEqual(self.journal.read_bytes(), original)
        self.assertTrue(self.active)
        self.assertIsNone(self.blocker.bus)
        self.assertNotIn("first-deletion-authorized-after-backup", [kind for kind, _ in self.events])

    def test_active_test_refuses_before_service_stop(self):
        self.processes.side_effect = ValueError("active-test-pid-456")
        with self.assertRaises(ValueError):
            operation.run(self.args, self.audit)
        self.assertTrue(self.journal.exists())
        self.assertTrue(self.active)
        self.assertNotIn(("stop", operation.host.UNIT), self.calls)

    def test_failed_stop_still_releases_names_and_restarts(self):
        control = self.control

        def failing_stop(*args):
            result = control(*args)
            if args[0] == "stop":
                raise subprocess.CalledProcessError(1, ["synthetic-systemctl"])
            return result

        with patch.object(operation.host, "systemctl", side_effect=failing_stop):
            with self.assertRaises(subprocess.CalledProcessError):
                operation.run(self.args, self.audit)
        self.assertTrue(self.active)
        self.assertIsNone(self.blocker.bus)
        self.assertTrue(self.journal.exists())

    def test_tampered_preview_refuses_before_stopping_service(self):
        self.plan["counts"] = {"candidate": 99}
        Path(self.args.plan).write_text(json.dumps(self.plan))
        with self.assertRaises(ValueError):
            operation.run(self.args, self.audit)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.journal.exists())


class ProcessClassificationTest(unittest.TestCase):
    def test_supported_test_runners_and_daemon_are_recognized(self):
        for argv in (["python3", "-m", "unittest", "discover"], ["python3", "-m", "pytest"],
                     ["/usr/bin/pytest"], ["python3", "/repo/tools/test_runner.py", "--changed"],
                     ["python3", "/repo/daemon/tests/guard_probe.py"], ["python3", "test_new.py"]):
            self.assertEqual(operation.process_kind(argv), "test", argv)
        self.assertEqual(operation.process_kind(["python3", "/installed/tide-daemon.py"]), "daemon")
        self.assertIsNone(operation.process_kind(["editor", "prompt mentions -m unittest but is one argument"]))

    def test_writable_descriptor_blocks_even_an_unknown_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process = root / "999999999"
            (process / "fd").mkdir(parents=True)
            (process / "fdinfo").mkdir()
            (process / "cmdline").write_bytes(b"unknown-program\0")
            source = root / "source"
            source.mkdir()
            (process / "fd" / "3").symlink_to(source / "not-yet-existing.jsonl")
            (process / "fdinfo" / "3").write_text("flags:\t0100001\n")
            original_iterdir = Path.iterdir

            def processes(path):
                return iter([process]) if str(path) == "/proc" else original_iterdir(path)

            with patch.object(Path, "iterdir", processes):
                with self.assertRaisesRegex(ValueError, "active-writer-pid"):
                    operation.check_processes(str(source))
                (process / "fdinfo" / "3").write_text("flags:\t0100000\n")
                self.assertEqual(operation.check_processes(str(source))["writable_source_descriptors"], 0)


if __name__ == "__main__":
    unittest.main()
