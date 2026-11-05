"""PT-198: publiczne /Board, atrapy gh/glab, prawdziwe lokalne refy i start."""
import json
import os

from . import hostingfakes
from .test_cardflow_dbus import FlowCase, git


class LinkSuggestionsDbusTest(FlowCase):
    def fake(self, responses=None, mode=""):
        hostingfakes.install(os.path.join(self.base, "fakebin"), {"responses": responses or {}, "mode": mode})

    def suggest(self, card, text=""):
        return self.call("SuggestCardBase", card, json.dumps({"text": text, "revision": 9, "scope": "links"}))

    def remote(self, name, url, branch="feature/source", sha=None):
        git(self.repo, "remote", "add", name, url, env=self.git_env)
        if sha:
            git(self.repo, "update-ref", f"refs/remotes/{name}/{branch}", sha, env=self.git_env)

    def test_github_two_remotes_source_not_target_stale_and_start_local_sha(self):
        git(self.repo, "checkout", "-qb", "feature/source", env=self.git_env)
        with open(os.path.join(self.repo, "source.txt"), "w", encoding="utf-8") as fh:
            fh.write("Tylko gałąź źródłowa\n")
        git(self.repo, "add", "source.txt", env=self.git_env)
        git(self.repo, "commit", "-qm", "source branch", env=self.git_env)
        local_sha = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "checkout", "-q", "master", env=self.git_env)
        self.remote("origin", "git@github.com:team/proj.git", sha=local_sha)
        self.remote("mirror", "https://github.com/team/proj.git", sha=local_sha)
        git(self.repo, "branch", "release/target", env=self.git_env)
        self.fake({"repos/team/proj/pulls/17": hostingfakes.github("b" * 40)})
        card = self.card(description="ATRAPA: pytanie\nhttps://github.com/team/proj/pull/17")
        before = (git(self.repo, "show-ref"), git(self.repo, "status", "--porcelain"), self.shown(card))
        result = self.suggest(card)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["revision"], 9)
        self.assertEqual({c["ref"] for c in result["candidates"]},
                         {"refs/remotes/origin/feature/source", "refs/remotes/mirror/feature/source"})
        picked = result["candidates"][0]
        self.assertEqual((picked["commit"], picked["sourceCommit"], picked["stale"]), (local_sha, "b" * 40, True))
        self.assertEqual(picked["targetBranch"], "release/target")
        self.assertEqual(before, (git(self.repo, "show-ref"), git(self.repo, "status", "--porcelain"), self.shown(card)))
        self.assertEqual(self.events(card), [])
        started = self.start(card, baseRef=picked["ref"], baseCommit=picked["commit"])
        self.assertTrue(started["ok"], started)
        self.assertEqual(self.of(card, "card.started")[0]["baseCommit"], local_sha)
        self.assertEqual(git(started["cardWorktree"], "rev-parse", "HEAD").strip(), local_sha)

    def test_fork_never_uses_same_named_target_branch_missing_remote_then_missing_ref(self):
        self.remote("origin", "https://github.com/team/proj.git", sha=self.master)
        git(self.repo, "branch", "feature/source", env=self.git_env)
        self.fake({"repos/team/proj/pulls/17": hostingfakes.github(self.master, source="user/proj")})
        card = self.card(description="https://github.com/team/proj/pull/17")
        first = self.suggest(card)["candidates"][0]
        self.assertFalse(first["selectable"])
        self.assertIn("remote", first["reason"])
        self.remote("fork", "ssh://git@github.com/user/proj.git")
        second = self.suggest(card)["candidates"][0]
        self.assertFalse(second["selectable"])
        self.assertIn("lokalnego refu", second["reason"])
        git(self.repo, "update-ref", "refs/remotes/fork/feature/source", self.master, env=self.git_env)
        picked = self.suggest(card)["candidates"]
        self.assertEqual([c["ref"] for c in picked], ["refs/remotes/fork/feature/source"])
        self.assertTrue(picked[0]["selectable"])
        self.assertFalse(picked[0]["stale"])
        git(self.repo, "config", "branch.feature/source.remote", "fork", env=self.git_env)
        git(self.repo, "config", "branch.feature/source.merge", "refs/heads/feature/source", env=self.git_env)
        self.assertEqual({c["ref"] for c in self.suggest(card)["candidates"]},
                         {"refs/heads/feature/source", "refs/remotes/fork/feature/source"})

    def test_gitlab_subgroups_self_hosted_and_github_enterprise(self):
        self.remote("gitlab", "ssh://git@git.example.org/team/sub/proj.git", sha=self.master)
        self.remote("fork", "https://git.example.org/user/sub/proj.git", sha=self.master)
        self.fake(hostingfakes.gitlab(self.master))
        card = self.card(description="Nie wysyłaj opisu FAKE_PRIVATE_DESCRIPTION")
        picked = self.suggest(card, "https://git.example.org/team/sub/proj/-/merge_requests/17")["candidates"][0]
        self.assertEqual((picked["host"], picked["project"], picked["sourceProject"], picked["ref"]),
                         ("git.example.org", "team/sub/proj", "user/sub/proj", "refs/remotes/fork/feature/source"))
        self.remote("enterprise", "git@github.example.org:team/proj.git", sha=self.master)
        self.fake({"repos/team/proj/pulls/17": hostingfakes.github(self.master, host="github.example.org")})
        result = self.suggest(card, "https://github.example.org/team/proj/pull/17")
        self.assertEqual(result["candidates"][0]["ref"], "refs/remotes/enterprise/feature/source")
        with open(os.path.join(self.base, "fakebin", "hosting-calls.jsonl")) as fh:
            calls = fh.read()
        self.assertNotIn("FAKE_PRIVATE_DESCRIPTION", calls)
        self.assertIn('["api", "--hostname", "git.example.org", "--method", "GET", "projects/team%2Fsub%2Fproj/merge_requests/17"]', calls)

    def test_foreign_and_unrecognized_urls_do_not_invoke_cli_or_expose_credentials(self):
        self.remote("origin", "https://user:FAKE_REMOTE_SECRET@github.com/team/proj.git")
        self.fake()
        card = self.card(description="Bez linku")
        for url in ("https://foreign.example/team/proj/pull/17", "https://github.com/other/proj/pull/17",
                    "http://github.com/team/proj/pull/17", "https://github.com/team/proj/issues/17",
                    "https://user:FAKE_SECRET@github.com/team/proj/pull/17",
                    "https://github.com/team/proj/pull/17?token=FAKE_SECRET",
                    "https://github.com/team/proj/pull/17/../../evil"):
            reply = self.suggest(card, url)
            self.assertEqual(reply["candidates"], [], reply)
            self.assertTrue(reply["warnings"])
            self.assertNotIn("FAKE_SECRET", json.dumps(reply))
        self.assertFalse(os.path.exists(os.path.join(self.base, "fakebin", "hosting-calls.jsonl")))

    def test_source_branch_with_shell_metacharacters_is_only_data(self):
        branch = "feature/$(id);<tag>"
        self.remote("origin", "git@github.com:team/proj.git", branch=branch, sha=self.master)
        self.fake({"repos/team/proj/pulls/17": hostingfakes.github(self.master, branch=branch)})
        card = self.card(description="https://github.com/team/proj/pull/17")
        picked = self.suggest(card)["candidates"][0]
        self.assertEqual(picked["ref"], "refs/remotes/origin/" + branch)
        self.assertTrue(picked["selectable"])
        with open(os.path.join(self.base, "fakebin", "hosting-calls.jsonl")) as fh:
            self.assertEqual(json.loads(fh.readline()),
                             ["api", "--hostname", "github.com", "--method", "GET", "repos/team/proj/pulls/17"])

    def test_cli_faults_limits_timeout_bad_identity_do_not_block_start_or_leak_stderr(self):
        self.remote("origin", "git@github.com:team/proj.git", sha=self.master)
        card = self.card(description="https://github.com/team/proj/pull/17\nATRAPA: pytanie")
        for mode in ("error", "bad-json", "large", "large-stderr", "timeout"):
            self.fake(mode=mode)
            reply = self.suggest(card)
            self.assertTrue(reply["ok"], reply)
            self.assertEqual(reply["candidates"], [])
            self.assertTrue(reply["warnings"], mode)
            self.assertNotIn("FAKE_SECRET", json.dumps(reply))
        invalid = hostingfakes.github(self.master)
        invalid["head"]["repo"]["html_url"] = "https://foreign.example/team/proj"
        self.fake({"repos/team/proj/pulls/17": invalid})
        self.assertEqual(self.suggest(card)["candidates"], [])
        self.assertTrue(self.start(card)["ok"])
        self.assertNotIn("FAKE_SECRET", json.dumps(self.events(card)))
