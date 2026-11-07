"""Małe regresje semantyki adaptera; pełną granicę sprawdza test_project_git_boundary."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tide_daemon.worktrees.git import Git
from . import gitrepos
from .test_cardflow_gitops import LocalStepGit


class KernelTransport:
    """Test samego wnętrza Landlock, bez zagnieżdżania bwrap w piaskownicy testu."""
    def run(self, argv, *, env, timeout, input_text=None):
        payload = json.loads(argv[-1])
        payload["_namespaced"] = True
        return subprocess.run([*argv[:-1], json.dumps(payload)], env=env,
                              input=input_text, capture_output=True, text=True, timeout=timeout)


def kernel_invocation(argv, **kwargs):
    if len(argv) > 3 and argv[2].endswith("git_sandbox.py"):
        payload = json.loads(argv[-1])
        payload["_namespaced"] = True
        argv = [*argv[:-1], json.dumps(payload)]
    return REAL_RUN(argv, **kwargs)


REAL_RUN = subprocess.run


class HostGitInvocationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pt264-invocation-")
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "project"
        self.repo.mkdir()
        gitrepos.git(str(self.repo), "init", "-q", "-b", "master")
        gitrepos.commit(str(self.repo), "base.txt")

    def git(self):
        git = Git(cancel=KernelTransport())
        git.env = dict(gitrepos.ENV)
        return git

    def test_private_merge_index_does_not_change_the_checkout_index(self):
        folder = Path(self.tmp.name) / "private"
        folder.mkdir()
        shutil.copyfile(self.repo / ".git/index", folder / "index")
        before = gitrepos.git(str(self.repo), "write-tree").strip()
        gitrepos.commit(str(self.repo), "result.txt", "wynik")
        tip = gitrepos.git(str(self.repo), "rev-parse", "HEAD").strip()
        gitrepos.git(str(self.repo), "reset", "--hard", "HEAD~1")
        with mock.patch("daemon.tests.test_cardflow_gitops.Git", side_effect=self.git):
            runner = LocalStepGit(str(self.repo), str(self.repo / ".git"), str(self.repo), "unused")
            made = runner.run("read-tree", tip, index_dir=str(folder))
            self.assertTrue(made.ok, made.err)
            self.assertEqual(gitrepos.git(str(self.repo), "write-tree").strip(), before)
            self.assertNotEqual(runner.run("write-tree", index_dir=str(folder)).out.strip(), before)

    def test_config_file_is_read_even_when_a_git_directory_is_explicit(self):
        gitrepos.git(str(self.repo), "config", "filter.Filtr.clean", "/must-not-run")
        result = self.git().run(str(self.repo), "--git-dir", str(self.repo / ".git"),
                                "config", "--name-only", "--get-regexp", r"^filter\..*\.(clean|smudge|process)$")
        self.assertTrue(result.ok, result.err)
        self.assertIn("filter.Filtr.clean", result.out)

    def test_excludes_outside_project_cannot_supply_repository_configuration(self):
        excludes = Path(self.tmp.name) / "host-excludes"
        excludes.write_text("private.bin\n")
        (self.repo / "private.bin").write_bytes(b"dane")
        gitrepos.git(str(self.repo), "config", "core.excludesFile", str(excludes))
        result = self.git().run(str(self.repo), "status", "--porcelain", "--untracked-files=all")
        self.assertFalse(result.ok)
        self.assertIn(str(excludes), result.err)
        # Ten sam wzorzec z jawnego projektu musi działać bez udostępniania hosta.
        (self.repo / ".git/info/exclude").write_text(excludes.read_text())
        gitrepos.git(str(self.repo), "config", "core.excludesFile", str(self.repo / ".git/info/exclude"))
        result = self.git().run(str(self.repo), "status", "--porcelain", "--untracked-files=all")
        self.assertTrue(result.ok, result.err)
        self.assertNotIn("private.bin", result.out)

    def test_task_worktree_inside_project_can_be_created_and_removed(self):
        folder = self.repo / ".claude/worktrees"
        folder.mkdir(parents=True)
        target = folder / "task"
        for args in (("worktree", "add", "-q", "-b", "task/local", str(target)),
                     ("worktree", "remove", str(target))):
            result = self.git().run(str(self.repo), *args)
            self.assertTrue(result.ok, result.err)
        self.assertFalse(target.exists())

    def test_task_worktree_cannot_be_created_in_another_project(self):
        foreign = Path(self.tmp.name) / "foreign"
        folder = foreign / ".claude/worktrees"
        folder.mkdir(parents=True)
        result = self.git().run(str(self.repo), "worktree", "add", "-q", "-b", "task/foreign", str(folder / "task"))
        self.assertFalse(result.ok)
        self.assertIn("poza jawnym katalogiem projektu", result.err)
        self.assertFalse((folder / "task").exists())

    def test_task_worktree_container_symlink_cannot_redirect_creation(self):
        foreign = Path(self.tmp.name) / "foreign"
        foreign.mkdir()
        (self.repo / ".claude").mkdir()
        folder = self.repo / ".claude/worktrees"
        folder.symlink_to(foreign)
        result = self.git().run(str(self.repo), "worktree", "add", "-q", "-b", "task/symlink", str(folder / "task"))
        self.assertFalse(result.ok)
        self.assertIn("dowiązanie", result.err)
        self.assertFalse((foreign / "task").exists())

    def test_read_only_environment_disables_all_repository_filter_drivers(self):
        from tide_daemon.tasks import agentcmd
        from .test_agentcmd import BackgroundCodexTest
        case = BackgroundCodexTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        with mock.patch.object(agentcmd.subprocess, "run", kernel_invocation):
            case.test_caretaker_git_runs_no_helper_programs_from_the_repository_config()

    def test_global_identity_is_copied_without_includes_and_local_identity_wins(self):
        config = Path(self.tmp.name) / "hostconfig"
        included = Path(self.tmp.name) / "included"
        included.write_text('[user]\nname = Wrong\n')
        config.write_text(f'[user]\nname = Global Tester\nemail = global@example.invalid\n'
                          f'[include]\npath = {included}\n[core]\nfsmonitor = /must-not-run\n')
        git = self.git()
        git.env = {"GIT_CONFIG_GLOBAL": str(config)}
        with mock.patch.object(subprocess, "run", kernel_invocation):
            result = git.run(str(self.repo), "var", "GIT_AUTHOR_IDENT")
            self.assertTrue(result.ok, result.err)
            self.assertTrue(result.out.startswith("Global Tester <global@example.invalid>"), result.out)
            gitrepos.git(str(self.repo), "config", "user.name", "Local Tester")
            result = git.run(str(self.repo), "var", "GIT_AUTHOR_IDENT")
            self.assertTrue(result.out.startswith("Local Tester <global@example.invalid>"), result.out)
            gitrepos.git(str(self.repo), "config", "--unset", "user.name")
            git.env["GIT_AUTHOR_DATE"] = "@1790000000 +0000"
            result = git.run(str(self.repo), "var", "GIT_AUTHOR_IDENT")
            self.assertTrue(result.out.startswith("Global Tester <global@example.invalid> 1790000000"), result.out)
            git.env["GIT_AUTHOR_NAME"] = "Explicit Tester"
            result = git.run(str(self.repo), "var", "GIT_AUTHOR_IDENT")
            self.assertTrue(result.out.startswith("Explicit Tester <global@example.invalid>"), result.out)

    def test_namespace_keeps_runtime_empty_and_mounts_legacy_worktree_container(self):
        from tide_daemon import git_sandbox as sandbox
        folder = Path(str(self.repo) + "-wt")
        folder.mkdir()
        captured = []
        def launch(argv, **kwargs):
            captured.append(argv)
            status_dir = argv[argv.index("/run/tide-git-status") - 1]
            Path(status_dir, "code").write_text("0\n")
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(sandbox.subprocess, "run", launch):
            self.assertEqual(sandbox.execute({"args": ["-C", str(self.repo), "worktree", "list"]}), 0)
        argv = captured[0]
        self.assertIn(str(folder), argv)
        self.assertEqual(argv[argv.index("/run") - 1], "--tmpfs")
        self.assertNotIn("/run/user", argv)

    def test_missing_namespace_confirmation_is_not_a_git_no_match(self):
        from tide_daemon import git_sandbox as sandbox
        with mock.patch.object(sandbox.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(sandbox.BoundaryError, "transport"):
                sandbox.execute({"args": ["--git-dir", str(self.repo / ".git"), "config", "--get", "absent.key"]})

    def test_external_registered_worktree_exposes_only_its_verified_pointer_when_listing(self):
        from tide_daemon import git_sandbox as sandbox
        target = Path(self.tmp.name) / "synthetic-home/codex/task"
        gitrepos.git(str(self.repo), "worktree", "add", "-q", "-b", "task/external", str(target))
        captured = []
        def launch(argv, **kwargs):
            captured.append(argv)
            status_dir = argv[argv.index("/run/tide-git-status") - 1]
            Path(status_dir, "code").write_text("0\n")
            return subprocess.CompletedProcess(argv, 0)
        with mock.patch.object(sandbox.subprocess, "run", launch):
            self.assertEqual(sandbox.execute({"args": ["-C", str(self.repo), "worktree", "list"]}), 0)
        argv = captured[0]
        at = argv.index(str(target / ".git"))
        self.assertEqual(argv[at - 1], "--ro-bind")
        self.assertNotIn(str(target), argv)
        self.assertNotIn(str(target.parent), argv)

    def test_confirmed_git_no_match_preserves_exit_code_and_uses_target_cwd(self):
        from tide_daemon import git_sandbox as sandbox
        def launch(argv, **kwargs):
            self.assertEqual(argv[argv.index("--chdir") + 1], str(self.repo))
            status_dir = argv[argv.index("/run/tide-git-status") - 1]
            Path(status_dir, "code").write_text("1\n")
            payload = json.loads(argv[-1])
            self.assertNotIn("/run/tide-git-status", payload.get("writeRoots", []))
            return subprocess.CompletedProcess(argv, 1)
        with mock.patch.object(sandbox.subprocess, "run", launch):
            self.assertEqual(sandbox.execute({"args": ["--git-dir", str(self.repo / ".git"),
                                                       "config", "--get", "absent.key"]}), 1)
