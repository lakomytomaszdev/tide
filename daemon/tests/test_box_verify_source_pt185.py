"""PT-185: verify na kopii źródeł, bez montowania repozytorium ani danych hosta."""
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock
from contextlib import nullcontext
import io

from tide_daemon.box.builder import Builder, BuildError
from tide_daemon.box.settings import BoxSettings
from tide_daemon.box import verify_source
from tide_daemon.box import cli


class VerifyContextTest(unittest.TestCase):
    def test_cli_passes_archive_and_commit_to_builder_within_snapshot_context(self):
        service = mock.Mock()
        with mock.patch.object(cli, "_settings", return_value=BoxSettings()), \
                mock.patch.object(cli.bbase, "ensure_proxy", return_value=(service, True)), \
                mock.patch("tide_daemon.box.command.project_repo", return_value="/project"), \
                mock.patch.object(verify_source, "snapshot", return_value=nullcontext(("/tmp/src.tar", "sha"))) as snap, \
                mock.patch.object(cli.bb, "Builder") as builder, \
                mock.patch.object(cli, "run_build", return_value=0) as run_build:
            self.assertEqual(cli.main(["build", "toolssuite", "--verify-worktree", "/project"],
                                      out=io.StringIO(), err=io.StringIO()), 0)
            snap.assert_called_once_with("/project", "/project")
            self.assertEqual(builder.call_args.kwargs["verify_archive"], "/tmp/src.tar")
            self.assertEqual(builder.call_args.kwargs["verify_commit"], "sha")
            self.assertEqual(run_build.call_args.args[1], "toolssuite")
        service.stop.assert_called_once()

    def builder(self, **kwargs):
        self.incus = mock.Mock()
        self.incus.exec.return_value = 0
        return Builder(BoxSettings(), incus=self.incus, **kwargs)

    def test_commands_use_copied_sources_and_cleanup_precedes_return(self):
        b = self.builder(verify_archive="/tmp/source.tar", verify_commit="abc")
        env = {"HTTPS_PROXY": "http://proxy"}
        self.assertEqual(b._verify("box", ["npm ci", "npm run lint"], env), [])
        self.incus.file_push.assert_called_once_with("/tmp/source.tar", "box", "/opt/tide/verify/source.tar")
        context = {"cwd": "/opt/tide/verify/work", "user": b.settings.agent_uid, "group": b.settings.agent_gid}
        verify_env = {**env, "HOME": "/home/agent"}
        self.assertIn(mock.call("box", ["bash", "-c", "npm ci"], verify_env, **context),
                      self.incus.exec.call_args_list)
        self.assertIn(mock.call("box", ["bash", "-c", "npm run lint"], verify_env, **context),
                      self.incus.exec.call_args_list)
        self.assertEqual(self.incus.exec.call_args_list[-1].args[1],
                         ["rm", "-rf", "--", "/opt/tide/verify"])
        self.incus.device_add.assert_not_called()

    def test_failed_install_stops_lint_and_still_cleans_sources(self):
        b = self.builder(verify_archive="source.tar")
        self.incus.exec.side_effect = lambda name, argv, env, **kw: int(argv == ["bash", "-c", "npm ci"])
        self.assertEqual(b._verify("box", ["npm ci", "npm run lint"], {}), ["npm ci"])
        self.assertNotIn(["bash", "-c", "npm run lint"], [c.args[1] for c in self.incus.exec.call_args_list])
        self.assertEqual(self.incus.exec.call_args_list[-1].args[1][0], "rm")

    def test_failed_extract_still_cleans_and_raises(self):
        b = self.builder(verify_archive="source.tar")
        self.incus.exec.side_effect = lambda name, argv, env, **kw: int(argv[0] == "tar")
        with self.assertRaises(ValueError):
            b._verify("box", ["npm run lint"], {})
        self.assertEqual(self.incus.exec.call_args_list[-1].args[1][0], "rm")

    def test_without_context_preserves_existing_verify_behavior(self):
        b = self.builder()
        self.incus.exec.return_value = 1
        self.assertEqual(b._verify("box", ["false", "false"], {}), ["false", "false"])
        self.incus.file_push.assert_not_called()
        self.assertEqual(self.incus.exec.call_args_list, [mock.call("box", ["bash", "-c", "false"], {})] * 2)

    def test_provision_uses_archive_and_records_commit_before_snapshot(self):
        from tide_daemon.box import builder as bb, profile
        b = self.builder(verify_archive="source.tar", verify_commit="abc")
        b._run = mock.Mock()
        b.stamp = mock.Mock(return_value={"hash": "recipe"})
        full = profile.with_defaults({"tide-box": 1, "verify": ["npm ci", "npm run lint"]})
        with mock.patch.object(bb.bs, "record") as record, \
                mock.patch.object(bb.verification, "project_source") as fallback:
            result = b._provision("toolssuite", "box", full, "http://proxy", [])
        self.assertFalse(result.warning)
        fallback.assert_not_called()
        self.incus.file_push.assert_called_once_with("source.tar", "box", "/opt/tide/verify/source.tar")
        event = next(c for c in record.call_args_list if c.args[1] == "box.verify")
        self.assertEqual(event.kwargs["source_commit"], "abc")
        calls = self.incus.mock_calls
        cleanup = next(i for i, c in enumerate(calls)
                       if c == mock.call.exec("box", ["rm", "-rf", "--", "/opt/tide/verify"], mock.ANY)
                       and i > 0)
        snapshot = next(i for i, c in enumerate(calls) if c[0] == "snapshot_create")
        self.assertLess(cleanup, snapshot)


class SourceSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()
        self.env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                    "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
        self.git("init", "-q")
        (self.repo / "package.json").write_text('{}\n')
        (self.repo / ".gitignore").write_text("node_modules/\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], env=self.env, text=True).strip()

    def test_archive_contains_only_committed_files_and_is_removed_after_use(self):
        (self.repo / "node_modules").mkdir()
        (self.repo / "node_modules/token").write_text("private")
        (self.repo / "untracked").write_text("private")
        with verify_source.snapshot(self.repo, self.repo) as (archive, commit):
            self.assertEqual(commit, self.git("rev-parse", "HEAD"))
            with tarfile.open(archive) as tar:
                self.assertEqual(set(tar.getnames()), {".gitignore", "package.json"})
        self.assertFalse(Path(archive).exists())

    def test_dirty_sources_or_other_repository_are_rejected(self):
        with self.assertRaises(BuildError):
            with verify_source.snapshot(self.repo, self.repo.parent):
                self.fail("obce repozytorium")
        (self.repo / "package.json").write_text('{"changed":true}\n')
        with self.assertRaises(BuildError):
            with verify_source.snapshot(self.repo, self.repo):
                self.fail("zmiany spoza HEAD")

    def test_snapshot_does_not_execute_repository_fsmonitor(self):
        monitor = Path(self.tmp.name) / "monitor"
        marker = Path(str(monitor) + ".ran")
        monitor.write_text('#!/bin/sh\nprintf ran > "$0.ran"\nprintf "\\n"\n')
        monitor.chmod(0o700)
        self.git("config", "core.fsmonitor", str(monitor))
        # Kontrola czułości próby wyłącznie w jednorazowym repozytorium testowym.
        self.git("status", "--porcelain", "--untracked-files=no")
        self.assertTrue(marker.exists())
        marker.unlink()

        with verify_source.snapshot(self.repo, self.repo) as (archive, commit):
            self.assertTrue(Path(archive).is_file())
            self.assertEqual(commit, self.git("rev-parse", "HEAD"))
        self.assertFalse(marker.exists(), "verify uruchomiło monitor projektu na hoście")


if __name__ == "__main__":
    unittest.main()
