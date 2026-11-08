"""Inicjalizacja z widoku Git nie może zmienić wspólnej konfiguracji hosta."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from tide_daemon.box import gitview
from tide_daemon.worktrees.git import Git
from .test_host_git_invocation import KernelTransport


class GitInitializationBoundaryTest(unittest.TestCase):
    def setUp(self):
        # Wszystkie cele, również pozorny „obcy host”, należą do tej fixture.
        build = Path(__file__).resolve().parents[2] / "build"
        build.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="pt264-init-", dir=build)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "foreign-project"
        self.repo.mkdir()
        self.home = self.base / "home"
        self.home.mkdir()
        self.env = {"PATH": "/usr/bin:/bin", "HOME": str(self.home), "LC_ALL": "C",
                    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_AUTHOR_NAME": "Tester", "GIT_AUTHOR_EMAIL": "test@example.invalid",
                    "GIT_COMMITTER_NAME": "Tester", "GIT_COMMITTER_EMAIL": "test@example.invalid"}
        self.raw(self.repo, "init", "-q", "-b", "master")
        self.raw(self.repo, "commit", "--allow-empty", "-qm", "base")
        self.wt = self.base / "project-worktrees/card"
        self.raw(self.repo, "worktree", "add", "-qb", "card/test", str(self.wt))
        self.config = self.repo / ".git/config"
        self.before = self.config.read_bytes()

    def raw(self, cwd, *args):
        return subprocess.run(["/usr/bin/git", *map(str, args)], cwd=cwd, env=self.env,
                              capture_output=True, text=True, check=True).stdout

    def assert_initialized_without_foreign_write(self, target, code, error):
        self.assertEqual(self.config.read_bytes(), self.before,
                         "git init zmienił config obcego wspólnego repozytorium")
        self.assertEqual(self.raw(self.repo, "config", "--get", "core.bare").strip(), "false")
        self.assertEqual(code, 0, error)
        self.assertEqual(self.raw(self.base, "--git-dir=" + str(target),
                                  "config", "--get", "core.bare").strip(), "true")

    def test_box_init_bare_from_linked_worktree_preserves_foreign_config(self):
        for command in ("init", "init-db"):
            with self.subTest(command=command):
                target = self.base / (command + ".git")
                result = subprocess.run(["/usr/bin/python3", "-I", gitview.__file__, "-C", str(self.wt),
                                         command, "--bare", str(target)], env=self.env,
                                        capture_output=True, text=True)
                self.assert_initialized_without_foreign_write(target, result.returncode, result.stderr)

    def test_host_private_init_bare_does_not_follow_commondir_to_foreign_config(self):
        view = self.base / "private-view"
        view.mkdir()
        (view / "HEAD").write_text("ref: refs/heads/master\n")
        (view / "commondir").write_text(str(self.repo / ".git") + "\n")
        git = Git(cancel=KernelTransport())
        git.env = dict(self.env)
        git.private = True
        git.read_roots = (str(self.repo),)
        git.write_roots = (str(self.repo), str(self.base))
        for command in (("init", "--bare"), ("-c", "alias.reinit=init-db", "reinit", "--bare"),
                        ("init-db", "--bare"),
                        ("init", "--bare", str(self.base / "new.git")),
                        ("clone", "--bare", str(self.repo), str(self.base / "clone.git"))):
            with self.subTest(command=command):
                result = git.run(str(view), "--git-dir=" + str(view), *command)
                self.assertEqual(self.config.read_bytes(), self.before,
                                 "git init zmienił config obcego wspólnego repozytorium")
                self.assertEqual(result.code, 126, result.err)
                self.assertIn("prywatnej administracji", result.err)
        self.assertFalse((self.base / "new.git").exists())
        self.assertFalse((self.base / "clone.git").exists())

    def test_box_init_with_inherited_common_dir_preserves_foreign_config(self):
        target = self.base / "new.git"
        self.raw(self.base, "init", "-q", "--bare", str(target))
        # Zwykły checkout (.git jest katalogiem) idzie ścieżką fallback wrappera.
        env = {**self.env, "GIT_DIR": str(self.repo / ".git"),
               "GIT_COMMON_DIR": str(self.repo / ".git")}
        result = subprocess.run(["/usr/bin/python3", "-I", gitview.__file__, "-C", str(self.repo),
                                 "init", "--bare", str(target)], env=env,
                                capture_output=True, text=True)
        self.assert_initialized_without_foreign_write(target, result.returncode, result.stderr)

    def test_host_init_with_inherited_common_dir_preserves_foreign_config(self):
        git = Git(cancel=KernelTransport())
        git.env = {**self.env, "GIT_DIR": str(self.repo / ".git"),
                   "GIT_COMMON_DIR": str(self.repo / ".git")}
        target = self.base / "new.git"
        git.write_roots = (str(self.base),)
        result = git.run(str(self.wt), "init", "--bare", str(target))
        self.assert_initialized_without_foreign_write(target, result.code, result.err)

    def test_box_clone_from_linked_worktree_preserves_common_config(self):
        target = self.base / "clone.git"
        result = subprocess.run(["/usr/bin/python3", "-I", gitview.__file__, "-C", str(self.wt),
                                 "clone", "--bare", str(self.repo), str(target)], env=self.env,
                                capture_output=True, text=True)
        self.assert_initialized_without_foreign_write(target, result.returncode, result.stderr)
        self.assertEqual(self.raw(self.base, "--git-dir=" + str(target), "rev-parse", "HEAD"),
                         self.raw(self.repo, "rev-parse", "HEAD"))

    def test_box_view_still_commits_to_its_branch_without_changing_host_index(self):
        admin = Path((self.wt / ".git").read_text().strip()[8:])
        index = (admin / "index").read_bytes()
        (self.wt / "own.txt").write_text("własny zapis")
        for args in (("add", "own.txt"), ("commit", "-qm", "add own data")):
            result = subprocess.run(["/usr/bin/python3", "-I", gitview.__file__,
                                     "-C", str(self.wt), *args], env=self.env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.config.read_bytes(), self.before)
        self.assertEqual((admin / "index").read_bytes(), index)
        self.assertEqual(self.raw(self.repo, "show", "card/test:own.txt"), "własny zapis")

    def test_private_guard_keeps_config_read_ref_validation_listing_and_cas(self):
        from tide_daemon.box import guardgit, refguard
        state = self.base / "guard-state"
        state.mkdir()
        oid = self.raw(self.repo, "rev-parse", "HEAD").strip()
        def factory(**kwargs):
            return Git(cancel=KernelTransport(), **kwargs)
        with mock.patch.object(refguard, "_root", return_value=str(state)), \
                mock.patch.object(guardgit, "Git", side_effect=factory):
            with guardgit.trusted(str(self.repo)) as (git, view):
                for args in (("check-ref-format", "refs/heads/guard-test"),
                             ("for-each-ref", "--format=%(refname)"),
                             ("update-ref", "refs/heads/guard-test", oid, "0" * len(oid))):
                    result = git.run(view, "--git-dir=" + view, *args)
                    self.assertTrue(result.ok, result.err)
        self.assertEqual(self.raw(self.repo, "rev-parse", "refs/heads/guard-test").strip(), oid)
        self.assertEqual(self.config.read_bytes(), self.before)
