"""PT-261: demo na hostowych atrapach, bez Incusa i wspólnego .git użytkownika."""
from contextlib import contextmanager
from dataclasses import replace
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from tide_daemon.box import trialgate as gate, cli, command, runner as br
from tide_daemon.cardflow import trialhold, boxstep
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold
from tide_daemon.tasks.runner import HostRunner
from tide_daemon.tasks import launch
from . import gitrepos


class HostProcedure:
    """Atrapa przyszłego rejestru PT-273. Cel należy wyłącznie do testu."""
    def __init__(self, root):
        self.target = Path(root) / "disposable"
        self.target.mkdir()
        gitrepos.git(str(self.target), "init", "-q", "-b", "master")
        gitrepos.commit(str(self.target), "README.md")
        sha = gitrepos.git(str(self.target), "rev-parse", "HEAD").strip()
        self.binding = gate.Binding("pt273-m0", "1", str(self.target), "attempt-1", "frozen-1", "manifest-1", sha,
                                    tuple((ticket, sha) for ticket in sorted(gate.DEPENDENCIES)), "review-274-1")
        self.calls = []
        self.reviewed = True

    def bootstrap_reviewed(self):
        return self.reviewed

    def snapshot(self):
        return self.binding

    def start(self, binding):
        self.calls.append(binding)
        (self.target / "proof.txt").write_text("hostowa atrapa M0\n")
        return {"started": True}


class HostStore:
    def __init__(self):
        self.record = None
        self.locked = False
        self.on_lock = lambda: None

    @contextmanager
    def locked_record(self, binding):
        self.locked = True
        try:
            self.on_lock()
            yield self.record
        finally:
            self.locked = False


class GateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.procedure = HostProcedure(self.tmp.name)
        self.store = HostStore()
        self.gate = gate.TrialGate(store=self.store, procedure=self.procedure)

    def start(self, name="new", **kwargs):
        return self.gate.start_registered(name, consent=True, inventory=lambda: True, **kwargs)

    def test_demo_partial_approvals_then_exact_host_record_only_new_procedure(self):
        self.gate.register_bootstrap("new")
        for record in (None, {"result": "done"}, {"verdict": "APPROVE", "reviewer-id": "host"},
                       {"stdout": "APPROVE", "signature": "host-signed-copy", "secret": "root-can-read"},
                       gate.HostRecord("PT-273", "review-274-1", self.procedure.binding),
                       gate.HostRecord("PT-270", "review-274-1", self.procedure.binding),
                       gate.HostRecord("PT-271", "review-274-1", self.procedure.binding),
                       gate.HostRecord("PT-274", "another-review", self.procedure.binding)):
            self.store.record = record
            with self.subTest(record=record), self.assertRaises(gate.TrialDenied):
                self.start()
        self.store.record = gate.HostRecord("PT-274", "review-274-1", self.procedure.binding)
        original_start = self.procedure.start

        def start(binding):
            self.assertTrue(self.store.locked, "blokada musi obejmować start procesu")
            return original_start(binding)

        self.procedure.start = start
        self.start()
        self.assertEqual(len(self.procedure.calls), 1)
        self.assertTrue((self.procedure.target / "proof.txt").is_file())
        with self.assertRaises(gate.TrialDenied):
            self.start()
        for ticket in gate.LEGACY:
            with self.assertRaises(gate.TrialDenied):
                gate.require_ticket(ticket)
        with self.assertRaises(gate.TrialDenied):
            gate.require_public()  # poprawny odbiór nie otwiera starej próby na .git

    def test_changed_code_manifest_attempt_or_dependency_closes_before_start(self):
        original = self.procedure.binding
        for changes in ({"sha": "changed"}, {"manifest": "changed"}, {"attempt": "new"},
                        {"persistence": "new"}, {"target": "other"}, {"version": "2"},
                        {"dependencies": original.dependencies[:-1] + (("PT-276", "changed"),)}):
            self.procedure.binding = original
            self.gate = gate.TrialGate(store=self.store, procedure=self.procedure)
            self.gate.register_bootstrap("new")
            self.store.record = gate.HostRecord("PT-274", "review-274-1", original)
            self.procedure.binding = replace(original, **changes)
            with self.subTest(changes=changes), self.assertRaises(gate.TrialDenied):
                self.start()
        self.assertEqual(self.procedure.calls, [])

    def test_change_in_window_before_process_is_refused(self):
        self.gate.register_bootstrap("new")
        self.store.record = gate.HostRecord("PT-274", "review-274-1", self.procedure.binding)
        self.store.on_lock = lambda: setattr(self.procedure, "binding", replace(self.procedure.binding, sha="raced"))
        with self.assertRaises(gate.TrialDenied):
            self.start()
        self.assertEqual(self.procedure.calls, [])

    def test_unknown_process_state_no_consent_missing_dependencies_and_restart_deny(self):
        self.gate.register_bootstrap("new")
        for kwargs in ({"consent": False, "inventory": lambda: True}, {"consent": True, "inventory": None},
                       {"consent": True, "inventory": True}, {"consent": True, "inventory": lambda: None}):
            with self.assertRaises(gate.TrialDenied):
                self.gate.start_registered("new", bootstrap=True, **kwargs)
        self.gate = gate.TrialGate(store=self.store, procedure=self.procedure)
        with self.assertRaises(gate.TrialDenied):
            self.start(bootstrap=True)
        self.procedure.binding = replace(self.procedure.binding, dependencies=())
        with self.assertRaises(gate.TrialDenied):
            self.gate.register_bootstrap("new")
        with self.assertRaises(gate.TrialDenied):
            gate.TrialGate().register_bootstrap("new")

    def test_bootstrap_is_narrow_and_requires_separately_reviewed_procedure(self):
        self.procedure.reviewed = False
        with self.assertRaises(gate.TrialDenied):
            self.gate.register_bootstrap("new")
        self.procedure.reviewed = True
        self.gate.register_bootstrap("new")
        self.start(bootstrap=True)
        self.assertEqual(len(self.procedure.calls), 1)
        with self.assertRaises(gate.TrialDenied):
            self.gate.register_bootstrap("another-step")

    def test_concurrent_consent_cannot_launch_the_same_registration_twice(self):
        self.gate.register_bootstrap("new")
        barrier = threading.Barrier(2)
        outcomes = []

        def run():
            barrier.wait(timeout=2)
            try:
                self.start(bootstrap=True)
                outcomes.append("started")
            except gate.TrialDenied:
                outcomes.append("denied")

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ["started", "denied"])
        self.assertEqual(len(self.procedure.calls), 1)


class EngineGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = SimpleNamespace(root=str(self.root / "board"))
        (Path(self.store.root) / "projects").mkdir(parents=True)
        self.journal = Journal(self.store.root, mirror=str(self.root / "mirror"))
        self.engine = self.restart()
        self.journal.append("K-38", "card.started", repo=str(self.root), project="plasma-tide")

    def restart(self):
        return FlowEngine(self.store, journal=self.journal, settings=str(self.root / "settings.json"), codex_roots=[])

    def old_step(self, ticket="PT-176", result="denied"):
        name = "K-38-" + ticket + "-1"
        work = self.root / ticket
        work.mkdir(exist_ok=True)
        (work / "wip.txt").write_text("zachowany WIP")
        step = self.journal.append("K-38", "step.started", step=name, ticket=ticket, kind="ticket", unit="old-unit",
                                   worktree=str(work), stepDir=str(work) + ".step", branch="ticket/" + ticket.lower())
        if result:
            self.journal.append("K-38", "step.finished", step=name, ticket=ticket, result=result,
                                outside=True, command="python3 old-boundary-trial.py", canAllow=True,
                                wip={"handoff": "historical-note"})
        return step

    def test_old_outside_pending_and_previously_approved_deny_after_restart(self):
        for ticket in gate.LEGACY | gate.EXCLUDED:
            step = self.old_step(ticket)
            for choice in ("allow", "run", "run-always", "run-ticket", "retry", "background", "transfer"):
                with self.subTest(ticket=ticket, choice=choice), self.assertRaises(FlowError):
                    self.engine.resolve_step("K-38", {"step": step["step"], "choice": choice})
            self.journal.append("K-38", "step.decision", step=step["step"], choice="run", auto=True)
            for engine in (self.engine, self.restart()):
                st = fold(self.journal.read("K-38"))
                with mock.patch("subprocess.Popen") as spawn:
                    with self.assertRaises(FlowError):
                        engine._outside_ready("K-38", step, st.finished[step["step"]], {"choice": "run"})
                    with self.assertRaises(FlowError):
                        engine._start_outside("K-38", step, "echo old")
                    with self.assertRaises(FlowError):
                        engine._resume_step("K-38", st, step, {"choice": "retry"})
                    with self.assertRaises(FlowError):
                        engine.enter("K-38", step["step"])
                    spawn.assert_not_called()
                engine._consent_recheck("K-38", step, st.finished[step["step"]])
                engine._outside_recheck("K-38", step, st.finished[step["step"]])

    def test_dispatch_runner_and_recovery_do_not_launch_or_cleanup_old_wip(self):
        step = self.old_step(result=None)
        st = fold(self.journal.read("K-38"))
        with self.assertRaises(FlowError):
            self.engine._launch(SimpleNamespace(id="K-38"), None, None, "PT-176", None, None, "new", [])
        for runner in (HostRunner(), object.__new__(br.BoxRunner)):
            with self.assertRaises(gate.TrialDenied):
                runner.start(SimpleNamespace(step_id=step["step"]))
        self.engine.terminal = mock.Mock()
        self.engine._recover_console_openings("K-38", st)
        self.engine.terminal.open.assert_not_called()
        with mock.patch.object(self.engine, "_finish") as finish, mock.patch.object(self.engine, "_kill") as kill:
            self.engine._poll("K-38", step)
            self.engine._guard("K-38", step)
            self.engine._advance("K-38", st)
            self.engine._reconcile("K-38", {"action": "cleanup", "key": "PT-176"})
            finish.assert_not_called()
            kill.assert_not_called()
        self.assertEqual((self.root / "PT-176/wip.txt").read_text(), "zachowany WIP")

    def test_missing_ticket_cannot_hide_protected_step_identity(self):
        for step in ({"step": "K-38-PT-176-1"}, {"branch": "ticket/pt-191-old-proof"}):
            self.assertTrue(trialhold.held(step))
            with self.assertRaises(FlowError):
                self.engine._start_outside("K-38", step, "echo old")
        with self.assertRaises(FlowError):
            self.engine._start_outside("K-38", {}, "echo unknown")

    def test_recovery_does_not_restore_refs_release_ports_or_revoke_old_mounts(self):
        step = self.old_step()
        self.journal.append("K-38", "ticket.assigned", ticket="PT-176")
        self.journal.append("K-38", "box.ports", step=step["step"], owner=step["step"], ticket="PT-176")
        self.journal.append("K-38", "step.started", **{k: v for k, v in step.items()
                                                       if k not in ("type", "seq", "v", "ts", "card")},
                            runner="box", box="plasma-tide", boxSlug="plasma-tide", boxIp="10.0.0.2")
        with mock.patch.object(boxstep, "proxy_of", return_value=None), \
                mock.patch.object(boxstep, "release_ports") as release, \
                mock.patch.object(boxstep, "_release_resources") as resources, \
                mock.patch("tide_daemon.box.refguard.finish") as finish:
            boxstep.recover_network(self.engine)
        release.assert_not_called()
        resources.assert_not_called()
        finish.assert_not_called()

    def test_inventory_preserves_processes_and_new_handoff_is_idempotent(self):
        step = self.old_step()
        self.journal.append("K-38", "outside.started", step=step["step"], ticket="PT-176", unit="old-outside")
        before = self.journal.read("K-38")
        with mock.patch.object(self.engine, "_unit_state", return_value="unknown"), \
                mock.patch.object(trialhold.stepstop, "unit_state", return_value="unknown"), \
                mock.patch.object(self.engine, "_kill") as kill:
            self.assertFalse(trialhold.inventory(self.engine, "K-38", fold(before)))
            self.assertFalse(trialhold.inventory(self.restart(), "K-38", fold(self.journal.read("K-38"))))
            kill.assert_not_called()
        events = self.journal.read("K-38")
        self.assertEqual(events[:len(before)], before)
        self.assertEqual(len([e for e in events if e["type"] == "k38.handoff"]), 1)
        self.assertEqual(len([e for e in events if e["type"] == "k38.inventory"]), 1)
        self.assertEqual((self.root / "PT-176/wip.txt").read_text(), "zachowany WIP")

    def test_bootstrap_host_ui_no_auto_no_agent_command_and_restart_consumes_nothing_twice(self):
        procedure = HostProcedure(self.tmp.name)
        self.engine.trial_gate = gate.TrialGate(procedure=procedure)
        registered = self.engine.register_trial_diagnostic()
        name = registered["step"]
        for extra in ({"auto": True}, {"command": "git --git-dir=/user/.git status"}, {"answer": "diagnostic"}):
            with self.assertRaises(FlowError):
                self.engine.resolve_step("K-38", {"step": name, "choice": "run", **extra})
        out = {"flow": {"decisions": []}}
        trialhold.view(self.engine, "K-38", out)
        self.assertEqual(out["flow"]["decisions"][0]["options"][0]["params"], {"step": name, "choice": "run"})
        self.engine.resolve_step("K-38", {"step": name, "choice": "run"})
        self.assertEqual(len(procedure.calls), 1)
        restarted = self.restart()
        restarted.trial_gate = gate.TrialGate(procedure=procedure)
        with self.assertRaises(FlowError):
            restarted.register_trial_diagnostic()
        with self.assertRaises(FlowError):
            restarted.resolve_step("K-38", {"step": name, "choice": "run"})

    def test_unknown_process_blocks_bootstrap_inside_the_final_start_check(self):
        procedure = HostProcedure(self.tmp.name)
        self.engine.trial_gate = gate.TrialGate(procedure=procedure)
        self.old_step(result=None)
        name = self.engine.register_trial_diagnostic()["step"]
        with mock.patch.object(self.engine, "_unit_state", return_value="unknown"):
            with self.assertRaises(FlowError):
                self.engine.resolve_step("K-38", {"step": name, "choice": "run"})
        self.assertEqual(procedure.calls, [])

    def test_old_public_cli_without_card_step_is_inventoried_without_cleanup(self):
        path = Path(self.store.root) / "projects/proj/box/journal.jsonl"
        path.parent.mkdir(parents=True)
        old = '{"type":"box.use","box":"proj","step":"demo-20261106120000-abcd1234"}\n'
        path.write_text(old)
        self.assertFalse(trialhold.inventory(self.engine, "K-38", fold(self.journal.read("K-38"))))
        self.assertEqual(path.read_text(), old)
        self.assertEqual(self.journal.read("K-38")[-1]["type"], "k38.public-inventory")
        self.assertEqual(self.journal.read("K-38")[-1]["items"][0]["state"], "unknown")
        path.write_text(old + "malformed\n")
        self.assertFalse(trialhold.public_inventory(self.restart()))

    def test_unrelated_outside_still_runs_as_before_k60(self):
        step = self.old_step("PT-999")
        with mock.patch("tide_daemon.cardflow.engine.subprocess.Popen") as spawn, \
                mock.patch("tide_daemon.tasks.node.node_home", return_value=""), \
                mock.patch("tide_daemon.tasks.node.chrome_bin", return_value=""):
            self.engine._start_outside("K-60", step, "echo ordinary-test")
        spawn.assert_called_once()
        self.assertEqual(self.journal.read("K-60")[-1]["type"], "outside.started")


class PublicGateTest(unittest.TestCase):
    def test_previously_issued_console_task_is_checked_again_at_both_process_boundaries(self):
        for ticket in gate.LEGACY | gate.EXCLUDED:
            old = {"title": "K-38 · " + ticket, "argv": ["old-agent"], "closedFile": "/tmp/old/closed"}
            with mock.patch.object(launch, "run_agent") as agent, mock.patch("subprocess.Popen") as spawn, \
                    mock.patch("subprocess.call") as shell:
                self.assertEqual(launch.run_flow(old), 3)
                self.assertEqual(launch.run_flow_child(old), 3)
                agent.assert_not_called()
                spawn.assert_not_called()
                shell.assert_not_called()
        self.assertFalse(launch.trial_console_allowed({"title": "K-38 · nieznany krok"}))
        self.assertTrue(launch.trial_console_allowed({"flowCard": "K-38", "flowKind": "planning"}))
        with mock.patch("runpy.run_path", side_effect=OSError("missing policy")):
            self.assertFalse(launch.trial_console_allowed({"flowCard": "K-38", "flowTicket": "PT-261"}))

    def test_public_run_and_demo_deny_before_any_host_preparation(self):
        with mock.patch.object(command.bp, "load_project") as load, \
                mock.patch.object(command, "make_step") as prepare, mock.patch("subprocess.Popen") as spawn:
            err = io.StringIO()
            self.assertEqual(cli.main(["run", "plasma-tide", "--", "git", "--git-dir=/user/.git", "status"],
                                      out=io.StringIO(), err=err), 3)
            self.assertEqual(br._demo(["plasma-tide", "--branch", "old", "--", "true"]), 3)
            load.assert_not_called()
            prepare.assert_not_called()
            spawn.assert_not_called()
            self.assertIn("K-38 v1", err.getvalue())
