"""PT-252: wyłącznie własne syntetyczne kopie; bez uruchamiania usługi."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2] / "tools"


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cleanup = load("cardflow_cleanup")
with patch.dict(sys.modules, {"cardflow_cleanup": cleanup}):
    host = load("cardflow_cleanup_host")


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "cardflow"
        self.source.mkdir()
        self.backup = self.base / "backup"
        self.report = self.base / "report.json"

    def data(self, card="K-1", starts=None):
        starts = [{"worktree": "/tmp/synthetic/work", "repo": ""}] if starts is None else starts
        return b"".join((json.dumps({"v": 1, "seq": n, "ts": "2026-11-06T10:00:00+01:00",
                                    "card": card, "type": "step.started", **fields}) + "\n").encode()
                        for n, fields in enumerate(starts, 1))

    def put(self, name="K-1.jsonl", data=None, key="012345abcdef"):
        path = self.source / key / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(self.data(card=name[:-6]) if data is None else data)
        return path

    def apply(self, plan=None):
        return cleanup.apply(plan or cleanup.preview(self.source), self.backup, self.report, writers_stopped=True)

    def test_qualification_conservative_per_file(self):
        cases = [
            ({"worktree": "/tmp/test"}, "candidate"),
            ({"worktree": "/tmp/a/../test", "repo": ""}, "candidate"),
            ({"worktree": "/tmp/test", "repo": "/real/repo"}, "repo-present-or-unknown"),
            ({"worktree": "/tmp/test", "repo": None}, "repo-present-or-unknown"),
            ({"worktree": "/tmp/test", "repo": " "}, "repo-present-or-unknown"),
            ({"worktree": "tmp/test"}, "unknown-worktree"),
            ({"worktree": "/tmp/../home/test"}, "worktree-outside-tmp"),
            ({"worktree": "/tmp2/test"}, "worktree-outside-tmp"),
            ({"worktree": "/tmp"}, "worktree-outside-tmp"),
            ({"worktree": None}, "unknown-worktree"),
            ({}, "unknown-worktree"),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                self.assertEqual(cleanup.qualify(self.data(starts=[fields]), "K-1"), expected)
        mixed = self.data(starts=[{"worktree": "/tmp/test"}, {"worktree": "/home/real"}])
        self.assertEqual(cleanup.qualify(mixed, "K-1"), "worktree-outside-tmp")
        link = self.base / "link"
        link.symlink_to("/home", target_is_directory=True)
        self.assertEqual(cleanup.qualify(self.data(starts=[{"worktree": str(link / "work")}]), "K-1"),
                         "worktree-outside-tmp")

    def test_bad_data_excluded_including_other_events(self):
        valid = self.data()
        cases = [b"", b"{}\n", b"[]\n", b"\xff", valid + b"{", valid + b"\n",
                 valid.replace(b'"seq": 1', b'"seq": true'),
                 valid.replace(b'"v": 1', b'"v": 2'),
                 valid.replace(b'"seq": 1', b'"seq": 1, "seq": 1'),
                 valid.replace(b'"repo": ""', b'"repo": NaN'),
                 valid.replace(b"K-1", b"K-2"),
                 valid.replace(b"step.started", b"card.created")]
        for data in cases:
            with self.subTest(data=data):
                self.assertNotEqual(cleanup.qualify(data, "K-1"), "candidate")

    def test_unresolvable_worktrees_are_not_deleted(self):
        loop = self.base / "loop"
        loop.symlink_to("loop")
        not_directory = self.base / "not-directory"
        not_directory.write_text("synthetic")
        paths = []
        for n, worktree in enumerate((loop / "work", not_directory / "work"), 1):
            data = self.data(f"K-{n}", [{"repo": "", "worktree": str(worktree)}])
            paths.append(self.put(f"K-{n}.jsonl", data))
        before = {path: path.read_bytes() for path in paths}
        plan = cleanup.preview(self.source)
        self.assertEqual(self.apply(plan)["deleted_count"], 0)
        self.assertEqual(plan["counts"], {"unknown-worktree": 2})
        self.assertEqual({path: path.read_bytes() for path in paths}, before)
        self.assertFalse(self.backup.exists())

    def test_worktree_permission_error_excludes_file(self):
        worktree = self.base / "denied" / "work"
        path = self.put(data=self.data(starts=[{"worktree": str(worktree)}]))
        original = path.read_bytes()
        lstat = os.lstat

        def deny(path, *args, **kwargs):
            if os.fspath(path) == str(worktree.parent):
                raise PermissionError("synthetic-permissions")
            return lstat(path, *args, **kwargs)

        with patch.object(os, "lstat", side_effect=deny):
            plan = cleanup.preview(self.source)
            self.assertEqual(self.apply(plan)["deleted_count"], 0)
            self.assertEqual(plan["counts"], {"unknown-worktree": 1})
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(self.backup.exists())

    def test_missing_historical_worktree_is_allowed_but_links_are_resolved(self):
        missing = self.base / "missing"
        inside = self.base / "inside-link"
        inside.symlink_to(missing, target_is_directory=True)
        outside = self.base / "outside-link"
        outside.symlink_to("/home/synthetic-missing", target_is_directory=True)
        for worktree, expected in (
                (missing / "work", "candidate"),
                (inside / "work", "candidate"),
                (missing / ".." / "inside-link" / "work", "candidate"),
                (outside / "work", "worktree-outside-tmp")):
            with self.subTest(worktree=worktree):
                data = self.data(starts=[{"worktree": str(worktree)}])
                self.assertEqual(cleanup.qualify(data, "K-1"), expected)
        path = self.put(data=self.data(starts=[{"worktree": str(inside / "work")}]))
        self.assertEqual(self.apply()["deleted_count"], 1)
        self.assertFalse(path.exists())

    def test_preview_and_cli_do_not_write_and_do_not_leak_contents(self):
        path = self.put(data=self.data().replace(b'"repo": ""', b'"repo": "SECRET-REPOSITORY"'))
        before = cleanup.snapshot(self.source)
        proc = subprocess.run([sys.executable, str(TOOLS / "cardflow_cleanup.py"), "--source", str(self.source)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["counts"], {"repo-present-or-unknown": 1})
        self.assertNotIn("SECRET", proc.stdout + proc.stderr)
        self.assertEqual(cleanup.snapshot(self.source), before)
        self.assertEqual(set(self.base.iterdir()), {self.source})
        self.assertTrue(path.exists())

    def test_cli_full_roundtrip_and_restore(self):
        candidate = self.put()
        original = candidate.read_bytes()
        kept = self.put("K-2.jsonl", self.data("K-2", [{"repo": "/repo", "worktree": "/tmp/w"}]))
        mixed = self.put("K-3.jsonl", self.data("K-3", [{"worktree": "/tmp/w"}, {"worktree": "/repo/w"}]))
        bad = self.put("K-4.jsonl", b"{broken SECRET-CONTENT")
        unknown = self.put("unknown.jsonl", original)
        link = candidate.parent / "K-5.jsonl"
        link.symlink_to(candidate)
        other = self.put(key="abcdef012345")
        board = self.base / "board"
        board.mkdir()
        (board / "untouched").write_text("repository")
        plan_path = self.base / "preview.json"
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(cleanup.main(["--source", str(self.source), "--plan", str(plan_path)]), 0)
            self.assertEqual(cleanup.main(["--source", str(self.source), "--plan", str(plan_path), "--apply",
                                          "--writers-stopped", "--backup-dir", str(self.backup),
                                          "--report", str(self.report)]), 0)
            self.assertEqual(cleanup.main(["--verify", str(self.report)]), 0)
        report = cleanup.load_json(self.report)
        self.assertEqual(report["deleted_count"], 2)
        self.assertTrue(report["backup_verified"])
        self.assertTrue(report["preserved_verified"])
        self.assertFalse(candidate.exists())
        self.assertFalse(other.parent.exists())
        self.assertTrue(candidate.parent.exists())
        self.assertEqual((board / "untouched").read_text(), "repository")
        self.assertTrue(all(p.exists() for p in (kept, mixed, bad, unknown)))
        self.assertTrue(link.is_symlink())
        self.assertNotIn("SECRET-CONTENT", self.report.read_text())
        self.assertNotIn("SECRET-CONTENT", out.getvalue())
        repeated = cleanup.apply(cleanup.preview(self.source), self.base / "backup2", self.base / "report2.json",
                                 writers_stopped=True)
        self.assertEqual(repeated["deleted_count"], 0)
        self.assertFalse((self.base / "backup2").exists())
        self.assertNotIn("candidate", cleanup.preview(self.source)["counts"])
        candidate.write_bytes((self.backup / "files" / "012345abcdef/K-1.jsonl").read_bytes())
        self.assertEqual(candidate.read_bytes(), original)

    def test_changes_since_preview_and_new_candidates_are_skipped(self):
        path = self.put()
        plan = cleanup.preview(self.source)
        path.write_bytes(self.data(starts=[{"worktree": "/tmp/changed"}]))
        added = self.put("K-2.jsonl")
        report = self.apply(plan)
        self.assertEqual(report["deleted_count"], 0)
        self.assertEqual(report["skipped"]["012345abcdef/K-1.jsonl"], "changed-since-preview")
        self.assertEqual(report["skipped"]["012345abcdef/K-2.jsonl"], "not-in-preview")
        self.assertTrue(path.exists() and added.exists())
        self.assertTrue(report["preserved_verified"])

    def test_backup_failure_or_corruption_never_removes_any_original(self):
        first = self.put()
        second = self.put("K-2.jsonl")
        before = cleanup.snapshot(self.source)
        real_read = cleanup.candidate_bytes
        calls = []

        def failing(source, row):
            calls.append(row["path"])
            if len(calls) == 2:
                raise OSError("synthetic-disk-full")
            return real_read(source, row)

        with patch.object(cleanup, "candidate_bytes", side_effect=failing):
            report = self.apply()
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["deleted_count"], 0)
        self.assertEqual(cleanup.snapshot(self.source), before)
        self.assertTrue(first.exists() and second.exists())
        with patch.object(cleanup, "verify_backup", side_effect=ValueError("synthetic-checksum-error")):
            report = cleanup.apply(cleanup.preview(self.source), self.base / "backup2", self.base / "report2.json",
                                   writers_stopped=True)
        self.assertEqual(report["deleted_count"], 0)
        self.assertFalse(report["backup_verified"])
        self.assertEqual(cleanup.snapshot(self.source), before)

    def test_entire_verified_backup_precedes_first_deletion(self):
        self.put()
        self.put("K-2.jsonl")
        remove = cleanup.remove_candidate
        checked = []

        def check(source, row):
            manifest = cleanup.verify_backup(self.backup)
            self.assertEqual(len(manifest["entries"]), 2)
            self.assertTrue((self.backup / "manifest.json").exists())
            checked.append(row["path"])
            return remove(source, row)

        with patch.object(cleanup, "remove_candidate", side_effect=check):
            self.assertEqual(self.apply()["deleted_count"], 2)
        self.assertEqual(len(checked), 2)

    def test_race_after_backup_preserves_changed_file(self):
        path = self.put()
        remove = cleanup.remove_candidate

        def change(source, row):
            path.write_bytes(self.data(starts=[{"worktree": "/tmp/new"}]))
            return remove(source, row)

        with patch.object(cleanup, "remove_candidate", side_effect=change):
            report = self.apply()
        self.assertEqual(report["deleted_count"], 0)
        self.assertTrue(path.exists())
        self.assertFalse(report["preserved_verified"])
        self.assertEqual(report["status"], "error")

    def test_symlinks_hardlinks_unknown_paths_and_fifo_are_excluded(self):
        path = self.put()
        os.link(path, path.parent / "K-2.jsonl")
        (path.parent / "K-3.jsonl").symlink_to(path)
        os.mkfifo(path.parent / "K-4.jsonl")
        self.put(key="not-a-hash")
        (self.source / "abcdef012345").symlink_to(path.parent, target_is_directory=True)
        plan = cleanup.preview(self.source)
        self.assertNotIn("candidate", plan["counts"])
        self.assertEqual(plan["counts"]["hardlink"], 2)
        self.assertEqual(self.apply(plan)["deleted_count"], 0)
        alias = self.base / "source-alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(OSError):
            cleanup.preview(alias)

    def test_replacement_with_symlink_after_preview_is_not_deleted(self):
        path = self.put()
        plan = cleanup.preview(self.source)
        outside = self.base / "outside.jsonl"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(outside)
        report = self.apply(plan)
        self.assertEqual(report["deleted_count"], 0)
        self.assertTrue(path.is_symlink())
        self.assertTrue(outside.exists())

    def test_cannot_delete_when_preserved_file_cannot_be_hashed(self):
        self.put()
        unreadable = self.put("K-2.jsonl", b"bad")
        original = cleanup.read_regular

        def fail(parent, name):
            if name == unreadable.name:
                raise PermissionError("synthetic-permissions")
            return original(parent, name)

        with patch.object(cleanup, "read_regular", side_effect=fail), self.assertRaises(ValueError):
            self.apply()
        self.assertTrue((self.source / "012345abcdef/K-1.jsonl").exists())

    def test_requires_stopped_writers_external_new_outputs(self):
        self.put()
        plan = cleanup.preview(self.source)
        with self.assertRaises(ValueError):
            cleanup.apply(plan, self.backup, self.report)
        for backup in [self.source / "backup", self.source, self.base]:
            with self.subTest(backup=backup), self.assertRaises(ValueError):
                cleanup.apply(plan, backup, self.report, writers_stopped=True)
        self.report.symlink_to(self.base / "absent")
        with self.assertRaises(OSError):
            self.apply(plan)
        self.assertTrue((self.source / "012345abcdef/K-1.jsonl").exists())

    def test_tampered_backup_or_preserved_set_fails_verification(self):
        self.put()
        kept = self.put("K-2.jsonl", b"bad")
        report = self.apply()
        self.assertTrue(cleanup.verify_report(report)["preserved_verified"])
        kept.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            cleanup.verify_report(report)
        (self.backup / "files/012345abcdef/K-1.jsonl").write_bytes(b"corrupted")
        with self.assertRaises(ValueError):
            cleanup.verify_backup(self.backup)

    def test_host_window_restarts_service_on_failure_and_checks_repeat(self):
        self.put()
        plan_path = self.base / "preview.json"
        cleanup.write_json(plan_path, cleanup.preview(self.source))
        args = host.argparse.Namespace(source=str(self.source), plan=str(plan_path), backup_dir=str(self.backup),
                                      report=str(self.report), repeat_plan=str(self.base / "repeat.json"),
                                      repeat_report=str(self.base / "repeat-report.json"),
                                      repeat_backup_dir=str(self.base / "repeat-backup"), other_writers_stopped=True)
        calls = []

        def control(*argv):
            calls.append(argv)
            if argv[0] == "show":
                return "inactive" if ("stop", host.UNIT) in calls and ("start", host.UNIT) not in calls else "active"
            return ""

        with patch.object(host, "systemctl", side_effect=control):
            result = host.run_window(args)
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["repeat_deleted_count"], 0)
        self.assertIn(("start", host.UNIT), calls)
        calls.clear()
        with patch.object(host, "systemctl", side_effect=control), patch.object(cleanup, "apply", side_effect=ValueError):
            with self.assertRaises(ValueError):
                host.run_window(args)
        self.assertIn(("start", host.UNIT), calls)


if __name__ == "__main__":
    unittest.main()
