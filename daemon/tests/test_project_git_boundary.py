"""Granica hostowego Git: własne repozytoria, bez administracji użytkownika."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from . import gitrepos
from tide_daemon.cardflow.gitops import FlowGit
from tide_daemon.worktrees.git import Git


class ProjectGitBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="pt264-git-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "project"
        self.foreign = self.base / "foreign"
        for repo in (self.repo, self.foreign):
            repo.mkdir()
            gitrepos.git(str(repo), "init", "-q", "-b", "master")
            gitrepos.commit(str(repo), "data.txt", str(repo))
        self.git = Git()
        self.git.env = dict(gitrepos.ENV)
        self.flow = FlowGit(self.git)
        self.oid = gitrepos.git(str(self.foreign), "rev-parse", "HEAD").strip()

    def test_alternates_cannot_import_foreign_objects_into_host_flow(self):
        self.assertEqual(gitrepos.git(str(self.foreign), "cat-file", "-t", self.oid).strip(), "commit")
        self.assertFalse(self.flow.sha(str(self.repo), self.oid))
        (self.repo / ".git/objects/info/alternates").write_text(str(self.foreign / ".git/objects") + "\n")
        self.assertFalse(self.flow.sha(str(self.repo), self.oid))
        denied = self.git.run(str(self.repo), "cat-file", "-p", self.oid)
        self.assertFalse(denied.ok)
        self.assertIn("alternates", denied.err)
        with self.assertRaisesRegex(OSError, "alternates"):
            self.git.read_checkout(str(self.repo))

    def test_worktree_filter_names_are_visible_to_read_only_agent_configuration(self):
        from tide_daemon.tasks.agentcmd import read_only_git_config
        container = Path(str(self.repo) + "-worktrees")
        container.mkdir()
        wt = container / "card"
        gitrepos.git(str(self.repo), "worktree", "add", "-qb", "card/config", str(wt))
        gitrepos.git(str(self.repo), "config", "filter.Filtr.clean", "/must-not-run")
        admin = str(self.repo / ".git/worktrees/card")
        result = self.git.run(str(wt), "config", "--name-only", "--get-regexp", r"^filter\.")
        self.assertTrue(result.ok, result.err)
        self.assertIn("filter.Filtr.clean", result.out)
        self.assertIn(("filter.Filtr.clean", ""), read_only_git_config(admin))

    def test_external_registered_worktree_is_not_reported_as_missing(self):
        wt = self.base / "synthetic-home/codex/task"
        gitrepos.git(str(self.repo), "worktree", "add", "-q", "-b", "card/external", str(wt))
        result = self.git.run(str(self.repo), "worktree", "list", "--porcelain")
        self.assertTrue(result.ok, result.err)
        block = next(b for b in result.out.split("\n\n") if "worktree " + str(wt) + "\n" in b)
        self.assertNotIn("prunable", block)

    def test_symlink_to_foreign_ref_cannot_make_host_read_foreign_git(self):
        target = self.foreign / ".git/refs/heads/master"
        self.assertTrue(target.is_file())
        (self.repo / ".git/refs/heads/foreign").symlink_to(target)
        self.assertFalse(self.flow.sha(str(self.repo), "refs/heads/foreign"))

    def test_checkout_batch_cannot_follow_foreign_head_ref(self):
        target = self.foreign / ".git/refs/heads/master"
        original = target.read_bytes()
        (self.repo / ".git/refs/heads/foreign").symlink_to(target)
        gitrepos.git(str(self.repo), "symbolic-ref", "HEAD", "refs/heads/foreign")
        with self.assertRaises(OSError):
            self.git.read_checkout(str(self.repo), include_ignored=True)
        self.assertEqual(target.read_bytes(), original)

    def test_project_config_cannot_execute_program_or_redirect_a_write(self):
        marker = self.base / "must-not-exist"
        hook = self.repo / "hook"
        hook.write_text("#!/bin/sh\necho bad > " + str(marker) + "\n")
        hook.chmod(0o755)
        gitrepos.git(str(self.repo), "config", "core.fsmonitor", str(hook))
        gitrepos.git(str(self.repo), "config", "core.worktree", str(self.foreign))
        original = (self.foreign / "data.txt").read_bytes()
        run = self.git.run(str(self.repo), "status", "--porcelain")
        self.assertTrue(run.ok, run.err)
        self.assertEqual(self.git.run(str(self.repo), "rev-parse", "--show-toplevel").out.strip(), str(self.repo))
        self.assertTrue(self.git.read_checkout(str(self.repo))["status"].ok)
        self.assertFalse(marker.exists())
        self.assertEqual((self.foreign / "data.txt").read_bytes(), original)

    def test_external_diff_and_filters_cannot_execute_even_with_explicit_git_options(self):
        marker = self.base / "must-not-exist"
        (self.repo / ".gitattributes").write_text("*.txt filter=evil\n")
        gitrepos.git(str(self.repo), "config", "filter.evil.clean", "touch " + str(marker))
        (self.repo / "data.txt").write_text("zmienione")
        result = self.git.run(str(self.repo), "add", "data.txt")
        self.assertFalse(marker.exists())
        # Git może zignorować niewymagany filtr; brak EXECUTE obowiązuje niezależnie od kodu wyjścia.
        self.assertNotIn("must-not-exist", self.git.run(str(self.repo), "status", "--porcelain").out)

    def test_project_write_and_other_card_show_work_without_cross_project_rights(self):
        wt = Path(str(self.repo) + "-worktrees") / "second"
        wt.parent.mkdir()
        gitrepos.git(str(self.repo), "worktree", "add", "-q", "-b", "card/second", str(wt))
        gitrepos.commit(str(wt), "second.txt", "inna karta")
        show = self.git.run(str(self.repo), "show", "card/second:second.txt")
        self.assertTrue(show.ok, show.err)
        self.assertEqual(show.out, "inna karta")
        (self.repo / "own.txt").write_text("własny zapis")
        for args in (("add", "own.txt"), ("commit", "-m", "add own data")):
            run = self.git.run(str(self.repo), *args)
            self.assertTrue(run.ok, run.err)
        self.assertEqual(self.git.run(str(self.repo), "show", "HEAD:own.txt").out, "własny zapis")

    def test_inherited_git_and_loader_environment_do_not_widen_boundary(self):
        self.git.env.update(GIT_DIR=str(self.foreign / ".git"),
                            GIT_ALTERNATE_OBJECT_DIRECTORIES=str(self.foreign / ".git/objects"))
        self.assertFalse(self.flow.sha(str(self.repo), self.oid))
        self.assertEqual(self.git.run(str(self.repo), "rev-parse", "--show-toplevel").out.strip(), str(self.repo))
        self.git.env["LD_PRELOAD"] = "/nonexistent/must-not-be-loaded.so"
        run = self.git.run(str(self.repo), "rev-parse", "HEAD")
        self.assertTrue(run.ok, run.err)
        self.assertNotIn("must-not-be-loaded", run.err)

    def test_kernel_refuses_a_ref_symlink_inserted_after_validation(self):
        from tide_daemon import git_sandbox as sandbox
        target = self.foreign / ".git/refs/heads/master"
        before = target.read_bytes()
        original = sandbox.Ruleset.restrict
        def changed(rules):
            (self.repo / ".git/refs/heads/raced").symlink_to(target)
            original(rules)
        with mock.patch.object(sandbox.Ruleset, "restrict", changed):
            code = sandbox.execute({"args": ["-C", str(self.repo), "rev-parse", "--verify", "refs/heads/raced"],
                                    "_namespaced": True})
        self.assertNotEqual(code, 0)
        self.assertEqual(target.read_bytes(), before)
