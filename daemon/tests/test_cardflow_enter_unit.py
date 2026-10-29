"""Kontrakt wejścia w krok: sesja, plik wyniku i zamknięcie Konsoli."""
import json
import os
import tempfile
import unittest
from unittest import mock

from tide_daemon.cardflow import state, execution
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.journal import Journal
from tide_daemon.tasks import agentcmd, launch
from tide_daemon.tasks.core import TaskFiles


class ResumeCommandTest(unittest.TestCase):
    def test_shared_command_builder_resumes_both_tools(self):
        sid = "00000000-0000-4000-8000-000000000067"
        self.assertEqual(agentcmd.resume_argv("claude", "/bin/claude", sid),
                         ["/bin/claude", "--resume", sid])
        self.assertEqual(agentcmd.resume_argv("codex", "/bin/codex", sid),
                         ["/bin/codex", "resume", sid])

    def test_invalid_session_id_never_reaches_command(self):
        with self.assertRaises(agentcmd.StepError):
            agentcmd.resume_argv("codex", "codex", "--help")


class InteractiveStateTest(unittest.TestCase):
    def test_entered_step_stays_running_and_result_is_from_finished_event(self):
        events = [
            {"type": "card.started", "executor": "Sonnet", "program": "claude", "check": "auto"},
            {"type": "step.started", "step": "K-1-PT-1-1", "ticket": "PT-1", "kind": "ticket",
             "worktree": "/tmp/wt", "stepDir": "/tmp/wt.step"},
            {"type": "step.entered", "step": "K-1-PT-1-1", "session": "id", "closeFile": "/tmp/closed"},
        ]
        st = state.fold(events)
        self.assertEqual(len(st.running()), 1)
        self.assertEqual(st.running()[0]["mode"], "interactive")
        self.assertEqual(st.running()[0]["session"], "id")
        self.assertEqual(state.derived(st, {"column": "todo", "check": "auto"})["column"], "progress")
        events.append({"type": "step.finished", "step": "K-1-PT-1-1", "result": "stopped",
                       "reason": "interactive-closed", "question": "Wznowić czy porzucić?"})
        st = state.fold(events)
        self.assertEqual(st.running(), [])
        self.assertEqual(state.derived(st, {"column": "todo", "check": "auto"})["column"], "waiting")


class CloseMarkerTest(unittest.TestCase):
    def test_close_marker_is_atomic(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "closed")
            launch.write_closed(path)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["closed"], True)

    def test_launcher_marks_close_after_agent_and_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "closed")
            order = []
            with mock.patch.object(launch, "run_agent", side_effect=lambda *_: order.append("agent")), \
                    mock.patch("subprocess.call", side_effect=lambda *_: order.append("shell")):
                launch.run_flow({"argv": ["/bin/claude", "--resume", "id"], "closedFile": path})
            self.assertEqual(order, ["agent", "shell"])
            self.assertTrue(os.path.isfile(path))


class _Terminal:
    def __init__(self, order):
        self.order = order
        self.launchers = mock.Mock()
        self.launchers.agents = {"claude": "/bin/claude", "codex": "/bin/codex"}
        self.launchers.codex_for.return_value = "/snap/bin/codex"

    def available(self):
        return True

    def open(self, plan):
        self.order.append("open")
        self.plan = plan
        return "tab"


class EnterStepTest(unittest.TestCase):
    SID = "00000000-0000-4000-8000-000000000067"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.worktree = os.path.join(self.root, "worktree")
        os.makedirs(self.worktree)
        self.step_dir = self.worktree + ".step"
        os.makedirs(os.path.join(self.step_dir, "out"))
        self.order = []
        self.terminal = _Terminal(self.order)
        store = mock.Mock(root=os.path.join(self.root, "board"))
        self.engine = FlowEngine(store, journal=Journal(store.root, mirror=os.path.join(self.root, "mirror")),
                                 terminal=self.terminal, task_files=TaskFiles(os.path.join(self.root, "tasks")))
        self.step_id = "K-1-PT-1-1"
        self.engine.journal.append("K-1", "card.started", executor="Sonnet", program="claude", repo=self.root)
        self.engine.journal.append("K-1", "step.started", step=self.step_id, ticket="PT-1", kind="ticket",
                                   executor="Sonnet", program="claude", session=self.SID, worktree=self.worktree,
                                   stepDir=self.step_dir, branch="ticket/pt-1", repo=self.root)

    def test_running_step_is_stopped_before_console_opens_and_result_is_used(self):
        def stop(*_):
            self.order.append("stop")
            return True
        with mock.patch.object(self.engine, "_stop_for_enter", side_effect=stop):
            reply = self.engine.enter("K-1", self.step_id)
        self.assertEqual(reply["mode"], "tab")
        self.assertEqual(self.order, ["stop", "open"])
        self.assertEqual(self.terminal.plan.argv[:3], ["/bin/claude", "--resume", self.SID])
        result_path = os.path.join(self.step_dir, "out", "result.json")
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump({"result": "done", "notes": []}, fh)
        self.engine.tick()
        done = state.fold(self.engine.journal.read("K-1")).finished[self.step_id]
        self.assertEqual(done["result"], "done")
        with open(result_path, "w", encoding="utf-8") as fh:
            json.dump({"result": "question", "question": "później"}, fh)
        self.engine.tick()
        self.assertEqual(state.fold(self.engine.journal.read("K-1")).finished[self.step_id]["result"], "done")
        with self.assertRaises(FlowError) as err:
            self.engine.enter("K-1", self.step_id)
        self.assertEqual(err.exception.code, "console-open")

    def test_next_background_step_waits_for_console_close_after_result(self):
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        with open(os.path.join(current["stepDir"], "out", "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"result": "done"}, fh)
        with mock.patch.object(self.engine, "_advance") as advance:
            self.engine.tick()
            folded = state.fold(self.engine.journal.read("K-1"))
            self.assertEqual(folded.finished[self.step_id]["result"], "done")
            self.assertTrue(folded.open_consoles())
            advance.assert_not_called()  # review nie rusza w worktree zajętym przez Konsolę
            launch.write_closed(current["closeFile"])
            self.engine.tick()
            advance.assert_called_once()
        self.assertFalse(state.fold(self.engine.journal.read("K-1")).open_consoles())

    def test_second_enter_cannot_start_another_session_in_same_worktree(self):
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        with self.assertRaises(FlowError) as err:
            self.engine.enter("K-1", self.step_id)
        self.assertEqual(err.exception.code, "already-entered")
        self.assertEqual(self.order, ["open"])

    def test_close_without_result_asks_and_new_attempt_can_resume(self):
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        launch.write_closed(current["closeFile"])
        self.engine.tick()
        done = state.fold(self.engine.journal.read("K-1")).finished[self.step_id]
        self.assertEqual((done["result"], done["reason"]), ("stopped", "interactive-closed"))
        reply = self.engine.enter("K-1", self.step_id)
        self.assertEqual(reply["step"], self.step_id + "-enter-1")

    def test_abandon_resolves_close_question(self):
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        launch.write_closed(current["closeFile"])
        self.engine.tick()
        self.engine.abandon("K-1", self.step_id)
        done = state.fold(self.engine.journal.read("K-1")).finished[self.step_id]
        self.assertEqual((done["result"], done["reason"]), ("stopped", "abandoned"))

    def test_codex_session_id_is_read_from_background_event(self):
        path = os.path.join(self.step_dir, "out", "step.log")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "thread.started", "thread_id": self.SID}) + "\n")
        self.assertEqual(execution.codex_session_id(path), self.SID)

    def test_codex_from_snap_uses_source_for_matching_binary(self):
        # Katalog sesji ze snapa jest poza ~/.codex; ścieżka wybiera właściwy program.
        snap = os.path.join(self.root, "home", "snap", "codex", "123")
        sessions = os.path.join(snap, "sessions", "2026", "10", "29")
        os.makedirs(sessions)
        source = os.path.join(sessions, "rollout-2026-10-29T00-00-00-" + self.SID + ".jsonl")
        with open(source, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "session_meta", "payload": {"id": self.SID,
                                                                    "cwd": self.worktree}}) + "\n")
        with open(os.path.join(self.step_dir, "out", "step.log"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "thread.started", "thread_id": self.SID}) + "\n")
        self.engine.codex_roots = [snap]
        self.engine.journal.append("K-1", "step.started", step=self.step_id, ticket="PT-1", kind="ticket",
                                   executor="Codex mocny", program="codex", session="", worktree=self.worktree,
                                   stepDir=self.step_dir, branch="ticket/pt-1", repo=self.root)
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        self.terminal.launchers.codex_for.assert_called_once_with(source)
        self.assertEqual(self.terminal.plan.argv[:3], ["/snap/bin/codex", "resume", self.SID])

    def test_missing_codex_session_does_not_stop_background(self):
        self.engine.journal.append("K-1", "step.started", step=self.step_id, ticket="PT-1", kind="ticket",
                                   executor="Codex mocny", program="codex", session="", worktree=self.worktree,
                                   stepDir=self.step_dir, branch="ticket/pt-1", repo=self.root)
        with mock.patch.object(self.engine, "_stop_for_enter") as stop:
            with self.assertRaises(FlowError) as err:
                self.engine.enter("K-1", self.step_id)
        self.assertEqual(err.exception.code, "no-session")
        stop.assert_not_called()

    def test_approved_cleanup_can_reopen_after_restart_without_auto_merge(self):
        self.engine.journal.append("K-1", "step.finished", step=self.step_id, ticket="PT-1", result="done")
        self.engine.journal.append("K-1", "result", action="cleanup", key="PT-1", ok=True)
        os.rmdir(self.worktree)
        restarted = FlowEngine(self.engine.store,
                               journal=Journal(self.engine.store.root, mirror=os.path.join(self.root, "mirror")),
                               terminal=self.terminal,
                               task_files=TaskFiles(os.path.join(self.root, "tasks")))
        restarted.git = mock.Mock()
        restarted.git.sha.return_value = "approved-tip"

        def attach(*_):
            os.makedirs(self.worktree)
            return {"branch": "ticket/pt-1", "worktree": self.worktree}

        restarted.git.branch_worktree.side_effect = attach
        with mock.patch.object(agentcmd, "resolve_git_dir", return_value="/git/worktrees/pt-1"):
            reply = restarted.enter("K-1", self.step_id)
        self.assertTrue(os.path.isdir(self.worktree))
        self.assertEqual(restarted.git.branch_worktree.call_count, 1)
        self.assertEqual(reply["step"], self.step_id + "-enter-1")
        current = state.fold(restarted.journal.read("K-1")).steps[reply["step"]]
        self.assertEqual(current["kind"], "interactive")
        with open(os.path.join(current["stepDir"], "out", "result.json"), "w", encoding="utf-8") as fh:
            json.dump({"result": "done"}, fh)
        restarted.tick()
        self.assertNotIn(("merge", "PT-1"), state.fold(restarted.journal.read("K-1")).results)
        launch.write_closed(current["closeFile"])
        restarted.tick()
        visible = state.derived(state.fold(restarted.journal.read("K-1")), {"column": "todo"})
        self.assertEqual(visible["column"], "waiting")
        self.assertIn("review", visible["note"])
