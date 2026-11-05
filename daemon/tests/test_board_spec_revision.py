"""PT-203: publiczne RenderLaunch z rewizją, bez skutków przepływu w repozytorium projektu."""
import json
import os

from .test_board_core import BoardCase, git_out
from tide_daemon.board.store import BoardStore
from tide_daemon.board_monitor import BoardMonitor


class SpecRevisionTest(BoardCase):
    def setUp(self):
        super().setUp()
        self.project = self.repo("project")
        self.file("user.txt", "pierwsza\n")
        git_out(self.project, "add", ".")
        git_out(self.project, "commit", "-qm", "initial")
        git_out(self.project, "checkout", "-qb", "feature")
        self.file("feature-only.txt", "treść tylko w rewizji\n")
        git_out(self.project, "add", ".")
        git_out(self.project, "commit", "-qm", "feature file")
        self.sha = git_out(self.project, "rev-parse", "HEAD").strip()
        git_out(self.project, "update-ref", "refs/remotes/team/feature", self.sha)
        git_out(self.project, "checkout", "-q", "master")
        self.file("user.txt", "staged\n")
        git_out(self.project, "add", "user.txt")
        self.file("user.txt", "unstaged\n")
        self.file("untracked.txt", "własny plik\n")
        self.store.upsert_project({"repo": self.project})
        self.store.create_card({"title": "Spec", "project": "project"})

    def file(self, name, text):
        with open(os.path.join(self.project, name), "w") as fh:
            fh.write(text)

    def state(self):
        with open(os.path.join(self.project, ".git", "index"), "rb") as fh:
            index = fh.read()
        with open(os.path.join(self.project, "untracked.txt"), "rb") as fh:
            untracked = fh.read()
        return (git_out(self.project, "show-ref"), git_out(self.project, "symbolic-ref", "HEAD"),
                git_out(self.project, "rev-parse", "HEAD"), index,
                git_out(self.project, "diff"), git_out(self.project, "diff", "--cached"),
                git_out(self.project, "ls-files", "--others", "--exclude-standard"),
                git_out(self.project, "worktree", "list", "--porcelain"), untracked)

    def test_extra_project_retains_its_checkout_and_existing_access(self):
        extra = self.repo("extra")
        git_out(extra, "commit", "--allow-empty", "-qm", "extra base")
        self.store.upsert_project({"repo": extra})
        self.store.update_card("K-1", {"projects": ["extra"]})
        before = (git_out(extra, "show-ref"), git_out(extra, "symbolic-ref", "HEAD"),
                  git_out(extra, "status", "--porcelain"))
        reply = self.render({"entry": "spec", "baseRef": "refs/heads/feature", "baseCommit": self.sha})
        self.assertTrue(reply["ok"], reply)
        self.assertIn(extra, reply["extraRepos"])
        self.assertIn(extra, reply["addDirs"])
        self.assertIn("dodatkowe projekty zachowują swoje bazy tylko do odczytu", reply["text"])
        self.assertEqual((git_out(extra, "show-ref"), git_out(extra, "symbolic-ref", "HEAD"),
                          git_out(extra, "status", "--porcelain")), before)

    def render(self, request):
        monitor = BoardMonitor(self.store)
        try:
            return json.loads(monitor.render_launch("K-1", json.dumps(request)))
        finally:
            monitor.stop()

    def test_local_remote_revision_restart_and_unchanged_user_checkout(self):
        before = self.state()
        self.assertFalse(os.path.exists(os.path.join(self.project, "feature-only.txt")))
        for ref in ("refs/heads/feature", "refs/remotes/team/feature"):
            with self.subTest(ref=ref):
                reply = self.render({"entry": "spec", "baseRef": ref, "baseCommit": self.sha})
                self.assertTrue(reply["ok"], reply)
                self.assertEqual(reply["specRevision"], {"repo": self.project, "ref": ref, "commit": self.sha})
                self.assertIn(f"show {self.sha}:ścieżka", reply["text"])
                self.assertIn(f"ls-tree -r --name-only {self.sha}", reply["text"])
                self.assertIn("także pliki nieobecne w bieżącym checkoutcie", reply["text"])
                self.assertEqual(git_out(self.project, "show", f"{self.sha}:feature-only.txt").strip(),
                                 "treść tylko w rewizji")
                self.store = BoardStore(self.root)
                card = self.store.snapshot()["cards"][0]
                self.assertEqual(card["specRevision"], reply["specRevision"])
                self.assertEqual((card["baseRef"], card["baseCommit"], card["baseVersion"],
                                  card["branch"], card["worktree"], card["entry"]), ("", "", 0, "", "", ""))
                self.assertEqual(self.render({"entry": "spec"})["specRevision"], reply["specRevision"])
                self.assertEqual(self.state(), before)
        self.assertFalse(os.path.exists(self.project + "-worktrees"))
        self.assertNotIn("card.started", git_out(self.root, "log", "-p"))

    def test_moved_deleted_invalid_and_incomplete_selection_requires_refresh(self):
        for ref in ("refs/heads/feature", "refs/remotes/team/feature"):
            self.assertTrue(self.render({"entry": "spec", "baseRef": ref, "baseCommit": self.sha})["ok"])
            git_out(self.project, "update-ref", ref, "master")
            before = self.state()
            saved_card = self.card_text("K-1")
            for request in ({"entry": "spec", "baseRef": ref, "baseCommit": self.sha}, {"entry": "spec"}):
                reply = self.render(request)
                self.assertFalse(reply["ok"], reply)
                self.assertIn("odśwież", reply["message"])
                self.assertEqual(self.state(), before)
                self.assertEqual(self.card_text("K-1"), saved_card)
            git_out(self.project, "update-ref", "-d", ref)
            self.assertFalse(self.render({"entry": "spec", "baseRef": ref, "baseCommit": self.sha})["ok"])
            self.assertFalse(self.render({"entry": "spec"})["ok"])
            git_out(self.project, "update-ref", ref, self.sha)
        before = self.state()
        for data in ({"baseRef": "HEAD", "baseCommit": self.sha}, {"baseRef": "refs/heads/feature"},
                     {"baseCommit": self.sha}, {"baseRef": None, "baseCommit": None},
                     {"baseRef": "refs/heads/feature", "baseCommit": ""}):
            self.assertFalse(self.render({"entry": "spec", **data})["ok"])
        self.assertEqual(self.state(), before)

    def test_symbolic_remote_ref_is_rejected_by_shared_policy(self):
        git_out(self.project, "symbolic-ref", "refs/remotes/team/HEAD", "refs/remotes/team/feature")
        before = self.state()
        reply = self.render({"entry": "spec", "baseRef": "refs/remotes/team/HEAD", "baseCommit": self.sha})
        self.assertFalse(reply["ok"])
        self.assertIn("symboliczny", reply["message"])
        self.assertIsNone(self.store.snapshot()["cards"][0]["specRevision"])
        self.assertEqual(self.state(), before)

