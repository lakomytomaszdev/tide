"""Worktrees: ahead/behind względem upstreamu i lokalnej głównej, zdalne i czas ostatniego fetch (PT-352).

Prawdziwe, tymczasowe repozytoria z gołym „zdalnym” w katalogu — bez sieci. Sprawdzamy to, co widać przez
snapshot (czyli List() w D-Bus): pola addytywne, repozytorium bez zdalnego, gałąź główna main/develop
i brak poleceń sieciowych w skanie.
"""
import os
import shutil
import tempfile
import unittest

from . import gitrepos
from tide_daemon.worktrees import WorktreeScanner
from tide_daemon.worktrees.git import Git

git, commit = gitrepos.git, gitrepos.commit


class RecordingGit(Git):
    def __init__(self):
        super().__init__()
        self.calls = []

    def run(self, cwd, *args, **kwargs):
        self.calls.append(args)
        return super().run(cwd, *args, **kwargs)


@unittest.skipUnless(shutil.which("git"), "brak gita")
class DivergenceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-wtdiv-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root = os.path.join(self.tmp, "work")
        os.makedirs(self.root)

    def repo(self, name, main="master"):
        path = os.path.join(self.root, name)
        os.makedirs(path)
        git(path, "init", "-q", "-b", main)
        commit(path, "README.md", name + "\n")
        return path

    def add_remote(self, path, name="origin"):
        bare = os.path.join(self.tmp, f"{os.path.basename(path)}-{name}.git")
        git(self.tmp, "init", "-q", "--bare", bare)
        git(path, "remote", "add", name, bare)
        return bare

    def worktree(self, repo, branch):
        wt = os.path.join(self.tmp, "wt", os.path.basename(repo), branch.replace("/", "-"))
        git(repo, "worktree", "add", "-q", "-b", branch, wt)
        return wt

    def listed(self, git_impl=None):
        scanner = WorktreeScanner(self.root, git=git_impl or Git())
        scanner.refresh()
        return {w["path"]: w for w in scanner.snapshot([])}


class UpstreamTest(DivergenceTestCase):
    def setUp(self):
        super().setUp()
        self.proj = self.repo("proj")
        self.bare = self.add_remote(self.proj)
        git(self.proj, "push", "-q", "-u", "origin", "master")
        self.wt = self.worktree(self.proj, "feat/sync")
        commit(self.wt, "c1.txt")
        git(self.wt, "push", "-q", "-u", "origin", "feat/sync")
        other = os.path.join(self.tmp, "other")
        git(self.tmp, "clone", "-q", "-b", "feat/sync", self.bare, other)
        commit(other, "remote.txt")
        git(other, "push", "-q", "origin", "feat/sync")
        git(self.proj, "fetch", "-q", "origin")
        commit(self.wt, "l1.txt")
        commit(self.wt, "l2.txt")

    def test_ahead_and_behind_against_upstream(self):
        item = self.listed()[self.wt]
        self.assertEqual((item["upstream"], item["upstreamAhead"], item["upstreamBehind"]),
                         ("origin/feat/sync", 2, 1))

    def test_main_ahead_and_behind_against_local_main(self):
        commit(self.proj, "m1.txt")
        item = self.listed()[self.wt]
        self.assertEqual((item["mainAhead"], item["mainBehind"]), (3, 1))
        self.assertEqual(item["unmerged"], 3)

    def test_branch_without_upstream_has_empty_upstream_and_no_numbers(self):
        local = self.worktree(self.proj, "feat/local")
        item = self.listed()[local]
        self.assertEqual((item["upstream"], item["upstreamAhead"], item["upstreamBehind"]), ("", None, None))
        self.assertEqual((item["mainAhead"], item["mainBehind"]), (0, 0))

    def test_detached_head_has_no_upstream_but_has_main_counts(self):
        det = os.path.join(self.tmp, "wt", "det")
        git(self.proj, "worktree", "add", "-q", "--detach", det, "master")
        commit(det, "d.txt")
        item = self.listed()[det]
        self.assertEqual((item["branch"], item["upstream"], item["upstreamAhead"]), ("", "", None))
        self.assertEqual((item["mainAhead"], item["mainBehind"]), (1, 0))

    def test_deleted_upstream_ref_keeps_name_without_numbers(self):
        git(self.proj, "update-ref", "-d", "refs/remotes/origin/feat/sync")
        item = self.listed()[self.wt]
        self.assertEqual((item["upstream"], item["upstreamAhead"], item["upstreamBehind"]), ("origin/feat/sync", None, None))
        self.assertIsNotNone(item["mainAhead"])

    def test_missing_worktree_directory_does_not_break_scan(self):
        shutil.rmtree(self.wt)
        item = self.listed()[self.wt]
        self.assertTrue(item["missing"])
        self.assertEqual((item["upstreamAhead"], item["upstreamBehind"]), (2, 1))

    def test_one_count_per_branch_and_comparison(self):
        rec = RecordingGit()
        self.listed(rec)
        counts = [c for c in rec.calls if c[:3] == ("rev-list", "--left-right", "--count")]
        # Główny katalog i worktree: po jednym porównaniu z główną, a upstream tylko tam, gdzie jest.
        self.assertEqual(len(counts), 2 + 2 + 0)

    def test_scan_runs_no_network_commands(self):
        rec = RecordingGit()
        self.listed(rec)
        verbs = {c[0] for c in rec.calls}
        self.assertFalse(verbs & {"fetch", "ls-remote", "pull", "push", "clone", "remote"}, verbs)


class RemotesAndFetchTest(DivergenceTestCase):
    def test_repository_without_remote(self):
        proj = self.repo("solo")
        wt = self.worktree(proj, "feat/x")
        commit(wt, "x.txt")
        items = self.listed()
        for path in (proj, wt):
            self.assertEqual(items[path]["remotes"], [])
            self.assertEqual(items[path]["lastFetch"], "")
            self.assertEqual((items[path]["upstream"], items[path]["upstreamAhead"], items[path]["upstreamBehind"]),
                             ("", None, None))
        self.assertEqual((items[wt]["mainAhead"], items[wt]["mainBehind"]), (1, 0))

    def test_last_fetch_empty_before_fetch_and_equals_fetch_head_after(self):
        proj = self.repo("proj")
        self.add_remote(proj)
        self.add_remote(proj, "backup")
        item = self.listed()[proj]
        self.assertEqual(item["remotes"], ["origin", "backup"])
        self.assertEqual(item["lastFetch"], "")
        git(proj, "push", "-q", "origin", "master")
        git(proj, "fetch", "-q", "origin")
        fetch_head = os.path.join(proj, ".git", "FETCH_HEAD")
        os.utime(fetch_head, (1_700_000_000, 1_700_000_000))
        from tide_daemon.agents.timeutil import iso
        self.assertEqual(self.listed()[proj]["lastFetch"], iso(1_700_000_000))

    def test_remote_name_with_dots(self):
        proj = self.repo("proj")
        self.add_remote(proj, "my.fork")
        self.assertEqual(self.listed()[proj]["remotes"], ["my.fork"])


class MainBranchTest(DivergenceTestCase):
    def check(self, main):
        proj = self.repo(main + "-repo", main)
        wt = self.worktree(proj, "feat/y")
        commit(wt, "y1.txt")
        commit(wt, "y2.txt")
        commit(proj, "z.txt")
        item = self.listed()[wt]
        self.assertEqual(item["mainBranch"], main)
        self.assertEqual((item["mainAhead"], item["mainBehind"]), (2, 1))

    def test_main(self):
        self.check("main")

    def test_develop(self):
        self.check("develop")

    def test_no_main_branch_gives_no_main_counts(self):
        proj = self.repo("trunk-repo", "trunk")
        item = self.listed()[proj]
        self.assertEqual((item["mainBranch"], item["mainAhead"], item["mainBehind"]), ("", None, None))


if __name__ == "__main__":
    unittest.main()
