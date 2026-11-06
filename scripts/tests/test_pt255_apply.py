"""Kontrola operacyjnej osłony PT-255 na syntetycznych kopiach i atrapach usługi."""
import argparse
from contextlib import contextmanager
import errno
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

    def test_diagnostic_preflight_never_reserves_names_or_stops_service(self):
        self.args.preflight_only = True
        result = operation.run(self.args, self.audit)
        self.assertTrue(result["preflight_only"])
        self.assertIsNone(self.blocker)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.journal.exists())

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
    def setUp(self):
        # Żaden test syntetycznego /proc nie pyta prawdziwego Incusa ani systemd.
        self.config_query = self.enterContext(patch.object(operation.subprocess, "run",
            return_value=SimpleNamespace(stdout="")))

    def test_read_only_fd_needs_no_target_but_uninspectable_writable_fd_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Path(directory) / "999999999"
            (process / "fd").mkdir(parents=True)
            (process / "fdinfo").mkdir()
            (process / "cmdline").write_bytes(b"unknown-program\0")
            (process / "fd" / "3").touch()
            original_iterdir = Path.iterdir

            def processes(path):
                return iter([process]) if str(path) == "/proc" else original_iterdir(path)

            with patch.object(Path, "iterdir", processes), patch.object(
                    operation.os, "readlink", side_effect=PermissionError(13, "not disclosed")) as target:
                (process / "fdinfo" / "3").write_text("flags:\t0100000\n")
                self.assertEqual(operation.check_processes("/synthetic/source")["writable_source_descriptors"], 0)
                target.assert_not_called()
                for flags in ("0100001", "0100002"):
                    with self.subTest(flags=flags):
                        (process / "fdinfo" / "3").write_text(f"flags:\t{flags}\n")
                        with self.assertRaisesRegex(ValueError, "process-metadata-unavailable"):
                            operation.check_processes("/synthetic/source")
                with patch.object(Path, "read_text", side_effect=PermissionError(13, "not disclosed")):
                    with self.assertRaisesRegex(ValueError, "process-metadata-unavailable"):
                        operation.check_processes("/synthetic/source")

    def test_metadata_denial_reports_pid_and_phase_without_exception_text(self):
        with tempfile.TemporaryDirectory() as directory:
            process = Path(directory) / "999999999"
            process.mkdir()
            (process / "cmdline").write_bytes(b"unknown-program\0")
            (process / "cgroup").write_text("0::/user.slice/host.scope\n")
            original_iterdir = Path.iterdir

            def processes(path):
                if str(path) == "/proc":
                    return iter([process])
                if path == process / "fd":
                    raise PermissionError(13, "DO_NOT_DISCLOSE")
                return original_iterdir(path)

            with patch.object(Path, "iterdir", processes):
                try:
                    operation.check_processes("/synthetic/source")
                except ValueError as error:
                    self.assertEqual(error.details, {"pid": 999999999, "phase": "list-fds", "errno": 13})
                    self.assertNotIn("DO_NOT_DISCLOSE", str(error))
                except OSError:
                    self.fail("Odmowa nie podaje kontrolowanego PID/fazy i kończy się ogólnym błędem")
                else:
                    self.fail("Nie wolno pomijać procesu z niedostępnymi metadanymi")

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
                # Wykryty zapis zawsze blokuje, bez pytań o wyjątki.
                with self.assertRaisesRegex(ValueError, "active-writer-pid"):
                    operation.check_processes(str(source))
                self.config_query.assert_not_called()
                (process / "fdinfo" / "3").write_text("flags:\t0100000\n")
                self.assertEqual(operation.check_processes(str(source))["writable_source_descriptors"], 0)


class ContainerIsolationTest(unittest.TestCase):
    CGROUP = "0::/lxc.payload.user-1000_toolssuite/user.slice/user@1000.service/init.scope\n"
    SOURCE = "/synthetic/state/tide/cardflow"

    @contextmanager
    def process(self, phase="read-fdinfo", error_number=errno.EACCES, cgroup=CGROUP):
        with tempfile.TemporaryDirectory() as directory:
            process = Path(directory) / "999999999"
            (process / "fd").mkdir(parents=True)
            (process / "fdinfo").mkdir()
            (process / "cmdline").write_bytes(b"systemd\0--user\0")
            (process / "cgroup").write_text(cgroup)
            (process / "fd" / "0").touch()
            (process / "fdinfo" / "0").write_text("flags:\t0100002\n")
            original_iterdir, original_text, original_bytes = Path.iterdir, Path.read_text, Path.read_bytes
            denied = PermissionError(error_number, "DO_NOT_DISCLOSE")

            def processes(path):
                if str(path) == "/proc":
                    return iter([process])
                if phase == "list-fds" and path == process / "fd":
                    raise denied
                return original_iterdir(path)

            def read_text(path, *args, **kwargs):
                if phase == "read-fdinfo" and path == process / "fdinfo" / "0":
                    raise denied
                return original_text(path, *args, **kwargs)

            def read_bytes(path):
                if phase == "read-cmdline" and path == process / "cmdline":
                    raise denied
                return original_bytes(path)

            with patch.object(Path, "iterdir", processes), patch.object(Path, "read_text", read_text), patch.object(
                    Path, "read_bytes", read_bytes), patch.object(operation.os, "readlink", side_effect=denied), patch.object(
                    operation.subprocess, "run", return_value=SimpleNamespace(stdout=self.configuration())) as query:
                events = []
                audit = SimpleNamespace(event=lambda kind, **values: events.append((kind, values)))
                yield process, query, audit, events

    def configuration(self, source="/synthetic/box-home"):
        return json.dumps({"config": {"user.secret": "DO_NOT_DISCLOSE"}, "devices": {
            "home": {"type": "disk", "source": source, "path": "/home/test"},
            "root": {"type": "disk", "path": "/", "pool": "default"},
            "eth0": {"type": "nic", "network": "incusbr0"}}})

    def test_container_without_source_mount_is_skipped_and_audited_for_fd_eacces_only(self):
        for phase in ("list-fds", "read-fdinfo", "read-fdlink"):
            with self.subTest(phase=phase), self.process(phase) as (_, query, audit, events):
                result = operation.check_processes(self.SOURCE, audit=audit)
                skip = result["inspection_skips"][0]
                self.assertEqual(skip["pid"], 999999999)
                self.assertEqual(skip["container"], {"project": "user-1000", "name": "toolssuite"})
                self.assertEqual(skip["reason"], "incus-container-without-source-mount")
                self.assertEqual(skip["phase"], phase)
                self.assertEqual(skip["errno"], errno.EACCES)
                self.assertEqual([row["device"] for row in skip["checked_disks"]], ["home", "root"])
                self.assertEqual(skip["checked_disks"][0]["source_relation"], "disjoint")
                self.assertEqual(events, [("process-inspection-skipped", skip)])
                self.assertNotIn("DO_NOT_DISCLOSE", json.dumps(result))
                query.assert_called_once_with(["incus", "config", "show", "toolssuite", "--project",
                    "user-1000", "--expanded"], check=True, capture_output=True, text=True, timeout=10)

    def test_source_ancestor_and_descendant_mounts_refuse(self):
        for source in (self.SOURCE, "/synthetic/state", "/", self.SOURCE + "/child",
                       "/synthetic/state/tide/elsewhere/../cardflow"):
            with self.subTest(source=source), self.process() as (_, query, audit, events):
                query.return_value.stdout = self.configuration(source)
                with self.assertRaises(operation.InspectionError) as failure:
                    operation.check_processes(self.SOURCE, audit=audit)
                self.assertEqual(failure.exception.details["phase"], "container-source-overlap")
                self.assertEqual(events, [])

    def test_symlink_to_source_refuses_but_sibling_prefix_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "source-alias"
            link.symlink_to(self.SOURCE)
            with self.process() as (_, query, audit, events):
                query.return_value.stdout = self.configuration(str(link))
                with self.assertRaises(operation.InspectionError):
                    operation.check_processes(self.SOURCE, audit=audit)
            with self.process() as (_, query, audit, events):
                query.return_value.stdout = self.configuration(self.SOURCE + "-other")
                self.assertEqual(len(operation.check_processes(self.SOURCE, audit=audit)["inspection_skips"]), 1)

    def test_unavailable_config_refuses_without_disclosing_output(self):
        for error in (subprocess.CalledProcessError(1, ["incus"], output="DO_NOT_DISCLOSE"),
                      subprocess.TimeoutExpired(["incus"], 10), PermissionError(errno.EACCES, "DO_NOT_DISCLOSE")):
            with self.subTest(error=type(error).__name__), self.process() as (_, query, audit, events):
                query.side_effect = error
                with self.assertRaises(operation.InspectionError) as failure:
                    operation.check_processes(self.SOURCE, audit=audit)
                self.assertEqual(failure.exception.details["phase"], "container-config-unavailable")
                self.assertNotIn("DO_NOT_DISCLOSE", str(failure.exception) + json.dumps(failure.exception.details))
                self.assertEqual(events, [])

    def test_outside_container_or_other_denial_never_uses_exception(self):
        for options in ({"cgroup": "0::/user.slice/user-1000.slice/user@1000.service/init.scope\n"},
                        {"cgroup": "0::/not-lxc.payload.user-1000_toolssuite/scope\n"},
                        {"phase": "read-cmdline"}, {"error_number": errno.EPERM}):
            with self.subTest(options=options), self.process(**options) as (_, query, audit, events):
                with self.assertRaises(operation.InspectionError):
                    operation.check_processes(self.SOURCE, audit=audit)
                query.assert_not_called()
                self.assertEqual(events, [])

    def test_unreadable_or_changed_cgroup_refuses(self):
        with self.process() as (process, query, audit, events):
            (process / "cgroup").unlink()
            with self.assertRaises(operation.InspectionError):
                operation.check_processes(self.SOURCE, audit=audit)
            query.assert_not_called()
        with self.process() as (process, query, audit, events):
            def changed(*args, **kwargs):
                (process / "cgroup").write_text("0::/user.slice/host.scope\n")
                return SimpleNamespace(stdout=self.configuration())

            query.side_effect = changed
            with self.assertRaises(operation.InspectionError):
                operation.check_processes(self.SOURCE, audit=audit)
            self.assertEqual(events, [])

    def test_configuration_is_rechecked_and_active_tests_still_block(self):
        with self.process() as (_, query, audit, events):
            operation.check_processes(self.SOURCE, audit=audit)
            query.return_value.stdout = self.configuration(self.SOURCE)
            with self.assertRaises(operation.InspectionError):
                operation.check_processes(self.SOURCE, audit=audit)
            self.assertEqual(query.call_count, 2)
            self.assertEqual(len(events), 1)
        with self.process() as (process, query, audit, events):
            (process / "cmdline").write_bytes(b"python3\0-m\0unittest\0")
            with self.assertRaisesRegex(ValueError, "active-test-pid"):
                operation.check_processes(self.SOURCE, audit=audit)
            query.assert_not_called()
            self.assertEqual(events, [])

    def test_invalid_or_ambiguous_config_never_authorizes_skip(self):
        for output in ("", "not-a-mapping", "{}", '{"devices": []}', '{"devices": {"x": {}}}',
                       self.configuration("relative/unknown"), "devices: {}\ndevices: {}\n",
                       "devices: !!python/object/apply:os.system ['DO_NOT_DISCLOSE']"):
            with self.subTest(output=output), self.process() as (_, query, audit, events):
                query.return_value.stdout = output
                with self.assertRaises(operation.InspectionError):
                    operation.check_processes(self.SOURCE, audit=audit)
                self.assertEqual(events, [])

    def test_expanded_yaml_devices_are_checked_without_exposing_other_config(self):
        with self.process() as (_, query, audit, events):
            query.return_value.stdout = """config:
  user.secret: DO_NOT_DISCLOSE
devices:
  home:
    type: disk
    source: /synthetic/box-home
    path: /home/test
  root:
    type: disk
    pool: default
    path: /
"""
            result = operation.check_processes(self.SOURCE, audit=audit)
            self.assertEqual(len(result["inspection_skips"]), 1)
            self.assertNotIn("DO_NOT_DISCLOSE", json.dumps(events))


if __name__ == "__main__":
    unittest.main()
