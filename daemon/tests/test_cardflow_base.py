"""PT-194: publiczny StartCard/Snapshot, prawdziwy git, krok oczekujący na wolny tor."""
import json
import os
import unittest
from unittest import mock

from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git
from tide_daemon.cardflow.engine import FlowDeferred
from tide_daemon.cardflow.state import fold, base_of, derived


class StartBaseTest(FlowInProcess):
    def make_flow(self):
        from tide_daemon.cardflow_monitor import CardFlowMonitor
        # Publiczne wywołania działają normalnie; zegar nie uruchamia wykonania ticketu w tym teście.
        with mock.patch.object(CardFlowMonitor, "start"):
            flow = super().make_flow()
        flow.engine._launch = mock.Mock(side_effect=FlowDeferred("kolejka testowa"))
        return flow

    def feature(self):
        git(self.repo, "checkout", "-qb", "feature/start")
        with open(os.path.join(self.repo, "feature.txt"), "w", encoding="utf-8") as fh:
            fh.write("tylko feature\n")
        git(self.repo, "add", "feature.txt")
        git(self.repo, "commit", "-qm", "feature")
        commit = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "checkout", "-q", "master")
        return commit

    def assert_base(self, card, ref, commit, version=1):
        self.board.publish()
        shown = self.shown(card)
        expected = {"status": "known", "baseRef": ref, "baseBranch": ref[len("refs/heads/"):],
                    "baseCommit": commit}
        self.assertEqual(shown["derived"]["flow"]["base"], expected)
        self.assertEqual(shown["derived"]["flow"]["baseVersion"], version)
        self.assertEqual(shown["baseVersion"], version)
        for key in ("baseRef", "baseBranch", "baseCommit"):
            self.assertEqual(shown[key], expected[key])
        return shown

    def test_feature_content_pinned_sha_restart_and_projection_retry(self):
        commit = self.feature()
        card = self.card()
        git(self.repo, "config", "branch.autoSetupMerge", "always")
        # Zapis dziennika jest skuteczny; projekcja chwilowo nie może zapisać pliku.
        with mock.patch.object(self.store, "project_card_base", side_effect=OSError("awaria projekcji")):
            reply = self.start(card, baseRef="refs/heads/feature/start", baseCommit=commit)
        self.assertTrue(reply["ok"], reply)
        self.assert_base(card, "refs/heads/feature/start", commit)
        self.assertEqual(git(reply["cardWorktree"], "rev-parse", "HEAD").strip(), commit)
        with open(os.path.join(reply["worktree"], "feature.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "tylko feature\n")
        self.assertEqual(git(self.repo, "rev-parse", "master").strip(), self.master)
        self.assertEqual(git(self.repo, "symbolic-ref", "--short", "HEAD").strip(), "master")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "feature.txt")))
        from tide_daemon.cardflow.gitops import FlowGit
        self.assertFalse(FlowGit().run(self.repo, "config", "--get", f"branch.{reply['cardBranch']}.remote").ok)
        self.assertFalse(FlowGit().run(self.repo, "config", "--get", f"branch.{reply['cardBranch']}.merge").ok)
        git(self.repo, "branch", "-f", "feature/start", "master")
        git(self.repo, "branch", "-D", "feature/start")
        self.restart()
        self.assert_base(card, "refs/heads/feature/start", commit)
        self.flow.engine._project_base(card)
        self.flow.engine._project_base(card)
        model = self.store.load().cards[int(card[2:])]
        self.assertEqual((model.base_ref, model.base_branch, model.base_commit),
                         ("refs/heads/feature/start", "feature/start", commit))
        self.assertFalse(model.doc.has("base-version"))
        self.assertEqual(self.shown(card)["derived"]["flow"]["initialBase"]["baseCommit"], commit)
        self.assertEqual(len(self.of(card, "card.started")), 1)
        self.assertEqual(len(self.results(card, "card-branch")), 1)
        self.assertEqual(git(reply["cardWorktree"], "rev-parse", "HEAD").strip(), commit)

    def test_old_client_uses_main_policy_and_project_pin_without_changing_config(self):
        commit = self.feature()
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        self.assert_base(card, "refs/heads/master", self.master)
        git(self.repo, "config", "tide.mainBranch", "feature/start")
        pinned = self.card(title="Przypięta główna")
        self.assertTrue(self.start(pinned)["ok"])
        self.assert_base(pinned, "refs/heads/feature/start", commit)
        self.assertEqual(git(self.repo, "config", "--get", "tide.mainBranch").strip(), "feature/start")

    def test_manual_columns_keep_null_derived_and_journal_base_even_without_projection(self):
        for source, column in (("waiting", "todo"), ("review", "todo"), ("todo", "done"), ("todo", "archived")):
            with self.subTest(source=source, column=column):
                card = self.card(title=f"Ręczna {source} {column}")
                journal = self.flow.engine.journal
                journal.append(card, "card.started", entry="now", column="todo", repo=self.repo,
                               baseRef="refs/heads/master", baseBranch="master", baseCommit=self.master,
                               baseVersion=1)
                self.store.move_card(card, column)
                journal.append(card, "card.moved", **{"from": source, "to": column})
                for restart in (False, True):
                    if restart:
                        self.restart()
                    self.board.publish()
                    shown = self.shown(card)
                    self.assertIsNone(shown["derived"])
                    self.assertEqual(shown["boardColumn"], column)
                    self.assertEqual(shown["baseRef"], "refs/heads/master")
                    self.assertEqual(shown["baseCommit"], self.master)
                    self.assertEqual(shown["baseVersion"], 1)
                self.assertEqual(self.store.load().cards[int(card[2:])].base_commit, "")

    def test_invalid_deleted_or_moved_base_leaves_no_order_or_number(self):
        commit = self.feature()
        git(self.repo, "tag", "tag-start")
        git(self.repo, "branch", "gone", "feature/start")
        git(self.repo, "branch", "-D", "gone")
        git(self.repo, "branch", "-f", "feature/start", "master")
        cases = [{"baseRef": ref} for ref in ("feature/start", "refs/tags/tag-start", commit, "HEAD",
                                              "refs/heads/master~1", "refs/heads/gone", "refs/heads/")]
        cases += [{"baseRef": "refs/heads/feature/start", "baseCommit": commit},
                  {"baseRef": 42}, {"baseCommit": "wrong"}]
        registry = os.path.join(self.base, "state", "tide", "ticket-ids.json")
        branches = git(self.repo, "branch", "--format=%(refname)")
        for index, request in enumerate(cases):
            with self.subTest(request=request):
                card = self.card(title=f"Odmowa {index}")
                response = self.start(card, **request)
                self.assertFalse(response["ok"], response)
                self.assertEqual(self.events(card), [])
                self.assertEqual(git(self.repo, "branch", "--format=%(refname)"), branches)
                self.assertFalse(os.path.exists(registry))
        good = self.start(self.card(title="Po odmowie"))
        self.assertEqual(good["ticket"], "PT-1")

    def test_no_main_does_not_fall_back_to_head_but_explicit_feature_works(self):
        commit = self.feature()
        git(self.repo, "checkout", "--detach", "-q", commit)
        git(self.repo, "branch", "-D", "master")
        card = self.card()
        self.assertFalse(self.start(card)["ok"])
        self.assertEqual(self.events(card), [])
        self.assertFalse(self.start(card, baseRef="HEAD")["ok"])
        self.assertTrue(self.start(card, baseRef="refs/heads/feature/start")["ok"])
        self.assert_base(card, "refs/heads/feature/start", commit)

    def test_update_card_cannot_edit_base_or_version(self):
        card = self.card()
        self.assertTrue(self.start(card)["ok"])
        for key in ("baseRef", "baseBranch", "baseCommit", "baseVersion", "base-ref", "base-version"):
            response = json.loads(self.board.update_card(card, json.dumps({key: "changed"})))
            self.assertFalse(response["ok"], response)
        self.assert_base(card, "refs/heads/master", self.master)
        # Ręczna edycja pliku projekcji też nie staje się źródłem stanu.
        model = self.store.load().cards[int(card[2:])]
        model.doc.set("base-commit", "f" * 40)
        with open(model.path, "w", encoding="utf-8") as fh:
            fh.write(model.doc.text())
        self.assert_base(card, "refs/heads/master", self.master)
        self.flow.engine._project_base(card)
        self.assertEqual(self.store.load().cards[int(card[2:])].base_commit, self.master)

    def test_ref_moving_after_started_does_not_change_created_worktree(self):
        commit = self.feature()
        card = self.card()
        original = self.flow.engine.git.branch_worktree

        def moving(repo, branch, path, base):
            if branch.startswith("card/"):
                git(self.repo, "branch", "-f", "feature/start", "master")
            return original(repo, branch, path, base)

        with mock.patch.object(self.flow.engine.git, "branch_worktree", side_effect=moving):
            reply = self.start(card, baseRef="refs/heads/feature/start", baseCommit=commit)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(git(reply["cardWorktree"], "rev-parse", "HEAD").strip(), commit)
        self.assert_base(card, "refs/heads/feature/start", commit)

    def test_base_changed_fixture_in_snapshot_replays_without_another_increment(self):
        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "card-base-changed.json")
        with open(fixture, encoding="utf-8") as fh:
            cases = json.load(fh)
        for events in cases:
            card = self.card(title=f"Wersja {events[-1]['baseVersion']}")
            journal = self.flow.engine.journal
            for event in events:
                data = {k: v for k, v in event.items() if k not in ("seq", "type")}
                journal.append(card, event["type"], **data)
            version = events[-1]["baseVersion"]
            expected = events[-1]["base"]
            self.assert_base(card, expected["baseRef"], expected["baseCommit"], version)
            self.restart()
            self.flow.engine._project_base(card)
            self.flow.engine._project_base(card)
            self.assert_base(card, expected["baseRef"], expected["baseCommit"], version)
            self.assertEqual(len(self.of(card, "card.base-changed")), 1)
            # Test odczytu nie wykonuje korekty ani żadnej operacji gita.
            self.assertEqual(git(self.repo, "branch", "--format=%(refname)").strip(), "refs/heads/master")

    def test_legacy_known_and_unknown_survive_restart_and_projection(self):
        for known in (True, False):
            card = self.card(title=f"Historia {known}")
            journal = self.flow.engine.journal
            journal.append(card, "card.started", entry="now", repo=self.repo, column="todo")
            if known:
                journal.append(card, "intent", action="card-branch", key="card/legacy", base="master")
                journal.append(card, "result", action="card-branch", key="card/legacy", ok=True,
                               base=self.master, existing=False)
            self.flow.engine._project_base(card)
            self.restart()
            self.board.publish()
            shown = self.shown(card)
            self.assertEqual(shown["baseVersion"], 0)
            self.assertEqual(shown["derived"]["flow"]["base"]["status"], "known" if known else "unknown")
            if known:
                self.assert_base(card, "refs/heads/master", self.master, 0)
            else:
                self.assertEqual(shown["baseCommit"], "")
            self.assertNotIn("baseVersion", self.of(card, "card.started")[0])


class BaseReplayTest(unittest.TestCase):
    def test_base_changed_fixtures_increment_once_and_preserve_initial_base(self):
        fixture = os.path.join(os.path.dirname(__file__), "fixtures", "card-base-changed.json")
        with open(fixture, encoding="utf-8") as fh:
            cases = json.load(fh)
        for events in cases:
            with self.subTest(events=events):
                initial = fold(events[:1])
                once, replay = fold(events), fold(events + [events[-1]])
                self.assertEqual(once.base_version, initial.base_version + 1)
                self.assertEqual(replay.base_version, once.base_version)
                self.assertEqual(base_of(once), base_of(replay))
                self.assertEqual(once.initial_base, initial.initial_base)
                self.assertEqual(derived(replay, {"column": "todo"})["flow"]["baseVersion"], once.base_version)
                fresh = fold(events + [{"type": "card.started", "seq": 3, "baseVersion": 1,
                                        **events[-1]["base"]}])
                self.assertEqual(fresh.base_version, 1)

    def test_unreliable_legacy_never_uses_sha_or_head_as_ref(self):
        for ref, existing in (("HEAD", False), ("a" * 40, False), ("refs/tags/v1", False),
                              ("master", True), ("master", None), ("master~1", False)):
            st = fold([{"seq": 1, "type": "card.started"},
                       {"seq": 2, "type": "intent", "action": "card-branch", "key": "x", "base": ref},
                       {"seq": 3, "type": "result", "action": "card-branch", "key": "x", "ok": True,
                        "existing": existing, "base": "b" * 40}])
            self.assertEqual(base_of(st)["status"], "unknown", (ref, existing))
            self.assertEqual(st.base_version, 0)
