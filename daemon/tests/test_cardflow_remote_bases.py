"""PT-195: publiczny start i lista na prawdziwym repozytorium, bez sieci."""
import json
import os
from unittest import mock

from .privatebus import wait_until
from .test_cardflow_base import StartBaseSetup
from .test_cardflow_dbus import git


class RemoteBasesTest(StartBaseSetup):
    def listing(self, card, **request):
        replies = []
        self.board.list_card_branches(card, json.dumps({"purpose": "base", **request}), replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=10))
        result = json.loads(replies[0])
        self.assertTrue(result["ok"], result)
        return result

    def remote(self):
        commit = self.feature()
        git(self.repo, "update-ref", "refs/remotes/origin/feature/start", commit)
        git(self.repo, "update-ref", "refs/remotes/second/feature/start", self.master)
        git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/feature/start")
        git(self.repo, "tag", "feature-tag")
        return commit

    def test_remote_identity_exact_start_no_mutations_and_deleted_ref_continuation(self):
        commit = self.remote()
        card = self.card()
        refs = git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes/")
        runner = self.flow.engine.git
        with mock.patch.object(runner, "run", wraps=runner.run) as calls:
            listed = self.listing(card, text="feature/start")
            self.assertEqual([b["ref"] for b in listed["branches"]], [
                "refs/heads/feature/start", "refs/remotes/origin/feature/start", "refs/remotes/second/feature/start"])
            self.assertEqual([b["remote"] for b in listed["branches"]], ["", "origin", "second"])
            self.assertEqual([b["kind"] for b in listed["branches"]], ["local", "remote", "remote"])
            self.assertEqual([b["commit"] for b in listed["branches"]], [commit, commit, self.master])
            self.assertEqual(self.listing(card, text="refs/remotes/second", limit=1)["branches"][0]["remote"], "second")
            self.assertEqual(len(self.listing(card, purpose="merge")["branches"]), 2)
            self.assertFalse(any(b["ref"].endswith("/HEAD") or b["ref"].startswith("refs/tags/")
                                 for b in self.listing(card)["branches"]))
            git(self.repo, "config", "branch.autoSetupMerge", "always")
            reply = self.start(card, baseRef="refs/remotes/origin/feature/start", baseCommit=commit)
            self.assertTrue(reply["ok"], reply)
            self.assertFalse(any(any(arg in ("fetch", "pull", "push", "update-ref") for arg in c.args[1:])
                                 for c in calls.call_args_list))
        self.board.publish()
        self.assertEqual(self.shown(card)["baseRef"], "refs/remotes/origin/feature/start")
        self.assertEqual(self.shown(card)["derived"]["flow"]["base"]["status"], "known")
        with open(os.path.join(reply["worktree"], "feature.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "tylko feature\n")
        self.assertEqual(git(self.repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes/"), refs)
        self.assertFalse(runner.run(self.repo, "config", "--get", f"branch.{reply['cardBranch']}.remote").ok)
        self.assertFalse(runner.run(self.repo, "config", "--get", f"branch.{reply['cardBranch']}.merge").ok)
        self.assertFalse(runner.sha(self.repo, "refs/heads/origin/feature/start"))
        self.assertTrue(json.loads(self.board.move_card(card, "todo", 0))["ok"])
        git(self.repo, "update-ref", "-d", "refs/remotes/origin/feature/start")
        self.restart()
        second = self.start(card)
        self.assertTrue(second["ok"], second)
        self.assertEqual(git(second["worktree"], "rev-parse", "HEAD").strip(), commit)
        self.assertEqual(self.of(card, "card.started")[-1]["baseCommit"], commit)

    def test_moved_deleted_symbolic_and_missing_sha_fail_without_history(self):
        commit = self.remote()
        card = self.card()
        git(self.repo, "update-ref", "refs/remotes/origin/feature/start", self.master)
        cases = [{"baseRef": "refs/remotes/origin/feature/start", "baseCommit": commit},
                 {"baseRef": "refs/remotes/origin/HEAD", "baseCommit": self.master},
                 {"baseRef": "refs/remotes/origin/feature/start"}]
        git(self.repo, "update-ref", "-d", "refs/remotes/second/feature/start")
        cases.append({"baseRef": "refs/remotes/second/feature/start", "baseCommit": self.master})
        for request in cases:
            self.assertFalse(self.start(card, **request)["ok"])
            self.assertEqual(self.events(card), [])
        self.assertEqual(self.listing(card)["recentBases"], [])
        # Repozytorium nie ma skonfigurowanego origin; nazwa remote nie wymaga połączenia.
        reply = self.start(card, baseRef="refs/remotes/origin/feature/start", baseCommit=self.master)
        self.assertTrue(reply["ok"], reply)

    def test_twelve_starts_order_limit_dedup_restart_and_repo_isolation(self):
        refs = [f"refs/remotes/team/base-{i:02}" for i in range(11)]
        for ref in refs:
            git(self.repo, "update-ref", ref, self.master)
        for index, ref in enumerate(refs + [refs[2]]):
            reply = self.start(self.card(title=f"Start {index}"), baseRef=ref, baseCommit=self.master)
            self.assertTrue(reply["ok"], reply)
        card = self.card(title="Przeglądanie")
        expected = [refs[2]] + list(reversed(refs[3:])) + [refs[1]]
        self.assertEqual([b["ref"] for b in self.listing(card)["recentBases"]], expected)
        self.assertEqual(self.events(card), [])
        self.restart()
        self.assertEqual([b["ref"] for b in self.listing(card, text="nie ma")["recentBases"]], expected)
        worktree = os.path.join(self.base, "linked")
        git(self.repo, "worktree", "add", "--detach", worktree, self.master)
        self.store.upsert_project({"slug": "linked", "repo": worktree, "name": "Worktree"})
        self.store.update_card(card, {"project": "linked"})
        self.assertEqual([b["ref"] for b in self.listing(card)["recentBases"]], expected)
        other = os.path.join(self.base, "other")
        os.makedirs(other)
        git(other, "init", "-q")
        git(other, "commit", "--allow-empty", "-qm", "initial")
        git(other, "update-ref", refs[2], "HEAD")
        self.store.upsert_project({"slug": "other", "repo": other, "name": "Inne repozytorium"})
        self.store.update_card(card, {"project": "other"})
        self.assertEqual(self.listing(card)["recentBases"], [])
        self.store.update_card(card, {"project": "proj"})
        git(self.repo, "update-ref", "-d", refs[2])
        self.assertNotIn(refs[2], [b["ref"] for b in self.listing(card)["recentBases"]])
        self.assertEqual(sum(len(self.of(f"K-{i}", "card.started")) for i in range(1, 13)), 12)
        # Ostatnia baza nie jest domyślna dla kolejnego zlecenia.
        self.assertTrue(self.start(card)["ok"])
        self.assertEqual(self.of(card, "card.started")[-1]["baseRef"], "refs/heads/master")

    def test_base_change_fixture_versions_and_shortcut_failure(self):
        self.remote()
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        journal = self.flow.engine.journal
        base = {"baseRef": "refs/remotes/second/feature/start", "baseBranch": "second/feature/start",
                "baseCommit": self.master}
        journal.append(card, "card.base-changed", previousBaseVersion=1, baseVersion=2, base=base)
        journal.append(card, "card.base-changed", previousBaseVersion=1, baseVersion=3,
                       base={"baseRef": "refs/heads/feature/start", "baseBranch": "feature/start", "baseCommit": self.master})
        recent = self.listing(card)["recentBases"]
        self.assertEqual([b["ref"] for b in recent], [base["baseRef"], "refs/heads/master"])
        self.restart()
        self.assertEqual(self.listing(card)["recentBases"], recent)
        with mock.patch.object(self.flow.engine.journal, "cards", side_effect=OSError("odczyt")):
            listed = self.listing(card)
        self.assertEqual(listed["recentBases"], [])
        self.assertTrue(listed["branches"])
        self.assertTrue(listed["warnings"])


