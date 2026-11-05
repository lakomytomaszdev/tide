"""PT-211: przypięta rewizja w istniejących wejściach i kontynuacjach planowania."""
import json
import os
from unittest import mock

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_base import StartBaseSetup
from .test_cardflow_dbus import git
from . import test_cardflow_full_planning as full
from . import test_cardflow_import_plan as imported


class PlanningBaseTest(FlowInProcess):
    feature = StartBaseSetup.feature
    assert_base = StartBaseSetup.assert_base
    source = imported.PlanImportTest.source
    _write_proposal = full.FullPlanningTest._write_proposal

    def approve(self, card):
        box = []
        self.flow.approve_plan(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        reply = json.loads(box[0])
        self.assertTrue(reply["ok"], reply)
        return reply

    def assert_feature(self, path):
        with open(os.path.join(path, "feature.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "tylko feature\n")

    def test_three_entries_keep_feature_after_ref_moves_restart_and_approval(self):
        self._three_entries("refs/heads/feature/start")

    def test_three_remote_entries_keep_sha_after_ref_deletion_restart_and_approval(self):
        self._three_entries("refs/remotes/team/feature/start")

    def _three_entries(self, ref):
        commit = self.feature()
        for entry, mode in (("planning", "light"), ("planning", "full"), ("import", "")):
            with self.subTest(entry=entry, mode=mode, ref=ref):
                git(self.repo, "branch", "-f", "feature/start", commit)
                git(self.repo, "update-ref", ref, commit)
                card = self.card(title=f"Baza {entry} {mode}", description="ATRAPA: planowanie")
                request = dict(entry=entry, mode=mode, baseRef=ref,
                               baseCommit=commit, cardBranch=f"card/{card.lower()}-chosen")
                terminal = full._Terminal()
                self.flow.engine.terminal = terminal
                source = self.source(card) if entry == "import" else ""
                if source:
                    request["planPath"] = source
                    with open(source, "rb") as fh:
                        original = fh.read()
                with mock.patch.object(self.store, "project_card_base", side_effect=OSError("projekcja")):
                    reply = self.start(card, **request)
                self.assertTrue(reply["ok"], reply)
                self.board.publish()
                self.assertEqual(self.shown(card)["baseRef"], ref)
                self.assertEqual(self.shown(card)["baseCommit"], commit)
                git(self.repo, "branch", "-f", "feature/start", "master")
                git(self.repo, "update-ref", "-d", ref)
                if mode == "full":
                    step = self.of(card, "step.started")[0]
                    self.assertEqual(len(terminal.opened), 1)
                    self.assert_feature(step["worktree"])
                    self._write_proposal(card, step)
                self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
                review = next(s for s in self.of(card, "step.started") if s["kind"] == "plan-review")
                self.assertEqual(git(review["worktree"], "rev-parse", "HEAD").strip(), commit)
                self.assertEqual(review["branch"], request["cardBranch"])
                self.assert_feature(review["worktree"])
                self.restart()
                self.board.publish()
                self.assertEqual(self.shown(card)["baseRef"], ref)
                self.assertEqual(self.shown(card)["baseCommit"], commit)
                self.approve(card)
                self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=40), self.events(card))
                tickets = [s for s in self.of(card, "step.started") if s["kind"] == "ticket"]
                self.assertTrue(tickets)
                for step in tickets:
                    self.assertEqual(git(self.repo, "show", step["branch"] + ":feature.txt"), "tylko feature\n")
                    self.assertEqual(git(self.repo, "merge-base", commit, step["branch"]).strip(), commit)
                self.assertEqual(len(self.of(card, "card.started")), 1)
                self.assertEqual(len(self.results(card, "card-branch")), 1)
                if source:
                    self.assertEqual(self.of(card, "plan.approved")[0]["map"], {"PT-7": "PT-7", "PT-8": "PT-8"})
                    with open(source, "rb") as fh:
                        self.assertEqual(fh.read(), original)

    def test_restart_between_durable_start_and_worktree_uses_original_sha_and_branch(self):
        commit = self.feature()
        card = self.card(description="ATRAPA: planowanie")
        self.flow.stop()
        with mock.patch.object(self.flow.engine, "_planning_launch", side_effect=OSError("restart")):
            with self.assertRaises(OSError):
                self.flow.engine.start(card, {"entry": "planning", "mode": "light", "executor": "Sonnet",
                                             "baseRef": "refs/heads/feature/start", "baseCommit": commit,
                                             "cardBranch": "card/pinned-before-launch"})
        git(self.repo, "branch", "-D", "feature/start")
        self.restart()
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
        step = self.of(card, "step.started")[0]
        self.assertEqual(step["branch"], "card/pinned-before-launch")
        self.assertEqual(git(step["worktree"], "rev-parse", "HEAD").strip(), commit)
        self.assert_feature(step["worktree"])
        self.assertEqual(len(self.of(card, "card.started")), 1)
        self.assertEqual(self.results(card, "reserve"), [])

    def test_retry_and_answer_after_ref_deletion_keep_planning_worktree(self):
        commit = self.feature()
        card = self.card(description="ATRAPA: plan-question")
        self.assertTrue(self.start(card, entry="planning", mode="light",
                                   baseRef="refs/heads/feature/start", baseCommit=commit)["ok"])
        self.wait_column(card, "waiting")
        initial = self.of(card, "step.started")[0]
        git(self.repo, "branch", "-D", "feature/start")
        self.restart()
        box = []
        self.flow.resolve_step(card, json.dumps({"choice": "answer", "answer": "Pierwszy"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
        for step in self.of(card, "step.started"):
            self.assertEqual(step["worktree"], initial["worktree"])
            self.assert_feature(step["worktree"])
        self.assert_base(card, "refs/heads/feature/start", commit)
        self.assertEqual(len(self.of(card, "card.started")), 1)

    def test_next_imported_ticket_sees_merged_blocker_from_current_card(self):
        commit = self.feature()
        card = self.card()
        source = self.source(card)
        with open(source, encoding="utf-8") as fh:
            text = fh.read().replace("| gotowe |", "| todo |")
        with open(source, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.assertTrue(self.start(card, entry="import", planPath=source,
                                   baseRef="refs/heads/feature/start", baseCommit=commit)["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        git(self.repo, "branch", "-D", "feature/start")
        self.approve(card)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=40), self.events(card))
        starts = [s for s in self.of(card, "step.started") if s["kind"] == "ticket"]
        self.assertEqual([s["ticket"] for s in starts], ["PT-7", "PT-8"])
        merge = next(r for r in self.results(card, "merge") if r["key"] == "PT-7")
        self.assertTrue(merge["ok"])
        self.assertLess(merge["seq"], starts[1]["seq"])
        first_tip = git(self.repo, "rev-parse", starts[0]["branch"]).strip()
        self.assertEqual(git(self.repo, "merge-base", first_tip, starts[1]["branch"]).strip(), first_tip)
        self.assertEqual(git(self.repo, "show", starts[1]["branch"] + ":feature.txt"), "tylko feature\n")
        with open(source, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), text)

    def test_automatic_retry_keeps_feature_after_base_moves(self):
        commit = self.feature()
        card = self.card(description="ATRAPA: plan-error")
        self.assertTrue(self.start(card, entry="planning", mode="light",
                                   baseRef="refs/heads/feature/start", baseCommit=commit)["ok"])
        git(self.repo, "branch", "-f", "feature/start", "master")
        self.wait_column(card, "waiting")
        steps = self.of(card, "step.started")
        self.assertEqual(len(steps), 2)
        self.assertEqual(len(self.of(card, "step.retry")), 1)
        for step in steps:
            self.assertEqual(git(step["worktree"], "rev-parse", "HEAD").strip(), commit)
            self.assert_feature(step["worktree"])
        self.assertEqual(len(self.of(card, "card.started")), 1)

    def test_full_planning_selected_base_does_not_change_extra_project(self):
        commit = self.feature()
        extra = os.path.join(self.base, "ws", "extra")
        os.makedirs(extra)
        git(extra, "init", "-q")
        with open(os.path.join(extra, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("Kontekst dodatkowego projektu")
        git(extra, "add", "README.md")
        git(extra, "commit", "-qm", "Initial extra context")
        tip = git(extra, "rev-parse", "HEAD")
        self.store.upsert_project({"slug": "extra", "name": "Dodatkowy", "repo": extra})
        card = self.card()
        self.store.update_card(card, {"projects": ["extra"]})
        terminal = full._Terminal()
        self.flow.engine.terminal = terminal
        reply = self.start(card, entry="planning", mode="full",
                           baseRef="refs/heads/feature/start", baseCommit=commit)
        self.assertTrue(reply["ok"], reply)
        self.assert_feature(reply["cardWorktree"])
        with open(terminal.opened[0].task_file, encoding="utf-8") as fh:
            task = json.load(fh)
        self.assertIn(extra, task["argv"])
        self.assertIn("Kontekst dodatkowego projektu", task["argv"][-1])
        self.assertIn(f"Edit(/{os.path.realpath(extra)}/**)", task["argv"])
        self.assertEqual(git(extra, "rev-parse", "HEAD"), tip)
        self.assertEqual(git(extra, "symbolic-ref", "--short", "HEAD").strip(), "master")
        self.assertFalse(os.path.exists(os.path.join(extra, "feature.txt")))
