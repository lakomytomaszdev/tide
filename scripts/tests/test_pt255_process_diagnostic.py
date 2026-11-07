"""Syntetyczne /proc: tylko wybrane metadane i jawne odmowy, bez operacji hosta."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "tools/pt255_process_diagnostic.py"
SPEC = importlib.util.spec_from_file_location("pt255_process_diagnostic", SCRIPT)
diagnostic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostic)


class ProcessDiagnosticTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.proc = Path(self.temp.name) / "proc"
        self.target = self.proc / "58604"
        for process in (self.proc / "self", self.target):
            (process / "ns").mkdir(parents=True)
            (process / "ns/pid").symlink_to("pid:[123]")
            (process / "status").write_text(f"Name:\tDO_NOT_PUBLISH_STATUS\nUid:\t{os.getuid()} {os.getuid()} {os.getuid()} {os.getuid()}\nGid:\t1 2 3 4\nNSpid:\t58604 8\n")
        (self.target / "comm").write_text("synthetic-process\n")
        (self.target / "stat").write_bytes(self.stat(12345))
        (self.target / "fdinfo").mkdir()
        (self.target / "fdinfo/0").write_text("flags:\t0100002\nother:\tDO_NOT_PUBLISH_FDINFO\n")
        (self.proc / "sys/kernel/random").mkdir(parents=True)
        (self.proc / "sys/kernel/random/boot_id").write_text("synthetic-boot\n")
        (self.proc / "stat").write_text("btime 1700000000\n")

    def stat(self, ticks):
        return b"58604 (odd ) process) " + b" ".join([b"S"] + [b"0"] * 18 + [str(ticks).encode()])

    def test_whitelist_and_identity_comparison_do_not_publish_other_metadata(self):
        read = Path.read_bytes
        link = os.readlink
        reads = []

        def checked_read(path):
            reads.append(path)
            self.assertNotIn(path.name, ("cmdline", "environ"))
            return read(path)

        def checked_link(path):
            self.assertEqual(path.parent.name, "ns")
            return link(path)

        with patch.object(Path, "read_bytes", checked_read), patch.object(os, "readlink", checked_link):
            report = diagnostic.diagnose(58604, self.proc)
        encoded = json.dumps(report)
        self.assertNotIn("DO_NOT_PUBLISH", encoded)
        self.assertEqual(report["facts"]["target-fd0-flags"]["value"], int("0100002", 8))
        self.assertEqual(report["facts"]["start-ticks-before"]["value"], 12345)
        self.assertTrue(report["comparisons"]["scanner-proc-directory-uid-matches-getuid"])
        self.assertTrue(report["comparisons"]["start-identity-stable"])
        self.assertEqual(report["permission-cause"], "not-established")

    def test_fdinfo_denial_is_reported_with_phase_and_errno_without_exception(self):
        read = Path.read_bytes

        def denied(path):
            if path == self.target / "fdinfo/0":
                raise PermissionError(13, "DO_NOT_PUBLISH_EXCEPTION")
            return read(path)

        with patch.object(Path, "read_bytes", denied):
            report = diagnostic.diagnose(58604, self.proc)
        self.assertEqual(report["facts"]["target-fd0-flags"],
                         {"phase": "target-fd0-flags", "ok": False, "errno": 13})
        self.assertNotIn("DO_NOT_PUBLISH_EXCEPTION", json.dumps(report))

    def test_absent_historical_pid_is_an_observation_and_does_not_authorize_cleanup(self):
        report = diagnostic.diagnose(99999, self.proc)
        self.assertEqual(report["comparisons"]["existence"], "absent-at-both-probes")
        self.assertEqual(report["facts"]["target-fd0-flags"]["errno"], 2)
        self.assertIn("diagnostic-only-not-preflight", report["limits"])
        self.assertEqual(report["comparisons"]["historical-pid-identity"], "unknown-no-historical-start-time")

    def test_pid_reuse_during_observation_is_not_stable_identity(self):
        read = Path.read_bytes
        ticks = iter((12345, 12346))

        def replaced(path):
            return self.stat(next(ticks)) if path == self.target / "stat" else read(path)

        with patch.object(Path, "read_bytes", replaced):
            report = diagnostic.diagnose(58604, self.proc)
        self.assertFalse(report["comparisons"]["start-identity-stable"])

    def test_private_report_never_overwrites_existing_file(self):
        target = Path(self.temp.name) / "report.json"
        diagnostic.write_report(target, {"synthetic": True})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            diagnostic.write_report(target, {"replacement": True})
        self.assertEqual(json.loads(target.read_text()), {"synthetic": True})


if __name__ == "__main__":
    unittest.main()
