"""PT-194: baza przez rzeczywiste /Board na prywatnej magistrali."""
import os

from .test_cardflow_dbus import FlowCase, git


class StartBaseDbusTest(FlowCase):
    def test_feature_start_snapshot_and_restart_keep_original_commit(self):
        git(self.repo, "checkout", "-qb", "feature/base", env=self.git_env)
        with open(os.path.join(self.repo, "feature.txt"), "w", encoding="utf-8") as fh:
            fh.write("zawartość wybranej bazy\n")
        git(self.repo, "add", "feature.txt", env=self.git_env)
        git(self.repo, "commit", "-qm", "feature base", env=self.git_env)
        commit = git(self.repo, "rev-parse", "HEAD", env=self.git_env).strip()
        git(self.repo, "checkout", "-q", "master", env=self.git_env)
        card = self.card(description="ATRAPA: pytanie")
        reply = self.start(card, baseRef="refs/heads/feature/base", baseCommit=commit)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(git(reply["cardWorktree"], "rev-parse", "HEAD", env=self.git_env).strip(), commit)
        with open(os.path.join(reply["worktree"], "feature.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "zawartość wybranej bazy\n")
        git(self.repo, "branch", "-f", "feature/base", "master", env=self.git_env)
        self.restart()
        shown = self.shown(card)
        self.assertEqual(shown["baseCommit"], commit)
        self.assertEqual(shown["baseVersion"], 1)
        self.assertEqual(shown["derived"]["flow"]["base"]["baseRef"], "refs/heads/feature/base")
        self.assertEqual(shown["derived"]["flow"]["initialBase"]["baseCommit"], commit)
        self.assertEqual(len(self.of(card, "card.started")), 1)
        self.assertEqual(git(self.repo, "rev-parse", "master", env=self.git_env).strip(), self.master)
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD", env=self.git_env).strip(), "master")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "feature.txt")))
