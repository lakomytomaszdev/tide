"""Projekty z `ai-docs: tide` (repozytoria klientów): commity Tide bez numerów kart i ticketów (09.11, K-89)."""
import os
import subprocess
import tempfile
import shutil
import unittest
from unittest import mock

from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.gitops import FlowGit


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout


class MergeMessageTest(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp(prefix="tide-msg-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        git(self.repo, "init", "-q", "-b", "master")
        with open(os.path.join(self.repo, "a.txt"), "w") as fh:
            fh.write("a\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm",
            "fix(assistant): amounts accept exponent notation")
        self.tip = git(self.repo, "rev-parse", "HEAD").strip()
        self.engine = FlowEngine.__new__(FlowEngine)
        self.engine.git = FlowGit()

    def message(self, board_docs):
        with mock.patch.object(self.engine, "_plan_on_board", return_value=board_docs):
            return self.engine._merge_message("K-89", self.repo, self.tip, "Merge ticket DLE-1 into card K-89")

    def test_client_repository_gets_the_ticket_subject_without_identifiers(self):
        self.assertEqual(self.message(True), "Merge: fix(assistant): amounts accept exponent notation")

    def test_repository_with_its_own_plans_keeps_the_identifiers(self):
        self.assertEqual(self.message(False), "Merge ticket DLE-1 into card K-89")

    def test_unreadable_tip_falls_back_to_a_neutral_message(self):
        with mock.patch.object(self.engine, "_plan_on_board", return_value=True):
            self.assertEqual(self.engine._merge_message("K-89", self.repo, "0" * 40, "Merge ticket DLE-1 into card K-89"),
                             "Merge reviewed work")


class ConflictMessageTest(unittest.TestCase):
    def card(self, merge_message=""):
        from tide_daemon.cardflow.handoff import conflict_card
        return conflict_card(card_id="K-89", title="t", description="d", ticket="DLE-1", card_branch="card/k-89-x",
                             ticket_branch="ticket/dle-1-x", worktree="/w", out_dir="/o", error="konflikt",
                             merge_message=merge_message)

    def test_conflict_step_gets_the_neutral_title_for_a_client_repository(self):
        self.assertIn('Tytuł commita scalenia ustaw dokładnie na: "Merge: fix(assistant): x"', self.card("Merge: fix(assistant): x"))

    def test_conflict_step_of_an_ordinary_project_has_no_title_instruction(self):
        self.assertNotIn("Tytuł commita scalenia", self.card())


if __name__ == "__main__":
    unittest.main()
