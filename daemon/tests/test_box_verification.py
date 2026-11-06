"""PT-186: verify rzeczywistej rewizji, publiczny restore i usunięcie źródeł przed clean."""
from contextlib import contextmanager
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

from tide_daemon.box import builder as bb, profile as bp, verification as v
from tide_daemon.box.settings import BoxSettings


def archive(path, name="App.csproj", kind=tarfile.REGTYPE):
    with tarfile.open(path, "w") as output:
        entry = tarfile.TarInfo(name)
        entry.type = kind
        entry.linkname = "/home/agent/.ssh"
        payload = b"<Project />" if kind == tarfile.REGTYPE else b""
        entry.size = len(payload)
        output.addfile(entry, io.BytesIO(payload))


class SourceTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.incus = mock.Mock()
        self.incus.exec.return_value = 0
        self.env = {"HTTPS_PROXY": "http://proxy:1234"}
        patcher = mock.patch.object(v, "project_repo", return_value="/registered/project")
        patcher.start()
        self.addCleanup(patcher.stop)

    def source_archive(self, repo, destination):
        self.assertEqual(repo, "/registered/project")
        archive(destination)
        return "a" * 40

    def test_only_a_valid_committed_archive_is_pushed_and_commands_run_as_agent(self):
        pushed = []

        def push(path, box, target):
            pushed.append((target, Path(path).read_bytes()))

        self.incus.file_push.side_effect = push
        with mock.patch.object(v, "archive_head", side_effect=self.source_archive):
            with v.project_source(self.incus, "proj", "box", {"stacks": ["dotnet"]}, self.env) as source:
                self.assertEqual(source.revision, "a" * 40)
                self.assertEqual(source.restore(), 0)
                source.run(["dotnet", "build", "--no-restore"])
                self.assertNotIn("rm", [c.args[1][0] for c in self.incus.exec.call_args_list])
        restore = next(c for c in self.incus.exec.call_args_list if c.args[1][:2] == ["dotnet", "restore"])
        self.assertEqual(restore.args[1], ["dotnet", "restore", "--configfile", source.config,
                                          "--source", v.PUBLIC_NUGET])
        self.assertEqual(restore.kwargs, {"user": 1000, "cwd": source.cwd})
        self.assertEqual(restore.args[2]["HOME"], "/home/agent")
        self.assertEqual(restore.args[2]["HTTPS_PROXY"], self.env["HTTPS_PROXY"])
        config = next(content for target, content in pushed if target.endswith("/nuget.config"))
        self.assertIn(b"<clear />", config)
        self.assertEqual(config.count(b"<add "), 1)
        self.assertNotIn(b"devhost", config)
        self.assertEqual([c.args[1][0] for c in self.incus.exec.call_args_list][-2:], ["rm", "rm"])

    def test_sources_are_removed_even_when_verify_raises(self):
        with mock.patch.object(v, "archive_head", side_effect=self.source_archive):
            with self.assertRaisesRegex(RuntimeError, "verify failed"):
                with v.project_source(self.incus, "proj", "box", {"stacks": ["dotnet"]}, self.env):
                    raise RuntimeError("verify failed")
        self.assertEqual([c.args[1][0] for c in self.incus.exec.call_args_list][-2:], ["rm", "rm"])

    def test_unsafe_archives_are_rejected_before_any_container_operation(self):
        for name, kind in (("../escape", tarfile.REGTYPE), ("/escape", tarfile.REGTYPE),
                           (".git/config", tarfile.REGTYPE), ("source/link", tarfile.SYMTYPE),
                           ("source/link", tarfile.LNKTYPE), ("device", tarfile.CHRTYPE)):
            with self.subTest(name=name, kind=kind):
                def unsafe(repo, path):
                    archive(path, name, kind)
                    return "b" * 40
                with mock.patch.object(v, "archive_head", side_effect=unsafe):
                    with self.assertRaises(v.VerificationError):
                        with v.project_source(self.incus, "proj", "box", {"stacks": ["dotnet"]}, self.env):
                            self.fail("unsafe source accepted")
                self.incus.assert_not_called()
                self.assertEqual(self.incus.method_calls, [])

    def test_preparation_failure_still_cleans_up_and_cleanup_failure_is_not_hidden(self):
        self.incus.exec.side_effect = [0, 1, 0, 0]  # mkdir, chmod, dwa sprzątania
        with mock.patch.object(v, "archive_head", side_effect=self.source_archive):
            with self.assertRaises(v.VerificationError):
                with v.project_source(self.incus, "proj", "box", {"stacks": ["dotnet"]}, self.env):
                    self.fail("preparation failed")
        self.assertEqual([c.args[1][0] for c in self.incus.exec.call_args_list][-2:], ["rm", "rm"])
        self.incus.reset_mock()
        self.incus.exec.side_effect = [0, 0, 0, 1, 0]  # odmowa usunięcia źródeł, usunięcie archiwum
        with mock.patch.object(v, "archive_head", side_effect=self.source_archive):
            with self.assertRaises(v.VerificationError):
                with v.project_source(self.incus, "proj", "box", {"stacks": ["dotnet"]}, self.env):
                    pass
        self.assertEqual([c.args[1][0] for c in self.incus.exec.call_args_list][-2:], ["rm", "rm"])

    def test_profiles_without_project_or_dotnet_preserve_tool_verification(self):
        with v.project_source(self.incus, "proj", "box", {"stacks": ["node"]}, self.env) as source:
            self.assertIsNone(source)
        with mock.patch.object(v, "project_repo", return_value=""):
            with v.project_source(self.incus, "proj", "box", {"stacks": ["dotnet"]}, self.env) as source:
                self.assertIsNone(source)
        self.assertEqual(self.incus.method_calls, [])

    def test_corrupt_archive_is_a_readable_verification_error(self):
        path = self.folder / "corrupt.tar"
        path.write_bytes(b"not a tar archive")
        with self.assertRaisesRegex(v.VerificationError, "nieczytelne archiwum"):
            v.validate_archive(path)

    def test_archive_uses_resolved_commit_and_does_not_copy_working_tree(self):
        with mock.patch.object(v.subprocess, "run") as run:
            run.side_effect = [mock.Mock(stdout="c" * 40 + "\n"), mock.Mock()]
            self.assertEqual(v.archive_head("/repo", self.folder / "source.tar"), "c" * 40)
        first, second = run.call_args_list
        self.assertIn("HEAD^{commit}", first.args[0])
        self.assertEqual(second.args[0][-1], "c" * 40)
        self.assertIn("archive", second.args[0])
        self.assertIn("core.hooksPath=/dev/null", second.args[0])


class BuildVerifyTest(unittest.TestCase):
    def test_source_cleanup_failure_prevents_the_clean_snapshot(self):
        source = mock.Mock(revision="d" * 40)
        source.restore.return_value = 0
        source.run.return_value = 0

        @contextmanager
        def prepared(*args):
            yield source
            raise v.VerificationError("sprzątanie źródeł nieudane")

        incus = mock.Mock()
        incus.exec.return_value = 0
        builder = bb.Builder(BoxSettings(), incus=incus, policy=mock.Mock())
        full = bp.with_defaults({"tide-box": 1, "verify": ["dotnet build --no-restore"]})
        with mock.patch.object(v, "project_source", side_effect=prepared):
            with self.assertRaises(v.VerificationError):
                builder._provision("proj", "box", full, "http://proxy:1234", [])
        incus.snapshot_create.assert_not_called()

    def test_restore_failure_skips_build_and_records_revision_before_snapshot(self):
        self.check_build(restore_code=1)

    def test_success_runs_profile_build_and_records_revision_before_snapshot(self):
        self.check_build(restore_code=0)

    def check_build(self, restore_code):
        incus = mock.Mock()
        incus.exec.return_value = 0
        source = mock.Mock(revision="d" * 40)
        source.restore.return_value = restore_code
        source.run.return_value = 0
        order = []

        @contextmanager
        def prepared(*args):
            yield source
            order.append("source-cleaned")

        incus.snapshot_create.side_effect = lambda *args: order.append("snapshot")
        builder = bb.Builder(BoxSettings(), incus=incus, policy=mock.Mock())
        full = bp.with_defaults({"tide-box": 1, "stacks": ["dotnet"], "dotnet": {"sdk": ["8.0.415"]},
                                 "verify": ["dotnet build --no-restore"]})
        with mock.patch.object(v, "project_source", side_effect=prepared), mock.patch.object(bb.bs, "record") as record:
            result = builder._provision("proj", "box", full, "http://proxy:1234", [])
        self.assertEqual(order, ["source-cleaned", "snapshot"])
        self.assertEqual(result.warning, bool(restore_code))
        event = next(c for c in record.call_args_list if c.args[1] == "box.verify")
        self.assertEqual(event.kwargs["source_revision"], "d" * 40)
        if restore_code:
            source.run.assert_not_called()
            self.assertIn("dotnet restore (wyłącznie nuget.org)", result.failed_verify)
        else:
            source.run.assert_called_once_with(["bash", "-c", "dotnet build --no-restore"])
            self.assertEqual(result.failed_verify, [])


if __name__ == "__main__":
    unittest.main()
