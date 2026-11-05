"""PT-201: rzeczywiste metody /Board, Snapshot i restart usługi na prywatnej magistrali."""
import json

from .test_cardflow_dbus import FlowCase, git
from .test_cardflow_merge_preview import fingerprint


class BaseChangeDbusTest(FlowCase):
    def test_user_preview_refusal_and_restart_preserve_base_question_and_files(self):
        card = self.card(description="ATRAPA: pytanie")
        started = self.start(card)
        self.assertTrue(started["ok"], started)
        self.wait_column(card, "waiting")
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
        git(started["cardWorktree"], "commit", "--allow-empty", "-qm", "docs(plan): saved plan", env=self.git_env)
        before = fingerprint(self.repo)
        refused = self.call("PreviewCardBaseChange", card, "{}")
        self.assertTrue(refused["ok"], refused)
        self.assertFalse(refused["canChange"])
        self.assertIn("contribution", {r["reason"] for r in refused["blockedReasons"]})
        self.assertTrue(refused["followup"]["available"])
        self.assertIn("Zachowaj bazę, aby kontynuować", refused["instruction"])
        self.assertEqual(fingerprint(self.repo), before)
        self.assertEqual(self.shown(card)["baseCommit"], self.master)
