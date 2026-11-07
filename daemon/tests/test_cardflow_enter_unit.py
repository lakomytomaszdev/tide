"""Kontrakt wejścia w krok: sesja, plik wyniku i zamknięcie Konsoli."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest import mock

from tide_daemon.cardflow import state, execution
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.journal import Journal
from tide_daemon.board.model import Card, Project
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

    def test_flow_child_runs_agent_then_shell(self):
        order = []
        with mock.patch.object(launch, "run_agent", side_effect=lambda *_: order.append("agent")), \
                mock.patch("subprocess.call", side_effect=lambda *_: order.append("shell")):
            launch.run_flow_child({"argv": ["/bin/claude", "--resume", "id"]})
        self.assertEqual(order, ["agent", "shell"])

    def test_failed_agent_reports_error_while_shell_stays_open(self):
        with tempfile.TemporaryDirectory() as temp:
            result = os.path.join(temp, "result.json")
            task = {"argv": ["/bin/false"], "resultFile": result}
            with mock.patch.object(launch, "run_agent", return_value=1), \
                    mock.patch("subprocess.call", return_value=0):
                launch.run_flow_child(task)
            with open(result, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), {"result": "error", "reason": "agent-exit-1"})

    def test_process_group_survives_killed_launcher_before_child_record(self):
        with tempfile.TemporaryDirectory() as temp:
            ready = os.path.join(temp, "ready")
            process = os.path.join(temp, "process")
            runner = subprocess.Popen([sys.executable, "-c",
                                       "import os,subprocess,sys,time; "
                                       "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
                                       "open(sys.argv[1],'w').write(str(child.pid)); time.sleep(30)", ready],
                                      start_new_session=True)
            self.addCleanup(lambda: runner.kill() if runner.poll() is None else None)
            deadline = time.monotonic() + 5
            while not os.path.isfile(ready) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(os.path.isfile(ready))
            with open(ready, encoding="utf-8") as fh:
                child = int(fh.read())
            try:
                launch.write_process(process, runner.pid)
                runner.kill()
                runner.wait(timeout=5)
                self.assertTrue(launch.process_alive(process))
            finally:
                os.kill(child, signal.SIGKILL)
            deadline = time.monotonic() + 5
            while launch.process_alive(process) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertFalse(launch.process_alive(process))

    def test_scope_keeps_setsid_grandchild_and_shell_until_unit_stops(self):
        unit = "tide-pt67-" + uuid.uuid4().hex
        try:
            probe = subprocess.run(["/usr/bin/systemd-run", "--user", "--scope", "--collect", "--quiet",
                                    f"--unit={unit}-probe", "--", "/bin/true"], capture_output=True)
        except OSError:
            self.skipTest("brak systemd-run")
        if probe.returncode:
            self.skipTest("brak jednostek systemd użytkownika")
        with tempfile.TemporaryDirectory() as temp:
            closed = os.path.join(temp, "closed")
            process = os.path.join(temp, "process")
            ready = os.path.join(temp, "ready")
            agent = os.path.join(temp, "agent.py")
            with open(agent, "w", encoding="utf-8") as fh:
                fh.write("import os,subprocess,sys\n"
                         "child=subprocess.Popen([sys.executable,'-c',"
                         "'import signal,time; signal.signal(signal.SIGHUP,signal.SIG_IGN); time.sleep(30)'],"
                         "start_new_session=True)\n"
                         f"open({ready!r},'w').write(f'{{child.pid}} {{os.getpid()}}')\n")
            task = {"argv": [sys.executable, agent], "cwd": temp, "workdir": temp,
                    "closedFile": closed, "processFile": process, "consoleUnit": unit,
                    "systemdRun": "/usr/bin/systemd-run", "systemctl": "/usr/bin/systemctl"}
            runner = subprocess.Popen([sys.executable, "-c",
                                       "import json,sys; from tide_daemon.tasks import launch; "
                                       "sys.exit(launch.run_flow(json.loads(sys.argv[1])))", json.dumps(task)],
                                      env={**os.environ, "SHELL": "/bin/true",
                                           "PYTHONPATH": os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))},
                                      cwd=temp)
            grandchild = 0
            try:
                deadline = time.monotonic() + 8
                while not os.path.isfile(ready) and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertTrue(os.path.isfile(ready))
                with open(ready, encoding="utf-8") as fh:
                    grandchild, agent_pid = map(int, fh.read().split())
                # Agent i powłoka już wyszły; osierocony wnuk po setsid nadal trzyma scope.
                deadline = time.monotonic() + 5
                while os.path.exists(f"/proc/{agent_pid}") and time.monotonic() < deadline:
                    time.sleep(0.05)
                self.assertFalse(os.path.exists(f"/proc/{agent_pid}"))
                self.assertFalse(os.path.isfile(closed))
                self.assertEqual(subprocess.run(["/usr/bin/systemctl", "--user", "is-active", "--quiet",
                                                 unit + ".scope"]).returncode, 0)
                store = mock.Mock(root=os.path.join(temp, "board"))
                journal = Journal(store.root, mirror=os.path.join(temp, "mirror"))
                step_id = "K-1-PT-1-1"
                os.makedirs(os.path.join(temp, "out"))
                journal.append("K-1", "card.started", executor="Sonnet", program="claude", repo=temp)
                journal.append("K-1", "step.started", step=step_id, kind="ticket", ticket="PT-1",
                               worktree=temp, stepDir=temp, repo=temp)
                journal.append("K-1", "step.entered", step=step_id, closeFile=closed, processFile=process,
                               consoleUnit=unit, taskFile=os.path.join(temp, "claimed.task"), enteredAt=time.time())
                restarted = FlowEngine(store, journal=Journal(store.root, mirror=journal.mirror))
                restarted.tick()
                self.assertNotIn(step_id, state.fold(journal.read("K-1")).finished)
                os.kill(runner.pid, signal.SIGHUP)
                runner.wait(timeout=8)
                self.assertTrue(os.path.isfile(closed))
                self.assertNotEqual(subprocess.run(["/usr/bin/systemctl", "--user", "is-active", "--quiet",
                                                    unit + ".scope"]).returncode, 0)
                restarted = FlowEngine(store, journal=Journal(store.root, mirror=journal.mirror))
                restarted.tick()
                self.assertEqual(state.fold(journal.read("K-1")).finished[step_id]["reason"], "interactive-closed")
            finally:
                subprocess.run(["/usr/bin/systemctl", "--user", "kill", "--kill-whom=all", "--signal=SIGKILL",
                                unit + ".scope"], capture_output=True)
                if runner.poll() is None:
                    runner.kill()
                    runner.wait(timeout=5)
                if grandchild:
                    try:
                        os.kill(grandchild, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


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
        unit_patch = mock.patch("tide_daemon.cardflow.engine.stepstop.unit_state", return_value="inactive")
        unit_patch.start()
        self.addCleanup(unit_patch.stop)
        store = mock.Mock(root=os.path.join(self.root, "board"))
        store.load.return_value.cards = {1: Card(1, "Karta", project="proj")}
        store.load.return_value.projects = {"proj": Project("proj", "Projekt", repo=self.root)}
        self.engine = FlowEngine(store, journal=Journal(store.root, mirror=os.path.join(self.root, "mirror")),
                                 terminal=self.terminal, task_files=TaskFiles(os.path.join(self.root, "tasks")))
        self.step_id = "K-1-PT-1-1"
        self.engine.journal.append("K-1", "card.started", executor="Sonnet", program="claude", repo=self.root,
                                   sourceEvidence=None)
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

    def test_failed_terminal_open_closes_attempt_and_allows_retry(self):
        ident = self._note()
        self.terminal.open = lambda _: ""
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            with self.assertRaises(FlowError):
                self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        self.assertTrue(current["consoleClosed"])
        self._assert_note_pending_and_retry(ident)

    def _note(self):
        return self.engine.add_step_note("K-1", {"step": self.step_id, "orderSeq": 1,
            "requestId": "console-note", "text": "Nie zgub uwagi przy otwieraniu Konsoli"})["noteId"]

    def _assert_note_pending_and_retry(self, ident, parent=None):
        from tide_daemon.cardflow import stepnotes
        self.engine = FlowEngine(self.engine.store,
            journal=Journal(self.engine.store.root, mirror=os.path.join(self.root, "mirror")),
            terminal=self.terminal, task_files=self.engine.task_files)
        folded = state.fold(self.engine.journal.read("K-1"))
        self.assertEqual(stepnotes.view(folded)[0]["status"], "pending")
        self.terminal.open = mock.Mock(return_value="tab")
        reply = self.engine.enter("K-1", parent or self.step_id)
        plan = self.terminal.open.call_args.args[0]
        self.assertIn("Nie zgub uwagi przy otwieraniu Konsoli", plan.prompt)
        with open(plan.task_file, encoding="utf-8") as fh:
            self.assertIn(ident, json.load(fh)["argv"][-1])
        folded = state.fold(self.engine.journal.read("K-1"))
        self.assertEqual(folded.steps[reply["step"]]["deliveredNoteIds"], [ident])
        self.assertEqual(stepnotes.view(folded)[0]["successor"], reply["step"])
        self.assertEqual(stepnotes.prepare(folded, reply["step"], "kolejne wznowienie")[1], [])

    def test_terminal_exception_closes_unclaimed_attempt(self):
        ident = self._note()
        self.terminal.open = mock.Mock(side_effect=RuntimeError("D-Bus"))
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            with self.assertRaises(FlowError):
                self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        self.assertTrue(current["consoleClosed"])
        self.assertEqual(state.fold(self.engine.journal.read("K-1")).finished[self.step_id]["result"], "stopped")
        self._assert_note_pending_and_retry(ident)

    def test_failed_open_cannot_revoke_notes_if_console_claimed_the_task(self):
        from tide_daemon.cardflow import stepnotes
        ident = self._note()

        def claim(plan):
            os.unlink(plan.task_file)
            return ""

        self.terminal.open = claim
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            with self.assertRaises(FlowError):
                self.engine.enter("K-1", self.step_id)
        folded = state.fold(self.engine.journal.read("K-1"))
        self.assertFalse(folded.steps[self.step_id]["consoleClosed"])
        self.assertEqual(folded.steps[self.step_id]["deliveredNoteIds"], [ident])
        self.assertEqual(stepnotes.view(folded)[0]["status"], "delivered")

    def test_unclaimed_timeout_closes_attempt_but_failed_cancel_waits(self):
        from tide_daemon.cardflow import stepnotes
        ident = self._note()
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        old = time.time() - 60
        os.utime(current["taskFile"], (old, old))
        with mock.patch.object(self.engine.task_files, "cancel", return_value=False):
            self.engine.tick()
        self.assertNotIn(self.step_id, state.fold(self.engine.journal.read("K-1")).finished)
        self.assertEqual(stepnotes.view(state.fold(self.engine.journal.read("K-1")))[0]["status"], "delivered")
        self.engine.tick()
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        self.assertTrue(current["consoleClosed"])
        self.assertEqual(state.fold(self.engine.journal.read("K-1")).finished[self.step_id]["result"], "stopped")
        self._assert_note_pending_and_retry(ident)

    def test_recovery_after_task_cancel_before_journal_close_keeps_note_pending(self):
        ident = self._note()
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        self.assertTrue(self.engine.task_files.cancel(current["taskFile"]))
        self.engine = FlowEngine(self.engine.store, journal=self.engine.journal, terminal=self.terminal,
                                task_files=self.engine.task_files, clock=lambda: current["enteredAt"] + 31)
        self.engine.tick()
        self._assert_note_pending_and_retry(ident)

    def test_recovery_after_cancel_and_runtime_loss_delivers_note_in_real_retry(self):
        ident = self._note()
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        self.assertTrue(self.engine.task_files.cancel(current["taskFile"]))
        # Awaria przed step.console_closed, a potem restart maszyny lub sweep().
        os.unlink(current["taskFile"] + ".cancelled")
        self.engine = FlowEngine(self.engine.store,
            journal=Journal(self.engine.store.root, mirror=os.path.join(self.root, "mirror")),
            terminal=self.terminal, task_files=TaskFiles(self.engine.task_files.folder),
            clock=lambda: current["enteredAt"] + 31)
        self.engine.tick()
        self._assert_note_pending_and_retry(ident)

    def test_recovery_after_durable_cancel_before_runtime_rename_delivers_note(self):
        ident = self._note()
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        rename = os.rename

        def crash(source, target):
            if source == current["taskFile"]:
                raise RuntimeError("awaria po trwałym anulowaniu")
            return rename(source, target)

        with mock.patch.object(os, "rename", side_effect=crash):
            with self.assertRaises(RuntimeError):
                self.engine.task_files.cancel(current["taskFile"])
        self.assertTrue(os.path.isfile(current["taskFile"]))
        self.assertIsNone(launch.claim(current["taskFile"]), "spóźniona Konsola nie uruchomi zadania")
        self.engine = FlowEngine(self.engine.store, journal=self.engine.journal, terminal=self.terminal,
                                task_files=self.engine.task_files, clock=lambda: current["enteredAt"] + 31)
        self.engine.tick()
        self._assert_note_pending_and_retry(ident)

    def test_cancelled_task_does_not_finish_stale_poll_before_transfer_recovery(self):
        from tide_daemon.cardflow import stepnotes
        self._note()
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        stale = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        self.engine.clock = lambda: stale["enteredAt"] + 1
        # Jak w testach awarii przekazania: migawka odpytywania powstała przed
        # zapisaniem decyzji i anulowaniem pliku, restart ma dopiero dokończyć WIP.
        self.engine.journal.append("K-1", "step.transfer_requested", step=self.step_id,
                                   ticket="PT-1", executor="Codex mocny")
        self.assertTrue(self.engine.task_files.cancel(stale["taskFile"]))
        stepnotes.close_console(self.engine.journal, "K-1", self.step_id, cancelled=True)
        self.engine._poll_interactive("K-1", stale)
        folded = state.fold(self.engine.journal.read("K-1"))
        self.assertNotIn(self.step_id, folded.finished)
        self.assertEqual(stepnotes.view(folded)[0]["status"], "pending")

    def test_full_planning_handoff_cancel_keeps_note_for_real_resume(self):
        ident = self._note()
        self.engine.journal.append("K-1", "step.finished", step=self.step_id, result="stopped")
        selected = execution.Execution("Sonnet", "claude", "/bin/claude", "sonnet", "anthropic")
        item = mock.Mock(id="K-1", projects=[])
        project = Project("proj", "Projekt", repo=self.root)
        self.terminal.open = mock.Mock(side_effect=RuntimeError("D-Bus"))
        with mock.patch.object(agentcmd, "resolve_git_dir", return_value="/git/worktree"):
            with self.assertRaises(FlowError):
                self.engine._launch_full_planning(item, project, selected,
                    {"worktree": self.worktree, "branch": "ticket/pt-1"}, "handoff", [], [], "Przekazanie",
                    parent_step=self.step_id, kind="ticket", ticket="PT-1")
        self._assert_note_pending_and_retry(ident, parent="handoff")

    def test_cancel_unclaimed_console_for_transfer_keeps_note_pending(self):
        from tide_daemon.cardflow import stepnotes
        ident = self._note()
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        with mock.patch.object(self.engine, "_stop", return_value=True):
            self.assertTrue(self.engine._stop_interactive_transfer("K-1", current))
        folded = state.fold(self.engine.journal.read("K-1"))
        self.assertEqual(stepnotes.view(folded)[0]["status"], "pending")
        self.assertEqual(stepnotes.prepare(folded, self.step_id, "Przekazanie")[1], [ident])

    def test_claimed_launcher_killed_is_reconciled_after_restart(self):
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        os.unlink(current["taskFile"])
        launch.write_process(current["processFile"], os.getpid())
        restarted = FlowEngine(self.engine.store, journal=self.engine.journal, terminal=self.terminal,
                               task_files=self.engine.task_files)
        restarted.tick()
        self.assertNotIn(self.step_id, state.fold(self.engine.journal.read("K-1")).finished)
        with open(current["processFile"], "w", encoding="utf-8") as fh:
            json.dump({"launcher": {"pid": 99999999, "start": "0", "boot": "dead"}}, fh)
        restarted.tick()
        folded = state.fold(self.engine.journal.read("K-1"))
        self.assertTrue(folded.steps[self.step_id]["consoleClosed"])
        self.assertEqual(folded.finished[self.step_id]["reason"], "interactive-closed")

    def test_lost_claim_without_process_record_is_reconciled_after_timeout(self):
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        os.unlink(current["taskFile"])
        restarted = FlowEngine(self.engine.store, journal=self.engine.journal, terminal=self.terminal,
                               task_files=self.engine.task_files, clock=lambda: current["enteredAt"] + 31)
        restarted.tick()
        folded = state.fold(self.engine.journal.read("K-1"))
        self.assertTrue(folded.steps[self.step_id]["consoleClosed"])
        self.assertEqual(folded.finished[self.step_id]["reason"], "interactive-closed")

    def test_close_marker_does_not_override_live_launcher(self):
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        launch.write_process(current["processFile"], os.getpid())
        launch.write_closed(current["closeFile"])
        self.engine.tick()
        folded = state.fold(self.engine.journal.read("K-1"))
        self.assertFalse(folded.steps[self.step_id]["consoleClosed"])
        self.assertNotIn(self.step_id, folded.finished)

    def test_active_or_unknown_scope_blocks_reconciliation_after_restart(self):
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        self.assertTrue(current.get("consoleUnit"))
        os.unlink(current["taskFile"])
        launch.write_closed(current["closeFile"])
        restarted = FlowEngine(self.engine.store, journal=self.engine.journal, terminal=self.terminal,
                               task_files=self.engine.task_files)
        with mock.patch("tide_daemon.cardflow.engine.stepstop.unit_state", return_value="active"):
            restarted.tick()
        self.assertNotIn(self.step_id, state.fold(self.engine.journal.read("K-1")).finished)
        with mock.patch("tide_daemon.cardflow.engine.stepstop.unit_state", return_value="unknown"):
            restarted.tick()
        self.assertNotIn(self.step_id, state.fold(self.engine.journal.read("K-1")).finished)
        with mock.patch("tide_daemon.cardflow.engine.stepstop.unit_state", return_value="inactive"):
            restarted.tick()
        folded = state.fold(self.engine.journal.read("K-1"))
        self.assertTrue(folded.steps[self.step_id]["consoleClosed"])
        self.assertEqual(folded.finished[self.step_id]["reason"], "interactive-closed")

    def test_corrupt_process_record_does_not_prove_console_closed(self):
        with mock.patch.object(self.engine, "_stop_for_enter", return_value=True):
            self.engine.enter("K-1", self.step_id)
        current = state.fold(self.engine.journal.read("K-1")).steps[self.step_id]
        os.unlink(current["taskFile"])
        with open(current["processFile"], "w", encoding="utf-8") as fh:
            fh.write("not json")
        restarted = FlowEngine(self.engine.store, journal=self.engine.journal, terminal=self.terminal,
                               task_files=self.engine.task_files, clock=lambda: current["enteredAt"] + 31)
        restarted.tick()
        self.assertNotIn(self.step_id, state.fold(self.engine.journal.read("K-1")).finished)

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
