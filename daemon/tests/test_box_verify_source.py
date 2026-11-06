"""PT-184: verify budowy na archiwum commita, bez zapisu do worktree projektu."""
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

from tide_daemon.box import builder as bb, cli, profile as bp, verify
from tide_daemon.box.incus import Incus
from tide_daemon.box.settings import BoxSettings


class VerifySourceTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "repo"
        self.repo.mkdir()
        self.git(self.repo, "init", "-b", "main")
        (self.repo / "project.txt").write_text("source")
        (self.repo / ".gitignore").write_text("bin/\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-m", "Initial project")
        self.worktree = Path(str(self.repo) + "-worktrees") / "verify"
        self.git(self.repo, "worktree", "add", "-b", "verify", str(self.worktree))
        patcher = mock.patch("tide_daemon.box.command.project_repo", return_value=str(self.repo))
        patcher.start()
        self.addCleanup(patcher.stop)

    def git(self, cwd, *args):
        return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false",
                               "-c", "user.name=Test", "-c", "user.email=test@example.org", *args],
                              cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def test_export_contains_only_committed_files_and_is_removed_after_use(self):
        (self.worktree / "bin").mkdir()
        (self.worktree / "bin/secret").write_text("not source")
        with verify.source_archive("project", str(self.worktree)) as source:
            self.assertEqual(source.commit, self.git(self.worktree, "rev-parse", "HEAD"))
            with tarfile.open(source.archive) as archive:
                self.assertEqual(set(archive.getnames()), {"project.txt", ".gitignore"})
            path = source.archive
        self.assertFalse(Path(path).exists())
        self.assertEqual(self.git(self.worktree, "status", "--porcelain"), "")

    def test_rejects_main_foreign_and_dirty_worktrees(self):
        foreign = self.worktree.parent / "foreign"
        foreign.mkdir()
        self.git(foreign, "init")
        for path in (self.repo, foreign, self.worktree.parent):
            with self.assertRaises(ValueError), verify.source_archive("project", str(path)):
                self.fail("niedozwolone źródło verify")
        for path in (self.worktree / "untracked.txt", self.worktree / "project.txt"):
            path.write_text("dirty")
            with self.assertRaisesRegex(ValueError, "niezapisane"), verify.source_archive("project", str(self.worktree)):
                self.fail("niezapisane źródło verify")
            if path.name == "untracked.txt":
                path.unlink()

    def test_unpack_and_verify_context_stay_inside_box_and_cleanup_on_failure(self):
        incus = mock.Mock()
        incus.exec.return_value = 0
        source = verify.Source("/tmp/archive.tar", "abc")
        with self.assertRaisesRegex(RuntimeError, "verify failed"):
            with verify.in_box(incus, "box", source, {"HTTPS_PROXY": "proxy"},
                               agent_uid=1000, agent_gid=1000) as context:
                self.assertEqual(context, {"user": 1000, "group": 1000, "cwd": "/opt/tide/verify/work"})
                raise RuntimeError("verify failed")
        incus.file_push.assert_called_once_with(source.archive, "box", "/opt/tide/verify/source.tar")
        tar_call = next(c for c in incus.exec.call_args_list if c.args[1][0] == "tar")
        self.assertEqual(tar_call.kwargs["user"], 1000)
        self.assertEqual(tar_call.kwargs["group"], 1000)
        self.assertEqual(incus.exec.call_args_list[-1].args[1], ["rm", "-rf", "--", "/opt/tide/verify"])

    def test_failed_unpack_does_not_run_verify(self):
        incus = mock.Mock()
        incus.exec.side_effect = lambda _box, cmd, _env, **kw: 2 if cmd[0] == "tar" else 0
        with self.assertRaises(ValueError):
            with verify.in_box(incus, "box", verify.Source("archive", "abc"), {},
                               agent_uid=1000, agent_gid=1000):
                self.fail("verify nie może ruszyć po błędzie tar")
        self.assertEqual(incus.exec.call_args_list[-1].args[1][0], "rm")

    def test_provision_runs_real_verify_as_agent_and_records_source_commit(self):
        builder = bb.Builder(BoxSettings(agent_uid=1001, agent_gid=1002), incus=mock.Mock())
        builder.incus.exec.return_value = 0
        builder._run = mock.Mock()
        builder.stamp = mock.Mock(return_value={"hash": "recipe"})
        full = bp.with_defaults({"tide-box": 1, "verify": ["dotnet build"]})
        source = verify.Source("archive", "abc")
        with mock.patch.object(bb.templates, "build_apt", return_value=[]), mock.patch.object(bb.bs, "record") as record:
            builder._provision("project", "box", full, "http://proxy", [], source)
        call = next(c for c in builder.incus.exec.call_args_list if c.args[1] == ["bash", "-c", "dotnet build"])
        self.assertEqual(call.kwargs, {"user": 1001, "group": 1002, "cwd": "/opt/tide/verify/work"})
        builder.incus.exec.assert_any_call(
            "box", ["install", "-d", "-o", "1001", "-g", "1002", "/opt/tide/verify/work"], mock.ANY)
        tar_call = next(c for c in builder.incus.exec.call_args_list if c.args[1][0] == "tar")
        self.assertEqual(tar_call.kwargs, {"user": 1001, "group": 1002})
        self.assertEqual(call.args[2]["HOME"], "/home/agent")
        event = next(c for c in record.call_args_list if c.args[1] == "box.verify")
        self.assertEqual(event.kwargs["sourceCommit"], "abc")
        self.assertEqual(event.kwargs["result"], "ok")

    def test_incus_exec_passes_explicit_group_and_keeps_default(self):
        run = mock.Mock(return_value=subprocess.CompletedProcess([], 0))
        incus = Incus("tide", run=run)
        for group, expected in ((None, "1001"), (1002, "1002"), (0, "0")):
            with self.subTest(group=group):
                incus.exec("box", ["id"], user=1001, group=group)
                argv = run.call_args.args[0]
                self.assertEqual(argv[argv.index("--user") + 1], "1001")
                self.assertEqual(argv[argv.index("--group") + 1], expected)

    def test_cli_build_forwards_worktree_without_changing_default(self):
        args = cli._parser().parse_args(["build", "project", "--worktree", str(self.worktree)])
        builder = mock.Mock()
        builder.build.return_value = bb.BuildResult("box", "recipe", False)
        with mock.patch.object(bb, "state_dir", return_value="/tmp/state"):
            cli.run_build(builder, args.slug, io.StringIO(), args.worktree)
        builder.build.assert_called_once_with("project", verify_worktree=str(self.worktree))


if __name__ == "__main__":
    unittest.main()
