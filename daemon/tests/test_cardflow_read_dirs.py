"""PT-246: ustawienia trafiają do rzeczywistych ścieżek startu, wznowienia i wejścia do Konsoli."""
import json
import os
import unittest
from unittest import mock

from .test_cardflow import FlowInProcess
from . import test_cardflow_enter_unit as enter_tests
from .privatebus import wait_until
from tide_daemon.cardflow.engine import FlowError


class ReadDirsLaunchTest(FlowInProcess):
    def configure(self, dirs):
        result = json.loads(self.board.upsert_project(json.dumps({"slug": "proj", "readDirs": dirs})))
        self.assertTrue(result["ok"], result)

    def filesystem(self, step):
        with open(os.path.join(step["stepDir"], "in", "settings.json")) as fh:
            return json.load(fh)["sandbox"]["filesystem"]

    def test_background_and_resume_use_current_list_without_mutating_first_profile(self):
        data = os.path.join(self.base, "home", "data")
        newer = os.path.join(self.base, "home", "newer")
        os.makedirs(data)
        os.makedirs(newer)
        self.configure([data])
        card = self.card(description="ATRAPA: pytanie-bez-pracy")
        self.assertTrue(self.start(card)["ok"])
        self.wait_column(card, "waiting")
        first = self.started(card)
        self.assertIn(data, self.filesystem(first)["denyWrite"])
        self.configure([newer])
        box = []
        self.flow.resolve_step(card, json.dumps({"step": first["step"], "choice": "answer", "answer": "ISO"}),
                               box.append)
        self.assertTrue(wait_until(lambda: box, timeout=15))
        self.assertTrue(json.loads(box[0])["ok"], box)
        self.assertTrue(wait_until(lambda: len(self.of(card, "step.started")) > 1, timeout=15), self.events(card))
        resumed = self.of(card, "step.started")[1]
        self.assertEqual(resumed["parentStep"], first["step"])
        self.assertIn(newer, self.filesystem(resumed)["denyWrite"])
        self.assertNotIn(data, self.filesystem(resumed)["allowRead"])
        self.assertIn(data, self.filesystem(first)["denyWrite"])

    def test_vanished_data_refuses_actual_start(self):
        data = os.path.join(self.base, "home", "data")
        os.makedirs(data)
        self.configure([data])
        os.rmdir(data)
        card = self.card(description="ATRAPA: pytanie-bez-pracy")
        reply = self.start(card)
        self.assertFalse(reply["ok"], reply)
        self.assertEqual(reply["error"], "step-start", reply)
        self.assertIn("nie istnieje", reply["message"])
        self.assertEqual(self.of(card, "step.started"), [])

    def test_full_planning_console_uses_same_readonly_profile_for_both_programs(self):
        data = os.path.join(self.base, "home", "data")
        os.makedirs(data)
        self.configure([data])
        terminal = mock.Mock()
        terminal.available.return_value = True
        terminal.open.return_value = "tab"
        self.flow.engine.terminal = terminal
        for executor, provider in (("Sonnet", "anthropic"), ("Codex mocny", "openai")):
            card = self.card(description="Zaplanuj eksport")
            reply = self.start(card, entry="planning", mode="full", executor=executor,
                               preferredProvider=provider)
            self.assertTrue(reply["ok"], reply)
            step = self.started(card)
            self.assertTrue(step["readDirsProfile"])
            argv = terminal.open.call_args.args[0].argv
            if provider == "anthropic":
                self.assertIn(data, self.filesystem(step)["denyWrite"])
                self.assertIn("--settings", argv)
            else:
                self.assertNotIn("--add-dir", argv)
                self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", argv)
                self.assertNotIn(data, next(a for a in argv if a.startswith(
                    "sandbox_workspace_write.writable_roots=")))


class ReadDirsEnterTest(unittest.TestCase):
    SID = enter_tests.EnterStepTest.SID
    setUp = enter_tests.EnterStepTest.setUp
    def test_enter_revalidates_before_stopping_or_removing_result(self):
        data = os.path.join(self.root, "data")
        os.makedirs(data)
        project = self.engine.store.load().projects["proj"]
        project.read_dirs = [data]
        os.rmdir(data)
        result = os.path.join(self.step_dir, "out", "result.json")
        with open(result, "w") as fh:
            fh.write('{"result":"question","question":"x"}')
        with mock.patch.object(self.engine, "_stop_for_enter") as stop:
            with self.assertRaisesRegex(FlowError, "nie istnieje"):
                self.engine.enter("K-1", self.step_id)
        stop.assert_not_called()
        self.assertTrue(os.path.isfile(result))
        self.assertEqual(self.order, [])

    def test_enter_uses_restricted_builder_and_keeps_it_after_directory_removal(self):
        data = os.path.join(self.root, "data")
        os.makedirs(data)
        self.engine.store.load().projects["proj"].read_dirs = [data]
        command = mock.Mock(argv=["/bin/claude", "restricted"], task_fields=lambda: {})
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True), \
                mock.patch.object(self.engine, "_restricted_console", return_value=command) as build:
            self.engine.enter("K-1", self.step_id)
        self.assertIn(data, build.call_args.args[7])
        self.assertEqual(self.terminal.plan.argv, command.argv)
        self.engine.store.load().projects["proj"].read_dirs = []
        self.assertTrue(self.engine._used_read_profile("K-1", self.worktree))
