"""PT-209: publiczna lista całego lokalnego zbioru oraz start od jej dokładnego wyniku."""
import json
import os
from unittest import mock

from . import test_cardflow_base as bases
from .test_cardflow_dbus import git
from .privatebus import wait_until


class CardBranchesTest(bases.StartBaseSetup):
    # Nie powtarzamy macierzy PT-194, korzystamy tylko z jej przygotowania repozytorium.
    def listed(self, card, **request):
        box = []
        self.board.list_card_branches(card, json.dumps({"purpose": "base", **request}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        return json.loads(box[0])

    def test_search_before_limit_and_strict_request(self):
        card = self.card()
        for i in range(120):
            git(self.repo, "branch", f"feature/branch-{i:03}")
        first = self.listed(card)
        self.assertTrue(first["ok"], first)
        self.assertEqual(len(first["branches"]), 50)
        self.assertTrue(first["hasMore"])
        self.assertEqual(first["mainRef"], "refs/heads/master")
        self.assertEqual(first["branches"][0]["ref"], first["mainRef"])
        found = self.listed(card, text="BRANCH-119")
        self.assertEqual(found["branches"], [{"ref": "refs/heads/feature/branch-119", "name": "feature/branch-119",
                                               "kind": "local", "remote": "", "commit": self.master,
                                               "selectable": True, "reason": ""}])
        self.assertFalse(found["hasMore"])
        self.assertEqual(len(self.listed(card, purpose="merge", limit=100)["branches"]), 100)
        for request in ({"limit": 101}, {"limit": True}, {"limit": 0}, {"text": []}, {"repo": self.repo},
                        {"purpose": "other"}, {"cursor": "next"}):
            self.assertFalse(self.listed(card, **request)["ok"], request)
        self.assertEqual(self.events(card), [])

    def test_list_to_start_preserves_user_repo_and_has_no_network_or_upstream(self):
        feature = self.feature()
        card = self.card()
        dirty = os.path.join(self.repo, "user.txt")
        with open(dirty, "w", encoding="utf-8") as fh:
            fh.write("staged\n")
        git(self.repo, "add", "user.txt")
        with open(dirty, "a", encoding="utf-8") as fh:
            fh.write("unstaged\n")
        git(self.repo, "config", "branch.autoSetupMerge", "always")
        before = git(self.repo, "diff", "--cached", "--binary"), git(self.repo, "diff", "--binary")
        original = self.flow.engine.git.run
        with mock.patch.object(self.flow.engine.git, "run", wraps=original) as run:
            picked = self.listed(card, text="feature/start")["branches"][0]
            reply = self.start(card, baseRef=picked["ref"], baseCommit=picked["commit"], cardBranch="card/custom-start")
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(reply["cardBranch"], "card/custom-start")
        self.assertEqual(git(reply["cardWorktree"], "rev-parse", "HEAD").strip(), feature)
        with open(os.path.join(reply["worktree"], "feature.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "tylko feature\n")
        self.assertEqual(before, (git(self.repo, "diff", "--cached", "--binary"), git(self.repo, "diff", "--binary")))
        with open(dirty, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "staged\nunstaged\n")
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD").strip(), "master")
        for call in run.call_args_list:
            self.assertFalse(set(call.args[1:]) & {"fetch", "pull", "push"}, call)
        for branch in (reply["cardBranch"], reply["branch"]):
            self.assertFalse(original(self.repo, "config", "--get", f"branch.{branch}.merge").ok)
            self.assertFalse(original(self.repo, "config", "--get", f"branch.{branch}.remote").ok)

    def test_no_main_deleted_ref_stale_sha_and_branch_collision(self):
        self.feature()
        git(self.repo, "checkout", "-q", "feature/start")
        git(self.repo, "branch", "-D", "master")
        card = self.card()
        listed = self.listed(card)
        self.assertEqual(listed["mainRef"], "")
        self.assertTrue(listed["warnings"])
        self.assertFalse(self.start(card)["ok"])
        picked = listed["branches"][0]
        git(self.repo, "commit", "--allow-empty", "-qm", "move base")
        stale = self.start(card, baseRef=picked["ref"], baseCommit=picked["commit"])
        self.assertIn("zmieniła", stale["message"])
        git(self.repo, "checkout", "--detach", "-q")
        git(self.repo, "branch", "-D", "feature/start")
        deleted = self.start(card, baseRef=picked["ref"], baseCommit=picked["commit"])
        self.assertIn("nie istnieje", deleted["message"])
        self.assertEqual(self.events(card), [])
        git(self.repo, "branch", "master")
        for branch in ("master", "bad name", "HEAD", "-wrong"):
            self.assertFalse(self.start(card, cardBranch=branch)["ok"], branch)
        response = self.start(card, entry="planning", mode="light", baseRef="refs/heads/master")
        self.assertIn("tylko", response["message"])
        self.assertEqual(self.events(card), [])
