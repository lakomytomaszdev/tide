"""PT-201: rzeczywiste metody /Board, Snapshot i restart usługi na prywatnej magistrali."""
import json
import os
from pathlib import Path

from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import FlowCase, git
from .test_cardflow_merge_preview import fingerprint
from .privatebus import wait_until


class BaseChangeScenario:
    def test_public_empty_generation_switch_and_restart(self):
        git(self.repo, "checkout", "-qb", "generation-feature", env=self.git_env)
        Path(self.repo, "generation-feature.txt").write_text("unikalna zawartość feature\n")
        git(self.repo, "add", "generation-feature.txt", env=self.git_env)
        git(self.repo, "commit", "-qm", "feature content", env=self.git_env)
        feature = git(self.repo, "rev-parse", "HEAD").strip()
        git(self.repo, "checkout", "-q", "master", env=self.git_env)
        card = self.card(description="ATRAPA: pytanie-bez-pracy")
        started = self.start(card)
        self.assertTrue(started["ok"], started)
        self.wait_column(card, "waiting")
        asked = self.call("RequestCardBaseChange", card, json.dumps({
            "baseRef": "refs/heads/generation-feature", "baseCommit": feature}))
        preview = asked["preview"]
        self.assertTrue(preview["canChange"], preview)
        self.assertEqual(len(preview["branches"]), 2)
        for row in preview["branches"]:
            self.assertEqual((row["commit"], row["startCommit"], row["commits"]), (self.master, self.master, []))
        self.assertFalse(any(p["active"] for p in preview["processes"]))
        request = json.dumps({"choice": "change-base", "step": preview["decisionStep"],
                              "baseRef": "refs/heads/generation-feature", "baseCommit": feature,
                              "previewId": preview["previewId"]})
        changed = self.call("ResolveCardStep", card, request)
        self.assertTrue(changed["changed"], changed)
        self.assertEqual(changed["baseVersion"], 2)
        generation = changed["generation"]
        for row in generation:
            self.assertEqual(git(row["oldWorktree"], "rev-parse", "HEAD").strip(), self.master)
            self.assertEqual(git(self.repo, "rev-parse", row["oldBranch"]).strip(), self.master)
            self.assertEqual(git(row["worktree"], "rev-parse", "HEAD").strip(), feature)
            self.assertEqual(Path(row["worktree"], "generation-feature.txt").read_text(), "unikalna zawartość feature\n")
            self.assertFalse(Path(row["oldWorktree"], "generation-feature.txt").exists())
        self.restart()
        shown = self.wait_column(card, "waiting")
        flow = shown["derived"]["flow"]
        self.assertEqual((shown["baseCommit"], shown["baseVersion"]), (feature, 2))
        self.assertEqual(flow["initialBase"]["baseCommit"], self.master)
        self.assertEqual(flow["ticket"], started["ticket"])
        self.assertFalse(flow["baseValidationRequired"])
        self.assertEqual(flow["generation"], generation)
        self.assertTrue(all(not s["canEnter"] for s in flow["steps"] if s["historical"]))
        self.assertNotEqual(flow["cardBranch"], started["cardBranch"])
        self.assertNotEqual(flow["branch"], started["branch"])
        self.assertTrue(self.call("ResolveCardStep", card, request)["changed"])
        self.assertEqual(len(self.of(card, "card.base-changed")), 1)
        self.assertEqual(len(self.results(card, "change-base")), 1)

    def test_user_preview_refusal_and_restart_preserve_base_question_and_files(self):
        card = self.card(description="ATRAPA: pytanie-bez-pracy")
        started = self.start(card)
        self.assertTrue(started["ok"], started)
        self.wait_column(card, "waiting")
        self.assertEqual(git(self.repo, "rev-parse", started["branch"]).strip(), self.master,
                         "kwalifikowany scenariusz wymaga pytania przed pierwszym commitem wkładu")
        git(self.repo, "branch", "correct-base", env=self.git_env)
        before = fingerprint(self.repo)
        request = json.dumps({"baseRef": "refs/heads/correct-base", "baseCommit": self.master})
        preview = self.call("PreviewCardBaseChange", card, request)
        self.assertTrue(preview["ok"], preview)
        self.assertTrue(preview["canChange"], preview)
        asked = self.call("RequestCardBaseChange", card, request)
        self.assertTrue(asked["ok"], asked)
        self.assertEqual(fingerprint(self.repo), before)
        self.restart()
        shown = self.shown(card)
        self.assertEqual(shown["baseCommit"], self.master)
        self.assertTrue(shown["derived"]["flow"]["baseDecisionPending"])
        self.assertEqual(len(shown["derived"]["flow"]["decisions"]), 2, "pytanie kroku pozostaje")
        # Każdy commit dokumentów oznacza odmowę, także po wybraniu korekty przez użytkownika.
        plan = Path(started["cardWorktree"], "docs/agents/plans/accepted.md")
        plan.parent.mkdir(parents=True)
        plan.write_text("# Zatwierdzony plan\n", encoding="utf-8")
        git(started["cardWorktree"], "add", "docs/agents/plans/accepted.md", env=self.git_env)
        git(started["cardWorktree"], "commit", "-qm", "docs(plan): saved plan", env=self.git_env)
        before = fingerprint(self.repo)
        refused = self.call("PreviewCardBaseChange", card, "{}")
        self.assertTrue(refused["ok"], refused)
        self.assertFalse(refused["canChange"])
        self.assertIn("contribution", {r["reason"] for r in refused["blockedReasons"]})
        self.assertTrue(refused["followup"]["available"])
        self.assertIn("Zachowaj bazę, aby kontynuować", refused["instruction"])
        self.assertEqual(fingerprint(self.repo), before)
        self.assertEqual(self.shown(card)["baseCommit"], self.master)

    def test_question_after_work_is_refused_and_preserved(self):
        card = self.card(description="ATRAPA: pytanie")
        started = self.start(card)
        self.assertTrue(started["ok"], started)
        shown = self.wait_column(card, "waiting")
        tip = git(self.repo, "rev-parse", started["branch"]).strip()
        self.assertNotEqual(tip, self.master, "dotychczasowa atrapa pytania wnosi commit")
        before = fingerprint(self.repo)
        refused = self.call("PreviewCardBaseChange", card, json.dumps({"baseRef": "refs/heads/master",
                                                                     "baseCommit": self.master}))
        self.assertTrue(refused["ok"], refused)
        self.assertFalse(refused["canChange"])
        self.assertIn("contribution", {r["reason"] for r in refused["blockedReasons"]})
        self.assertIn(tip, str(refused["branches"]))
        self.assertEqual(fingerprint(self.repo), before)
        self.assertEqual(self.shown(card)["derived"]["flow"]["decisions"], shown["derived"]["flow"]["decisions"])


class BaseChangeDbusTest(BaseChangeScenario, FlowCase):
    pass


class BaseChangeInProcessTest(BaseChangeScenario, FlowInProcess):
    """Ten sam przebieg z prawdziwym programem atrapy, także bez gniazd prywatnego D-Bus."""

    def setUp(self):
        super().setUp()
        self.git_env = dict(os.environ)

    def call(self, member, *args):
        methods = {"PreviewCardBaseChange": self.board.preview_card_base_change,
                   "RequestCardBaseChange": self.board.request_card_base_change,
                   "ResolveCardStep": self.board.resolve_card_step}
        replies = []
        methods[member](*args, replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=15), member)
        return json.loads(replies[0])
