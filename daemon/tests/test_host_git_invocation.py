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


def boundary_on(case):
    """Scenariusze granicy: usługa z `box.enabled` (bez pliku ustawień w izolowanym stanie testu)."""
    patcher = mock.patch("tide_daemon.gitcommand.box_enabled", return_value=True)
    patcher.start()
    case.addCleanup(patcher.stop)


class ConditionalBoundaryTest(unittest.TestCase):
    """Wyłączone pudełko = argv i otoczenie sprzed PT-264; granica tylko dla danych pudełka."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pt264-conditional-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "project"
        self.foreign = self.base / "foreign"
        for repo in (self.repo, self.foreign):
            repo.mkdir()
            gitrepos.git(str(repo), "init", "-q", "-b", "master")
            gitrepos.commit(str(repo), "data.txt", str(repo))
        self.config = self.base / "config"
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.config)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def settings(self, enabled):
        (self.config / "tide").mkdir(parents=True, exist_ok=True)
        (self.config / "tide/cardflow.json").write_text(json.dumps({"box": {"enabled": enabled}}))

    def test_disabled_box_runs_plain_git_with_the_callers_environment(self):
        from tide_daemon.gitcommand import HOST_OPTIONS, git_argv, git_command
        for enabled in (None, False):
            if enabled is not None:
                self.settings(enabled)
            args = ("-C", str(self.repo), "status", "--porcelain")
            self.assertEqual(git_argv(*args), ["git", *HOST_OPTIONS, *args])
            caller_env = {"PATH": "/x", "GIT_OPTIONAL_LOCKS": "0"}
            self.assertEqual(git_command(*args, env=caller_env), (["git", *HOST_OPTIONS, *args], caller_env))
            self.assertEqual(git_command(*args), (["git", *HOST_OPTIONS, *args], None))

    def test_enabled_box_keeps_the_full_boundary(self):
        from tide_daemon.gitcommand import git_argv, git_command
        self.settings(True)
        argv = git_argv("-C", str(self.repo), "status")
        self.assertEqual(argv[:2], ["/usr/bin/python3", "-I"])
        self.assertTrue(argv[2].endswith("git_sandbox.py"))
        argv, env = git_command("-C", str(self.repo), "status", env={"PATH": "/x", "LD_PRELOAD": "/evil"})
        self.assertTrue(argv[2].endswith("git_sandbox.py"))
        self.assertNotIn("LD_PRELOAD", env)

    def test_box_path_and_exposed_repository_keep_the_boundary_while_box_is_disabled(self):
        from tide_daemon.gitcommand import git_argv
        self.settings(False)
        self.assertTrue(git_argv("-C", str(self.repo), "status", boundary=True)[2].endswith("git_sandbox.py"))
        self.assertTrue(git_argv("--git-dir", str(self.repo / ".git"), "config", "-l", private=True)[2]
                        .endswith("git_sandbox.py"))
        gitrepos.git(str(self.repo), "worktree", "add", "-q", "-b", "wt", str(self.base / "project-worktrees/wt"))
        (self.repo / ".git/tide-boundary").write_text("project-git:2\n")
        for args in (("-C", str(self.repo / "sub")), ("-C", str(self.base / "project-worktrees/wt")),
                     ("--git-dir", str(self.repo / ".git/worktrees/wt")),
                     ("config", "--file", str(self.repo / ".git/config"))):
            self.assertTrue(git_argv(*args, "status")[2].endswith("git_sandbox.py"), args)
        self.assertEqual(git_argv("-C", str(self.foreign), "status")[0], "git")

    def test_unreadable_repository_pointer_chooses_the_boundary(self):
        from tide_daemon.gitcommand import git_argv
        broken = self.base / "broken"
        broken.mkdir()
        (broken / ".git").write_text("not a pointer\n")
        self.assertTrue(git_argv("-C", str(broken), "status")[2].endswith("git_sandbox.py"))

    def test_alternates_in_exposed_repository_cannot_import_foreign_objects_with_box_disabled(self):
        from tide_daemon.cardflow.gitops import FlowGit
        self.settings(False)
        oid = gitrepos.git(str(self.foreign), "rev-parse", "HEAD").strip()
        git = Git()
        git.env = dict(gitrepos.ENV)
        (self.repo / ".git/tide-boundary").write_text("project-git:2\n")
        (self.repo / ".git/objects/info/alternates").write_text(str(self.foreign / ".git/objects") + "\n")
        self.assertFalse(FlowGit(git).sha(str(self.repo), oid))
        self.assertIn("alternates", git.run(str(self.repo), "cat-file", "-p", oid).err)

    def filtered_repository(self, path, outside):
        """Jednorazowe repo z filtrem clean, który przy `status` zostawia ślad poza projektem."""
        path.mkdir(parents=True)
        gitrepos.git(str(path), "init", "-q", "-b", "master")
        (path / ".gitattributes").write_text("data.txt filter=x\n")
        (path / "data.txt").write_text("aaaa\n")
        gitrepos.git(str(path), "add", ".")
        gitrepos.git(str(path), "commit", "-q", "-m", "base")
        gitrepos.git(str(path), "config", "filter.x.clean", f"touch {outside}; cat")
        (path / "data.txt").write_text("bbbb\n")  # ten sam rozmiar: status musi przeliczyć treść filtrem
        info = os.stat(path / "data.txt")
        os.utime(path / "data.txt", ns=(info.st_atime_ns, info.st_mtime_ns + 5_000_000_000))
        return path

    def exposed_project(self):
        self.settings(False)
        (self.repo / ".git/tide-boundary").write_text("project-git:2\n")
        gitrepos.git(str(self.repo), "worktree", "add", "-q", "-b", "wt", str(self.base / "project-worktrees/wt"))
        trap = self.base / "outside"
        trap.mkdir()
        return trap / "pwned"

    def test_new_unmarked_repository_in_exposed_worktrees_keeps_the_boundary(self):
        from tide_daemon.gitcommand import HOST_OPTIONS, git_command
        from tide_daemon.worktrees.scanner import WorktreeScanner, find_repositories
        pwned = self.exposed_project()
        evil = self.filtered_repository(self.base / "project-worktrees/evil", pwned)
        control = self.filtered_repository(self.base / "control", pwned)
        # Kontrola dodatnia: host potwierdza, że ten sam układ bez granicy wykonuje filtr.
        subprocess.run(["git", *HOST_OPTIONS, "-C", str(control), "status", "--porcelain"],
                       env=gitrepos.ENV, check=True, capture_output=True)
        self.assertTrue(pwned.exists())
        pwned.unlink()
        self.assertTrue((self.repo / ".git/tide-boundary").exists())  # oryginalny znacznik zostaje
        self.assertFalse((evil / ".git/tide-boundary").exists())
        git = Git(cancel=KernelTransport())
        git.env = dict(gitrepos.ENV)
        for path in (evil, evil / "sub", self.base / "project-worktrees", self.repo / ".git/objects"):
            argv, _ = git_command("-C", str(path), "status")
            self.assertTrue(argv[2].endswith("git_sandbox.py"), path)
        result = git.run(str(evil), "status", "--porcelain=v1", "-z", "--untracked-files=normal")
        self.assertFalse(result.ok)
        self.assertFalse(pwned.exists())
        # Automatyczny odczyt hostowego skanera katalogu projektów.
        self.assertIn(str(evil), find_repositories(str(self.base)))
        scanner = WorktreeScanner(str(self.base), git=git)
        git.cancel = KernelTransport()
        self.assertEqual(scanner.scan_repo(str(evil)), [])
        self.assertFalse(pwned.exists())

    def test_replaced_worktree_pointer_to_unmarked_administration_keeps_the_boundary(self):
        pwned = self.exposed_project()
        rogue = self.filtered_repository(self.base / "rogue", pwned)
        wt = self.base / "project-worktrees/wt"
        (wt / ".git").write_text(f"gitdir: {rogue / '.git'}\n")
        (wt / ".gitattributes").write_text("data.txt filter=x\n")
        (wt / "data.txt").write_text("bbbb\n")
        git = Git(cancel=KernelTransport())
        git.env = dict(gitrepos.ENV)
        from tide_daemon.gitcommand import git_argv
        self.assertTrue(git_argv("-C", str(wt), "status")[2].endswith("git_sandbox.py"))
        result = git.run(str(wt), "status", "--porcelain=v1", "-z", "--untracked-files=normal")
        self.assertFalse(result.ok)
        self.assertFalse(pwned.exists())
        # Ta sama administracja poza korzeniami pudełka pozostaje zwykłym Gitem hosta.
        self.assertEqual(git_argv("-C", str(rogue), "status")[0], "git")

    def test_disabled_box_checkout_snapshot_matches_plain_git(self):
        (self.repo / "new.txt").write_text("n\n")
        git = Git()
        git.env = dict(gitrepos.ENV)
        with mock.patch("subprocess.run", wraps=subprocess.run) as run:
            snapshot = git.read_checkout(str(self.repo), include_ignored=True)
        self.assertTrue(all(call.args[0][0] == "git" for call in run.call_args_list))
        self.assertEqual(snapshot["head"].out.strip(), gitrepos.git(str(self.repo), "rev-parse", "HEAD").strip())
        self.assertIn("?? new.txt", snapshot["status"].out)
        self.assertEqual(snapshot["symbolic"].out.strip(), "refs/heads/master")
        self.assertEqual(set(snapshot), {"head", "symbolic", "status", "index", "gitdir", "index_entries",
                                         "ignored", "repository"})


class HostGitInvocationTest(unittest.TestCase):
    def setUp(self):
        boundary_on(self)
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

    def test_checkout_snapshot_reads_share_one_boundary_without_losing_paths_or_state(self):
        from tide_daemon.cardflow.gitops import FlowGit
        from tide_daemon.cardflow.mergepreview import checkout_snapshot
        (self.repo / "base.txt").write_text("zmiana")
        name = "plik\nz nową linią.txt"
        (self.repo / name).write_text("nieśledzony")
        calls = []
        original = KernelTransport.run
        def measured(transport, *args, **kwargs):
            calls.append(args[0])
            return original(transport, *args, **kwargs)
        with mock.patch.object(KernelTransport, "run", measured):
            snapshot = checkout_snapshot(FlowGit(self.git()).run, str(self.repo))
        self.assertEqual(snapshot["changes"]["untracked"][0]["paths"], [name])
        self.assertEqual(snapshot["changes"]["unstaged"][0]["paths"], ["base.txt"])
        self.assertTrue(snapshot["indexEntries"])
        self.assertEqual(len(calls), 1)
        # Adapter bez grupowania daje identyczny odcisk.
        sequential = FlowGit(self.git())
        self.assertEqual(snapshot, checkout_snapshot(lambda *args: sequential.run(*args), str(self.repo)))

    def test_local_snapshot_groups_ignored_and_repository_reads_but_rechecks_each_snapshot(self):
        from tide_daemon.cardflow.gitops import FlowGit
        from tide_daemon.cardflow.basechange import local_snapshot
        (self.repo / ".git/info/exclude").write_text("ignored.bin\n")
        (self.repo / "ignored.bin").write_text("dane")
        calls = []
        original = KernelTransport.run
        def measured(transport, *args, **kwargs):
            calls.append(args[0])
            return original(transport, *args, **kwargs)
        flow = FlowGit(self.git())
        with mock.patch.object(KernelTransport, "run", measured):
            first = local_snapshot(flow, str(self.repo), str(self.repo))
            (self.repo / "base.txt").write_text("późniejsza zmiana")
            second = local_snapshot(flow, str(self.repo), str(self.repo))
        self.assertEqual(first["ignored"], ["ignored.bin"])
        self.assertFalse(first["changes"]["unstaged"])
        self.assertTrue(second["changes"]["unstaged"])
        self.assertEqual(len(calls), 4)  # Dwie świeże migawki i dwa sprawdzenia repo źródłowego.

    def test_checkout_batch_detached_head_and_protocol_failures_are_explicit(self):
        gitrepos.git(str(self.repo), "checkout", "--detach", "-q")
        snapshot = self.git().read_checkout(str(self.repo))
        self.assertEqual(snapshot["symbolic"].code, 1)
        self.assertEqual(snapshot["symbolic"].out, "")
        for output in ("{}", "not json", '{"head": {"code": 0, "out": "", "err": ""}}'):
            with self.subTest(output=output):
                git = self.git()
                with mock.patch.object(git.cancel, "run", return_value=subprocess.CompletedProcess([], 0, output, "")):
                    with self.assertRaisesRegex(OSError, "niepoprawny wynik"):
                        git.read_checkout(str(self.repo))

    def test_checkout_batch_does_not_accept_arbitrary_commands(self):
        result = self.git().run(str(self.repo), "config", "--file", str(self.repo / ".git/config"),
                                "core.worktree", "/foreign", checkout="all")
        self.assertFalse(result.ok)
        self.assertIn("niepoprawne żądanie", result.err)
        self.assertNotIn("worktree", (self.repo / ".git/config").read_text())

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
