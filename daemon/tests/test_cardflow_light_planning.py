"""PT-79: Planowanie Lekkie przez StartCard bez Konsoli."""
import json
import os

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import FlowCase


class LightPlanningTest(FlowInProcess):
    def test_light_planning_uses_selected_executor_and_waits_for_approval(self):
        card = self.card(description="Dodaj eksport. ATRAPA: planowanie")
        reply = self.start(card, entry="planning", mode="light", executor="Opus", preferredProvider="mixed")
        self.assertTrue(reply["ok"], reply)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
        shown = self.wait_column(card, "waiting")
        self.assertEqual(shown["derived"]["flow"]["plan"]["status"], "pending")
        self.assertEqual(self.results(card, "reserve"), [])
        step = self.of(card, "step.started")[0]
        self.assertEqual((step["executor"], step["kind"]), ("Opus", "planning"))
        with open(os.path.join(step["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            prompt = json.load(fh)["prompt"]
        for text in ("to-spec", "Problem Statement", "Testing Decisions", "to-tickets", "pionow", "Dla recenzenta",
                     "mieszany", "Zasady przydziału", "Sonnet", "standard", "Nie publikuj", "GitHub Issues"):
            self.assertIn(text, prompt)
        self.assertFalse(os.path.isfile(self.calls), "planowanie nie otwiera Konsoli")

    def test_invalid_plan_is_repaired_in_background(self):
        card = self.card(description="ATRAPA: plan-repair")
        self.assertTrue(self.start(card, entry="planning", mode="light")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
        self.assertEqual([e["valid"] for e in self.of(card, "plan.checked")], [False, True])
        self.assertEqual([e["kind"] for e in self.of(card, "step.started")], ["planning", "plan-fix", "plan-review"])

    def test_two_failed_repairs_wait_for_user_without_retry_loop(self):
        card = self.card(description="ATRAPA: plan-bad")
        self.assertTrue(self.start(card, entry="planning", mode="light")["ok"])
        self.assertTrue(wait_until(lambda: len(self.of(card, "plan.checked")) == 3, timeout=20), self.events(card))
        shown = self.wait_column(card, "waiting")
        self.assertIn("dwóch poprawach", shown["derived"]["question"])
        self.assertEqual(self.of(card, "plan.proposed"), [])
        self.assertEqual(len(self.of(card, "step.started")), 3)
        self.restart()
        wait_until(lambda: False, timeout=0.4)
        self.assertEqual(len(self.of(card, "step.started")), 3)

    def test_assignment_rules_are_common_and_overridden_per_project(self):
        self.store.upsert_project({"globalAssignmentRules": "mocny: reguła wspólna"})
        snap = self.store.snapshot()
        self.assertEqual(snap["assignmentRules"], "mocny: reguła wspólna")
        card = self.card(description="ATRAPA: planowanie")
        self.assertTrue(self.start(card, entry="planning", mode="light")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        first = self.of(card, "step.started")[0]
        with open(os.path.join(first["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            self.assertIn("mocny: reguła wspólna", json.load(fh)["prompt"])
        self.store.upsert_project({"slug": "proj", "assignmentRules": "lekki: reguła projektu"})
        self.assertEqual(self.store.snapshot()["projects"][0]["assignmentRules"], "lekki: reguła projektu")
        other = self.card(description="ATRAPA: planowanie")
        self.assertTrue(self.start(other, entry="planning", mode="light")["ok"])
        self.assertTrue(wait_until(lambda: self.of(other, "plan.reviewed"), timeout=20))
        second = self.of(other, "step.started")[0]
        with open(os.path.join(second["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            prompt = json.load(fh)["prompt"]
        self.assertIn("lekki: reguła projektu", prompt)
        self.assertNotIn("mocny: reguła wspólna", prompt)

    def test_approved_light_plan_runs_ticket_through_review_and_merge(self):
        card = self.card(description="ATRAPA: planowanie")
        self.assertTrue(self.start(card, entry="planning", mode="light")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        box = []
        self.flow.approve_plan(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.finished"), timeout=30), self.events(card))
        self.wait_column(card, "review")
        self.assertEqual([s["kind"] for s in self.of(card, "step.started")], ["planning", "plan-review", "ticket", "review"])

    def test_restart_after_request_before_launch_still_plans_once(self):
        from unittest import mock
        self.flow.stop()
        card = self.card(description="ATRAPA: planowanie")
        with mock.patch.object(self.flow.engine, "_planning_launch", side_effect=OSError("restart przed krokiem")):
            with self.assertRaises(OSError):
                self.flow.engine.start(card, {"entry": "planning", "mode": "light", "executor": "Sonnet"})
        self.restart()
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))
        self.assertEqual(len(self.of(card, "step.started")), 2)

    def test_planner_question_is_visible_and_answer_resumes_planning(self):
        card = self.card(description="ATRAPA: plan-question")
        self.assertTrue(self.start(card, entry="planning", mode="light")["ok"])
        shown = self.wait_column(card, "waiting")
        self.assertEqual(shown["derived"]["flow"]["action"], "answer")
        self.assertEqual(shown["derived"]["flow"]["question"], "Który wariant wybrać?")
        box = []
        self.flow.resolve_step(card, json.dumps({"choice": "answer", "answer": "Pierwszy"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20), self.events(card))

    def test_planner_crash_is_retried_once_then_waits_for_user(self):
        card = self.card(description="ATRAPA: plan-error")
        self.assertTrue(self.start(card, entry="planning", mode="light")["ok"])
        shown = self.wait_column(card, "waiting")
        self.assertEqual(shown["derived"]["flow"]["action"], "error")
        self.assertEqual(len(self.of(card, "step.started")), 2)
        self.assertEqual(len(self.of(card, "step.retry")), 1)
        self.assertEqual(self.of(card, "plan.proposed"), [])

    def test_failed_planner_can_be_changed_without_a_ticket_reviewer(self):
        card = self.card(description="ATRAPA: plan-error")
        self.assertTrue(self.start(card, entry="planning", mode="light")["ok"])
        self.wait_column(card, "waiting")
        box = []
        self.flow.resolve_step(card, json.dumps({"choice": "change-executor", "executor": "Opus"}), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: any(e["executor"] == "Opus" for e in self.of(card, "step.started")), timeout=10), self.events(card))

    def test_planner_receives_images_and_extra_repository_read_only(self):
        from .test_cardflow_dbus import git
        extra = os.path.join(self.base, "ws", "extra")
        os.makedirs(extra)
        git(extra, "init", "-q")
        self.store.upsert_project({"slug": "extra", "name": "Dodatkowy", "repo": extra})
        card = self.card(description="ATRAPA: planowanie")
        self.store.update_card(card, {"projects": ["extra"]})
        image = os.path.join(self.base, "zrzut.png")
        with open(image, "wb") as fh:
            fh.write(b"PNG")
        self.assertTrue(self.start(card, entry="planning", mode="light", images=[image])["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        step = self.of(card, "step.started")[0]
        with open(os.path.join(step["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            prompt = json.load(fh)["prompt"]
        self.assertIn(extra, prompt)
        self.assertIn("zrzut.png", prompt)
        with open(os.path.join(step["stepDir"], "in", "settings.json"), encoding="utf-8") as fh:
            filesystem = json.load(fh)["sandbox"]["filesystem"]
        self.assertIn(extra, filesystem["allowRead"])
        self.assertNotIn(extra, filesystem["allowWrite"])
        self.assertEqual(filesystem["denyRead"], ["~"])

    def test_initial_board_persists_class_based_rules_and_clearing_override_restores_them(self):
        from tide_daemon.board.assignment import KEY, DEFAULT_RULES
        self.assertEqual(json.loads(self.store.load().meta.get(KEY)), DEFAULT_RULES)
        self.store.upsert_project({"globalAssignmentRules": "standard: wspólne\\nlekki: proste"})
        self.store.upsert_project({"slug": "proj", "assignmentRules": "mocny: nadpisane"})
        self.store.upsert_project({"slug": "proj", "assignmentRules": ""})
        card = self.card(description="ATRAPA: planowanie")
        self.assertTrue(self.start(card, entry="planning", mode="light")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=20))
        self.assertEqual(self.of(card, "card.started")[0]["assignmentRules"], "standard: wspólne\\nlekki: proste")


class LightPlanningDBusTest(FlowCase):
    def test_light_plan_and_assignment_rules_through_private_board_bus(self):
        self.assertTrue(self.call("UpsertProject", json.dumps({"globalAssignmentRules": "standard: wspólna"}))["ok"])
        self.assertTrue(self.call("UpsertProject", json.dumps({"slug": "proj", "assignmentRules": "mocny: projektowa"}))["ok"])
        card = self.card(description="ATRAPA: planowanie")
        reply = self.call("StartCard", card, json.dumps({"entry": "planning", "mode": "light", "executor": "Codex mocny",
                                                        "preferredProvider": "openai"}))
        self.assertTrue(reply["ok"], reply)
        shown = self.wait_column(card, "waiting")
        self.assertEqual(shown["derived"]["flow"]["plan"]["status"], "pending")
        self.assertEqual(self.results(card, "reserve"), [])
        step = self.of(card, "step.started")[0]
        self.assertEqual(step["program"], "codex")
        with open(os.path.join(step["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            prompt = json.load(fh)["prompt"]
        self.assertIn("mocny: projektowa", prompt)
        self.assertIn("Preferowany dostawca: openai", prompt)
        self.assertFalse(os.path.isfile(self.calls))
