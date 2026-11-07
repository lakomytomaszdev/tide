"""PT-261: kompletność inwentaryzacji na plikach Tablicy, bez prawdziwych procesów."""
import json
import os
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from tide_daemon.box import lifecycle, state as bs, trialgate as gate
from tide_daemon.box.builder import BoxBusy, Builder
from tide_daemon.cardflow import boxstep, trialhold
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import fold
from .test_cardflow_trial_gate import HostStore


class InventoryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.board = self.root / "board"
        self.projects = self.board / "projects"
        self.projects.mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state"),
                                           "TIDE_BOARD_DIR": str(self.board)})
        env.start()
        self.addCleanup(env.stop)
        for method in ("run", "Popen", "call"):
            patcher = mock.patch("subprocess." + method, side_effect=AssertionError("zakaz procesu w regresji"))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.restart()
        self.journal.append("K-38", "card.started", project="plasma-tide")

    def restart(self):
        self.journal = Journal(str(self.board))
        self.engine = FlowEngine(SimpleNamespace(root=str(self.board)), journal=self.journal,
                                 codex_roots=[], settings=str(self.root / "settings.json"))

    def inventory(self):
        return trialhold.inventory(self.engine, "K-38", fold(self.journal.read("K-38")))

    def procedure(self):
        binding = gate.Binding("diagnostic", "1", str(self.root / "disposable"), "attempt",
                               "frozen", "manifest", "sha",
                               tuple((ticket, "sha") for ticket in sorted(gate.DEPENDENCIES)), "review-274")
        procedure = SimpleNamespace(snapshot=lambda: binding, bootstrap_reviewed=lambda: True,
                                    start=mock.Mock(return_value={"started": True}))
        store = HostStore()
        store.record = gate.HostRecord("PT-274", "review-274", binding)
        trial_gate = gate.TrialGate(store=store, procedure=procedure)
        trial_gate.register_bootstrap("new")
        return trial_gate, procedure

    def project(self):
        # BoardStore przechowuje metadane <slug>.md obok katalogu rejestrów <slug>/.
        (self.projects / "proj.md").write_text("---\nname: Projekt\nrepo: /disposable/project\n---\n")
        box = self.projects / "proj/box"
        box.mkdir(parents=True)
        path = box / "journal.jsonl"
        bs.record("proj", "box.ready", "proj", duration=0)
        (self.projects / "proj/box.yaml").write_text("tide-box: 1\n")
        return path

    def test_project_metadata_allows_new_procedure(self):
        self.project()
        self.assertTrue(trialhold.public_inventory(self.engine))
        trial_gate, procedure = self.procedure()
        trial_gate.start_registered("new", consent=True, inventory=self.inventory)
        procedure.start.assert_called_once()

    def outside_history(self, *, choice="run", invalid="", omit_ticket=False):
        name = "K-38-PT-999-1"
        step = self.journal.append("K-38", "step.started", step=name, ticket="PT-999", kind="ticket",
                                   unit="old", worktree=str(self.root), stepDir=str(self.root / "old.step"))
        identity = {} if omit_ticket else {"ticket": "PT-999"}
        self.journal.append("K-38", "step.finished", step=name, result="denied", outside=True,
                            command="echo ordinary-test", canAllow=True,
                            **({"ticket": "PT-176"} if invalid == "request" else identity))
        if choice:
            self.journal.append("K-38", "step.decision", step=name, choice=choice, auto=True,
                                **({"ticket": "PT-176"} if invalid == "decision" else identity))
        if invalid == "orphan":
            self.journal.append("K-38", "outside.started", step="orphan", unit="old.scope")
        return step

    @contextmanager
    def outside_environment(self):
        with mock.patch.object(self.engine, "_project", return_value=(None, SimpleNamespace(
                slug="proj", repo=str(self.root), denied_commands=[]))), \
                mock.patch("tide_daemon.tasks.node.node_home", return_value=""), \
                mock.patch("tide_daemon.tasks.node.chrome_bin", return_value=""), \
                mock.patch("tide_daemon.cardflow.engine.subprocess.Popen") as spawn:
            yield spawn

    def test_invalid_outside_history_blocks_resume_and_auto_consent_across_restart(self):
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        original = mirror.read_bytes()
        for invalid in ("request", "decision", "orphan", "truncated"):
            for choice in ("", "run", "run-always", "run-ticket"):
                if invalid == "decision" and not choice:
                    continue
                for fallback in (False, True):
                    mirror.write_bytes(original)
                    board.write_bytes(original)
                    self.restart()
                    self.outside_history(choice=choice, invalid=invalid)
                    before = mirror.read_bytes() + (b'{"v":' if invalid == "truncated" else b"")
                    board.write_bytes(before)
                    if fallback:
                        mirror.unlink()
                    else:
                        mirror.write_bytes(before)
                    for restarted in (False, True):
                        if restarted:
                            self.restart()
                        with self.subTest(invalid=invalid, choice=choice, fallback=fallback, restarted=restarted), \
                                self.outside_environment() as spawn:
                            for _ in range(2):
                                self.engine._advance("K-38", fold(self.journal.read("K-38")))
                            spawn.assert_not_called()
                            self.assertEqual(board.read_bytes(), before)
                            self.assertEqual(mirror.exists(), not fallback)
                            if not fallback:
                                self.assertEqual(mirror.read_bytes(), before)

    def test_valid_outside_with_omitted_ticket_runs_after_restart(self):
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        original = mirror.read_bytes()
        for choice in ("", "run", "run-always", "run-ticket"):
            for restarted in (False, True):
                mirror.write_bytes(original)
                board.write_bytes(original)
                self.restart()
                self.outside_history(choice=choice, omit_ticket=True)
                if restarted:
                    self.restart()
                with self.subTest(choice=choice, restarted=restarted), self.outside_environment() as spawn:
                    self.engine._advance("K-38", fold(self.journal.read("K-38")))
                    if not choice:
                        self.engine._advance("K-38", fold(self.journal.read("K-38")))
                    spawn.assert_called_once()
                    self.assertEqual(spawn.call_args.args[0][-1], "echo ordinary-test")

    def test_conflicting_start_identity_blocks_outside_and_procedure_across_restart(self):
        self.project()
        paths = [Path(self.journal.path("K-38")), Path(self.journal.mirror_path("K-38"))]
        original = paths[0].read_bytes()
        name = "legacy-step"
        # Każda para zapisów może nadpisać wcześniejszą tożsamość przy fold.
        for first, second in (("intent", "step.started"), ("step.started", "step.started"),
                              ("intent", "intent"), ("step.started", "intent")):
            for fallback in (False, True):
                for outside in (False, True):
                    for path in paths:
                        path.write_bytes(original)
                    self.restart()
                    for event_type, ticket in ((first, "PT-176"), (second, "PT-999")):
                        identity = {"key": name, "action": "step-start"} if event_type == "intent" else {"step": name}
                        self.journal.append("K-38", event_type, ticket=ticket, kind="ticket",
                                            unit="old", worktree=str(self.root),
                                            stepDir=str(self.root / "old.step"), **identity)
                    self.journal.append("K-38", "result", action="step-start", key=name, ok=True)
                    if outside:
                        self.journal.append("K-38", "step.finished", step=name, ticket="PT-999",
                                            result="denied", outside=True, command="echo ordinary-test", canAllow=True)
                        self.journal.append("K-38", "step.decision", step=name, ticket="PT-999", choice="run")
                    before = paths[0].read_bytes()
                    if fallback:
                        paths[1].unlink()
                    for restarted in (False, True):
                        if restarted:
                            self.restart()
                        with self.subTest(first=first, second=second, fallback=fallback,
                                          outside=outside, restarted=restarted):
                            with self.outside_environment() as spawn:
                                self.engine._advance("K-38", fold(self.journal.read("K-38")))
                                spawn.assert_not_called()
                            for bootstrap in (False, True):
                                trial_gate, procedure = self.procedure()
                                with self.assertRaises(gate.TrialDenied):
                                    trial_gate.start_registered("new", consent=True, inventory=self.inventory,
                                                                bootstrap=bootstrap)
                                procedure.start.assert_not_called()
                            self.assert_lifecycle_preserved()
                            self.assertEqual(paths[0].read_bytes(), before)
                            self.assertEqual(paths[1].exists(), not fallback)
                            if not fallback:
                                self.assertEqual(paths[1].read_bytes(), before)

    def test_matching_repeated_start_preserves_optional_k60_fields_across_restart(self):
        paths = [Path(self.journal.path("K-38")), Path(self.journal.mirror_path("K-38"))]
        original = paths[0].read_bytes()
        for restarted in (False, True):
            for path in paths:
                path.write_bytes(original)
            self.restart()
            name = "K-38-PT-999-1"
            self.journal.append("K-38", "intent", action="step-start", key=name, ticket="PT-999")
            self.journal.append("K-38", "result", action="step-start", key=name, ok=True)
            self.journal.append("K-38", "step.started", step=name, ticket="PT-999")
            self.outside_history(omit_ticket=True)
            if restarted:
                self.restart()
            with self.subTest(restarted=restarted), self.outside_environment() as spawn:
                self.engine._advance("K-38", fold(self.journal.read("K-38")))
                spawn.assert_called_once()

    def test_optional_start_identity_cannot_change_through_an_omitted_field(self):
        paths = [Path(self.journal.path("K-38")), Path(self.journal.mirror_path("K-38"))]
        original = paths[0].read_bytes()
        for field, value, changed in (("kind", "ticket", "review"), ("branch", "old", "new"),
                                       ("worktree", "/old", "/new"), ("stepDir", "/old", "/new"),
                                       ("runner", "host", "box")):
            for path in paths:
                path.write_bytes(original)
            self.restart()
            self.journal.append("K-38", "intent", action="step-start", key="ordinary", ticket="PT-999",
                                **{field: value})
            self.journal.append("K-38", "step.started", step="ordinary", ticket="PT-999")
            # Poprawne pominięcie pola nadal pozwala na nową procedurę.
            self.assertTrue(self.inventory())
            self.journal.append("K-38", "step.started", step="ordinary", ticket="PT-999",
                                **{field: changed})
            before = [path.read_bytes() for path in paths]
            for restarted in (False, True):
                if restarted:
                    self.restart()
                with self.subTest(field=field, restarted=restarted):
                    self.assertFalse(self.inventory())
                    self.assertEqual([path.read_bytes() for path in paths], before)

    def test_outside_rechecks_start_identity_at_process_boundary(self):
        step = self.outside_history()
        paths = [Path(self.journal.path("K-38")), Path(self.journal.mirror_path("K-38"))]
        changed = []

        def invalidate(_):
            # Ostatni zapis pasuje do przekazanego kroku, ale historia już jest sprzeczna.
            for ticket in ("PT-176", "PT-999"):
                self.journal.append("K-38", "intent", action="step-start", key=step["step"], ticket=ticket)
            changed.extend(path.read_bytes() for path in paths)
            return ""

        with self.outside_environment() as spawn, \
                mock.patch("tide_daemon.tasks.node.chrome_bin", side_effect=invalidate):
            with self.assertRaises(FlowError):
                self.engine._start_outside("K-38", step, "echo ordinary-test")
        spawn.assert_not_called()
        self.assertEqual([path.read_bytes() for path in paths], changed)

    def test_outside_rechecks_history_at_process_boundary(self):
        step = self.outside_history()
        paths = [Path(self.journal.path("K-38")), Path(self.journal.mirror_path("K-38"))]
        changed = []

        def invalidate(_):
            self.journal.append("K-38", "step.decision", step=step["step"], ticket="PT-176", choice="run")
            changed.extend(path.read_bytes() for path in paths)
            return ""

        with self.outside_environment() as spawn, \
                mock.patch("tide_daemon.tasks.node.chrome_bin", side_effect=invalidate):
            with self.assertRaises(FlowError):
                self.engine._start_outside("K-38", step, "echo ordinary-test")
        spawn.assert_not_called()
        self.assertEqual([path.read_bytes() for path in paths], changed)

    def test_direct_outside_start_requires_current_recorded_request_and_consent(self):
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        original = mirror.read_bytes()
        for invalid in ("no-consent", "command", "decision-command", "step", "missing-history"):
            mirror.write_bytes(original)
            board.write_bytes(original)
            self.restart()
            step = self.outside_history(choice="" if invalid == "no-consent" else "run")
            command = "echo ordinary-test"
            if invalid == "command":
                command = "echo changed"
            elif invalid == "decision-command":
                self.journal.append("K-38", "step.decision", step=step["step"], choice="run", command="echo changed")
            elif invalid == "step":
                step = {**step, "worktree": str(self.root / "other")}
            elif invalid == "missing-history":
                mirror.unlink()
                board.unlink()
            before = [path.read_bytes() if path.exists() else None for path in (mirror, board)]
            with self.subTest(invalid=invalid), self.outside_environment() as spawn:
                with self.assertRaises(FlowError):
                    self.engine._start_outside("K-38", step, command)
                spawn.assert_not_called()
                self.assertEqual([path.read_bytes() if path.exists() else None for path in (mirror, board)], before)

    def test_invalid_outside_history_stops_tick_before_recovery(self):
        self.outside_history(invalid="request")
        before = Path(self.journal.mirror_path("K-38")).read_bytes()
        with mock.patch.object(self.engine, "_recover_started_steps") as recover:
            self.engine.tick()
        recover.assert_not_called()
        self.assertEqual(Path(self.journal.mirror_path("K-38")).read_bytes(), before)

    def test_card_process_identity_is_validated_before_filtering(self):
        path = Path(self.journal.mirror_path("K-38"))
        original = path.read_bytes()
        for kind, name_field in (("step.started", "step"), ("intent", "key")):
            valid = {"v": 1, "seq": 2, "card": "K-38", "ts": "now", "type": kind,
                     "action": "step-start", name_field: "ordinary", "kind": "ticket",
                     "ticket": "PT-1", "branch": "ticket/pt-1"}
            invalid = [{k: v for k, v in valid.items() if k not in (name_field, "ticket", "branch")}]
            for field, values in {name_field: (None, "", " ", [], 1),
                                  "ticket": (None, "", " ", [], 1),
                                  "kind": (None, "", "future", [], 1),
                                  "branch": (None, [], 1)}.items():
                invalid.extend({**valid, field: value} for value in values)
            invalid.append({k: v for k, v in valid.items() if k != "ticket"})
            for event in invalid:
                before = original + (json.dumps(event) + "\n").encode()
                path.write_bytes(before)
                with self.subTest(event=event):
                    with self.assertRaises(ValueError):
                        self.journal.read_complete("K-38")
                    self.assertFalse(trialhold.inventory(self.engine, "K-38", fold([])))
                    self.assertEqual(path.read_bytes(), before)

    def test_caretaker_kinds_preserve_complete_history_and_ticket_identity_after_restart(self):
        for kind in ("caretaker", "caretaker-chat"):
            for ticket in ("", "PT-176"):
                name = kind + (ticket or "-card")
                self.journal.append("K-38", "intent", action="step-start", key=name, kind=kind, ticket=ticket)
                self.journal.append("K-38", "step.started", step=name, kind=kind, ticket=ticket)
                self.restart()
                history = trialhold.read_process_history(self.journal, "K-38")
                self.assertEqual(fold(history).caretakers[name]["ticket"], ticket)
                if ticket:
                    self.assertTrue(trialhold.held(fold(history).caretakers[name]))
                    self.assertTrue(trialhold.preserves_project(fold(history)))

    def test_historical_caretaker_without_intent_is_included_in_inventory(self):
        self.journal.append("K-38", "step.started", step="care", kind="caretaker", ticket="PT-176")
        self.restart()
        self.assertFalse(self.inventory())
        inventories = [e for e in self.journal.read("K-38") if e["type"] == "k38.inventory"]
        self.assertEqual(inventories[-1]["items"][0]["step"], "care")

    def test_incomplete_card_identity_blocks_start_and_lifecycle_across_restart(self):
        self.project()
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        original = mirror.read_bytes()
        for kind in ("step.started", "intent"):
            for fallback in (False, True):
                event = {"v": 1, "seq": 2, "card": "K-38", "ts": "now", "type": kind,
                         "action": "step-start", "kind": "ticket", "unit": "old.scope"}
                before = original + (json.dumps(event) + "\n").encode()
                board.write_bytes(before if fallback else original)
                if fallback:
                    mirror.unlink(missing_ok=True)
                else:
                    mirror.write_bytes(before)
                for generation in range(2):
                    self.restart()
                    with self.subTest(kind=kind, fallback=fallback, generation=generation):
                        _, procedure = self.procedure()
                        self.engine.trial_gate = gate.TrialGate(procedure=procedure)
                        with self.assertRaises(FlowError):
                            self.engine.register_trial_diagnostic()
                        procedure.start.assert_not_called()
                        for bootstrap in (False, True):
                            trial_gate, accepted = self.procedure()
                            with self.assertRaises(gate.TrialDenied):
                                trial_gate.start_registered("new", consent=True, inventory=self.inventory,
                                                            bootstrap=bootstrap)
                            accepted.start.assert_not_called()
                        self.assert_lifecycle_preserved()
                        self.engine._recover_started_steps("K-38", fold([]))
                        self.engine.tick()
                        with self.assertRaises(ValueError):
                            self.journal.append("K-38", "card.paused")
                        self.assertEqual(board.read_bytes(), before if fallback else original)
                        self.assertEqual(mirror.exists(), not fallback)
                        if not fallback:
                            self.assertEqual(mirror.read_bytes(), before)

    def test_complete_card_identity_allows_planning_without_ticket(self):
        for kind in ("planning", "plan-fix", "plan-import", "plan-review", "plan-amend",
                     "plan-revise", "close", "interactive"):
            self.journal.append("K-38", "intent", action="step-start", key=kind, kind=kind)
            self.journal.append("K-38", "step.started", step=kind, kind=kind, ticket="")
        self.journal.append("K-38", "step.started", step="ordinary", kind="ticket", ticket="PT-1")
        self.restart()
        self.assertTrue(self.inventory())
        _, procedure = self.procedure()
        self.engine.trial_gate = gate.TrialGate(procedure=procedure)
        name = self.engine.register_trial_diagnostic()["step"]
        self.engine.resolve_step("K-38", {"step": name, "choice": "run"})
        procedure.start.assert_called_once()

    def test_orphan_outside_denies_both_modes_across_restart_without_rewriting(self):
        self.project()
        self.journal.append("K-38", "card.started", project="proj")
        self.journal.append("K-38", "card.closed")
        bs.record("proj", "box.ready", "proj", clock=lambda: 0, duration=0)
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        original = mirror.read_bytes()
        for identity in ({"step": "K-38-PT-176-1"}, {}):
            for fallback in (False, True):
                event = {"v": 1, "seq": 4, "ts": "now", "card": "K-38",
                         "type": "outside.started", "unit": "old.scope", **identity}
                before = original + (json.dumps(event) + "\n").encode()
                board.write_bytes(before if fallback else original)
                if fallback:
                    mirror.unlink(missing_ok=True)
                else:
                    mirror.write_bytes(before)
                for restarted in (False, True):
                    if restarted:
                        self.restart()
                    for bootstrap in (True, False):
                        with self.subTest(identity=identity, fallback=fallback,
                                          restarted=restarted, bootstrap=bootstrap):
                            trial_gate, procedure = self.procedure()
                            with self.assertRaises(gate.TrialDenied):
                                trial_gate.start_registered("new", consent=True, inventory=self.inventory,
                                                            bootstrap=bootstrap)
                            procedure.start.assert_not_called()
                            self.assert_lifecycle_preserved()
                            self.assertEqual(board.read_bytes(), before if fallback else original)
                            self.assertEqual(mirror.exists(), not fallback)
                            if not fallback:
                                self.assertEqual(mirror.read_bytes(), before)

    def test_linked_unprotected_outside_allows_both_modes_across_restart(self):
        for kind, name_field in (("step.started", "step"), ("intent", "key")):
            name = "ordinary-" + kind
            self.journal.append("K-38", kind, **{name_field: name, "ticket": "PT-244",
                                                "action": "step-start", "kind": "ticket"})
            # Dawne outside nie powtarzało ticketu; tożsamość bierze z kroku.
            self.journal.append("K-38", "step.finished", step=name, outside=True, result="denied")
            self.journal.append("K-38", "step.decision", step=name, choice="run")
            self.journal.append("K-38", "outside.started", step=name, unit="ordinary.scope")
        paths = [Path(self.journal.path("K-38")), Path(self.journal.mirror_path("K-38"))]
        before = [path.read_bytes() for path in paths]
        for restarted in (False, True):
            if restarted:
                self.restart()
            for bootstrap in (True, False):
                with self.subTest(restarted=restarted, bootstrap=bootstrap):
                    trial_gate, procedure = self.procedure()
                    trial_gate.start_registered("new", consent=True, inventory=self.inventory,
                                                bootstrap=bootstrap)
                    procedure.start.assert_called_once()
                    self.assertEqual([path.read_bytes() for path in paths], before)

    def test_invalid_outside_evidence_preserves_processes_across_restart(self):
        self.project()
        # Nie ufamy nawet przypisaniu do innego projektu z niepełnej historii.
        self.journal.append("K-38", "step.started", step="ordinary", ticket="PT-244")
        self.journal.append("K-38", "card.closed")
        bs.record("proj", "box.ready", "proj", clock=lambda: 0, duration=0)
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        original = mirror.read_bytes()
        identities = [{}]
        identities.extend({"step": name} for name in (None, "", " ", [], 1,
                                                       "orphan", "K-38-PT-176-1"))
        identities.extend({"step": "ordinary", "ticket": ticket}
                          for ticket in (None, "", [], 1, "PT-245"))
        for evidence in ({"type": "outside.started", "unit": "old.scope"},
                         {"type": "step.finished", "outside": True, "result": "denied"},
                         {"type": "step.decision", "choice": "run"}):
            for identity in identities:
                event = {"v": 1, "seq": 4, "ts": "now", "card": "K-38", **evidence, **identity}
                before = original + (json.dumps(event) + "\n").encode()
                for fallback in (False, True):
                    board.write_bytes(before if fallback else original)
                    if fallback:
                        mirror.unlink(missing_ok=True)
                    else:
                        mirror.write_bytes(before)
                    for restarted in (False, True):
                        if restarted:
                            self.restart()
                        with self.subTest(event=event, fallback=fallback, restarted=restarted):
                            inventory = lambda: trialhold.inventory(self.engine, "K-38", fold([]))
                            for bootstrap in (False, True):
                                trial_gate, procedure = self.procedure()
                                with self.assertRaises(gate.TrialDenied):
                                    trial_gate.start_registered("new", consent=True, inventory=inventory,
                                                                bootstrap=bootstrap)
                                procedure.start.assert_not_called()
                            self.assertFalse(trialhold.public_inventory(self.engine))
                            self.assert_lifecycle_preserved()
                            incus = mock.Mock()
                            incus.instance.return_value = {"status": "Running", "config": {
                                bs.COPY_OF_KEY: "proj", bs.CARD_KEY: "K-38"}}
                            incus.list.return_value = [{"name": "proj"}]
                            builder = Builder(SimpleNamespace(), incus=incus, policy=object())
                            for operation in (lambda: builder.reset("proj"), lambda: builder.destroy("proj")):
                                with self.assertRaises(BoxBusy):
                                    operation()
                            lifecycle.Lifecycle(builder).close_card("proj", "K-38")
                            incus.snapshot_restore.assert_not_called()
                            incus.delete.assert_not_called()
                            incus.stop.assert_not_called()
                            self.assertEqual(board.read_bytes(), before if fallback else original)
                            self.assertEqual(mirror.exists(), not fallback)
                            if not fallback:
                                self.assertEqual(mirror.read_bytes(), before)

    def test_outside_identity_must_match_an_earlier_step_before_filtering(self):
        self.journal.append("K-38", "step.started", step="ordinary", ticket="PT-244")
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        original = mirror.read_bytes()
        valid = {"v": 1, "seq": 3, "ts": "now", "card": "K-38", "type": "outside.started",
                 "step": "ordinary", "ticket": "PT-244", "unit": "old.scope"}
        invalid = [{**valid, "step": name} for name in (None, "", " ", [], 1, "orphan")]
        invalid.extend({**valid, "ticket": ticket} for ticket in (None, "", [], 1, "PT-245"))
        for event in invalid:
            before = original + (json.dumps(event) + "\n").encode()
            mirror.write_bytes(before)
            with self.subTest(event=event):
                self.assertFalse(trialhold.inventory(self.engine, "K-38", fold([])))
                self.assertEqual(mirror.read_bytes(), before)
                self.assertEqual(board.read_bytes(), original)
        # Późniejszy step.started nie naprawia brakującego powiązania przy starcie outside.
        orphan = {**valid, "step": "later"}
        later = {**valid, "seq": 4, "type": "step.started", "step": "later"}
        before = original + (json.dumps(orphan) + "\n" + json.dumps(later) + "\n").encode()
        mirror.write_bytes(before)
        self.assertFalse(self.inventory())
        self.assertEqual(mirror.read_bytes(), before)
        self.assertEqual(board.read_bytes(), original)

    def test_diagnostic_decision_rechecks_card_identity_after_registration(self):
        _, procedure = self.procedure()
        self.engine.trial_gate = gate.TrialGate(procedure=procedure)
        name = self.engine.register_trial_diagnostic()["step"]
        path = Path(self.journal.mirror_path("K-38"))
        event = {"v": 1, "seq": len(self.journal.read_complete("K-38")) + 1,
                 "card": "K-38", "ts": "now", "type": "step.started", "kind": "ticket"}
        before = path.read_bytes() + (json.dumps(event) + "\n").encode()
        path.write_bytes(before)
        with self.assertRaises(FlowError):
            self.engine.resolve_step("K-38", {"step": name, "choice": "run"})
        procedure.start.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_failed_reservation_cannot_dispatch_ticket_without_identity(self):
        self.journal.append("K-38", "card.started", entry="now", executor="Sonnet")
        self.journal.append("K-38", "intent", action="reserve", key="launch-2", youtrack=True)
        self.journal.append("K-38", "result", action="reserve", key="launch-2", ok=False,
                            permanent=True, error="odmowa trackera")
        path = Path(self.journal.mirror_path("K-38"))
        before = path.read_bytes()
        with mock.patch.object(self.engine, "_project", return_value=(mock.Mock(), mock.Mock())), \
                mock.patch("tide_daemon.cardflow.engine.execution.resolve", return_value=mock.Mock()), \
                mock.patch.object(self.engine, "_launch") as launch:
            for _ in range(3):
                self.engine._advance("K-38", fold(self.journal.read_complete("K-38")))
            launch.assert_not_called()
            self.assertEqual(path.read_bytes(), before)
            # Dopiero udana rezerwacja i przygotowanie worktree pozwalają na dispatch.
            self.journal.append("K-38", "result", action="reserve", key="launch-2", ok=True, ticket="LOG-1")
            self.engine._advance("K-38", fold(self.journal.read_complete("K-38")))
            launch.assert_not_called()
            self.journal.append("K-38", "result", action="card-branch", key="card/k-38", ok=True,
                                branch="card/k-38", worktree=str(self.root / "card"))
            self.engine._advance("K-38", fold(self.journal.read_complete("K-38")))
            launch.assert_not_called()
            self.journal.append("K-38", "result", action="ticket-branch", key="LOG-1", ok=True,
                                ticket="LOG-1", branch="ticket/log-1", worktree=str(self.root / "ticket"))
            self.engine._advance("K-38", fold(self.journal.read_complete("K-38")))
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args[3], "LOG-1")

    def test_final_stop_preserves_incomplete_box_history_without_engine_after_restart(self):
        path = self.project()
        for missing in (False, True):
            path.write_bytes(b'{"type":"box.use","step":"demo-old",BROKEN}\n')
            if missing:
                path.unlink()
            for restarted in (False, True):
                if restarted:
                    self.restart()
                with self.subTest(missing=missing, restarted=restarted):
                    incus = mock.Mock()
                    incus.instance.return_value = {"status": "Running"}
                    builder = Builder(SimpleNamespace(), incus=incus, policy=object())
                    before = None if missing else path.read_bytes()
                    with self.assertRaises(BoxBusy):
                        lifecycle.Lifecycle(builder).stop("proj")
                    incus.stop.assert_not_called()
                    self.assertEqual(None if not path.exists() else path.read_bytes(), before)

    def test_first_build_without_instance_or_history_is_idle(self):
        path = self.project()
        path.unlink()
        incus = mock.Mock()
        incus.instance.return_value = None
        builder = Builder(SimpleNamespace(), incus=incus, policy=object())
        builder.require_idle("proj", "proj")

    def test_box_history_validates_envelope_and_process_identity_before_filtering(self):
        path = self.project()
        valid = {"v": 1, "ts": "2026-11-07T00:00:00+0100", "type": "box.use",
                 "box": "proj", "step": "ordinary-step"}
        invalid = [{}, {"v": 1, "type": "box.use", "box": "proj"}]
        for field, values in {"v": (None, True, 1.0, 2),
                              "type": (None, [], "box.future"),
                              "ts": (None, 123, ""),
                              "box": (None, [], 123, "", " "),
                              "step": (None, [], {}, 123, "", " ")}.items():
            invalid.append({key: value for key, value in valid.items() if key != field})
            invalid.extend({**valid, field: value} for value in values)
        invalid.append({**valid, "type": "box.release", "step": None})
        invalid.append({**valid, "type": "box.ready", "v": 2})
        for event in invalid:
            before = (json.dumps(event) + "\n" + json.dumps({**valid, "type": "box.release"}) + "\n").encode()
            path.write_bytes(before)
            with self.subTest(event=event):
                self.assertEqual(trialhold.public_processes(str(self.board)),
                                 [{"state": "unknown", "path": str(path)}])
                self.assertEqual(path.read_bytes(), before)

        before = json.dumps(valid).encode()
        path.write_bytes(before)
        self.assertEqual(trialhold.public_processes(str(self.board)),
                         [{"state": "unknown", "path": str(path)}])
        self.assertEqual(path.read_bytes(), before)

    def test_incomplete_box_process_blocks_final_operations_and_cleanup_after_restart(self):
        path = self.project()
        before = b'{"v":1,"type":"box.use","box":"proj"}\n'
        path.write_bytes(before)
        self.journal.append("K-38", "card.started", project="proj")
        self.journal.append("K-38", "card.closed")
        for restarted in (False, True):
            if restarted:
                self.restart()
            with self.subTest(restarted=restarted):
                incus = mock.Mock()
                incus.instance.return_value = {"status": "Running", "config": {
                    bs.COPY_OF_KEY: "proj", bs.CARD_KEY: "K-38"}}
                incus.list.return_value = [{"name": "proj"}]
                builder = Builder(SimpleNamespace(), incus=incus, policy=object())
                managed = lifecycle.Lifecycle(builder)
                for operation in (lambda: managed.stop("proj"), lambda: builder.reset("proj"),
                                  lambda: builder.destroy("proj")):
                    with self.assertRaises(BoxBusy):
                        operation()
                    self.assertEqual(path.read_bytes(), before)
                managed.close_card("proj", "K-38")
                incus.stop.assert_not_called()
                incus.snapshot_restore.assert_not_called()
                incus.delete.assert_not_called()
                self.assertEqual(path.read_bytes(), before)
                with mock.patch.object(boxstep, "settings_of", return_value=SimpleNamespace(enabled=True)), \
                        mock.patch.object(boxstep, "builder_of") as build, \
                        mock.patch("tide_daemon.box.packs.cleanup") as cleanup:
                    lifecycle.tick(self.engine)
                build.assert_not_called()
                cleanup.assert_not_called()
                self.assertEqual(path.read_bytes(), before)

    def test_incomplete_box_process_blocks_fresh_public_diagnostic_after_restart(self):
        path = self.project()
        before = b'{"v":1,"type":"box.use","box":"proj"}\n'
        path.write_bytes(before)
        for generation in range(2):
            if generation:
                self.restart()
            with self.subTest(generation=generation):
                _, procedure = self.procedure()
                binding = procedure.snapshot()
                procedure.snapshot = lambda: replace(binding, attempt=str(generation),
                                                       target=binding.target + str(generation))
                self.engine.trial_gate = gate.TrialGate(procedure=procedure)
                name = self.engine.register_trial_diagnostic()["step"]
                with self.assertRaises(FlowError):
                    self.engine.resolve_step("K-38", {"step": name, "choice": "run"})
                procedure.start.assert_not_called()
                for bootstrap in (False, True):
                    trial_gate, accepted = self.procedure()
                    with self.subTest(bootstrap=bootstrap), self.assertRaises(gate.TrialDenied):
                        trial_gate.start_registered("new", consent=True, inventory=self.inventory,
                                                    bootstrap=bootstrap)
                    accepted.start.assert_not_called()
                self.assertEqual(path.read_bytes(), before)

    def test_complete_box_history_preserves_release_and_unrelated_process_semantics(self):
        path = self.project()
        for kind in bs.EVENTS:
            if kind not in ("box.use", "box.release"):
                bs.record("proj", kind, "proj", **({"duration": 0} if kind in bs.TIMED else {}))
        bs.mark_in_use("proj", "proj", "ordinary-step")
        self.assertTrue(trialhold.public_inventory(self.engine))
        bs.mark_in_use("proj", "proj", "demo-old")
        self.assertFalse(trialhold.public_inventory(self.engine))
        bs.mark_released("proj", "proj", "demo-old")
        bs.mark_released("proj", "proj", "ordinary-step")
        before = path.read_bytes()
        self.restart()
        self.assertTrue(trialhold.public_inventory(self.engine))
        _, procedure = self.procedure()
        self.engine.trial_gate = gate.TrialGate(procedure=procedure)
        name = self.engine.register_trial_diagnostic()["step"]
        self.engine.resolve_step("K-38", {"step": name, "choice": "run"})
        procedure.start.assert_called_once()
        self.assertEqual(path.read_bytes(), before)
        incus = mock.Mock()
        incus.project = 'user-1000'
        incus.operations.return_value = []
        incus.list.return_value = []
        incus.instance.side_effect = lambda name: {'status': 'Stopped' if incus.stop.called else 'Running'}
        builder = Builder(SimpleNamespace(), incus=incus, policy=object())
        lifecycle.Lifecycle(builder).stop("proj")
        incus.stop.assert_called_once_with("proj")

    def test_recovery_and_later_append_preserve_corrupt_fallback(self):
        _, procedure = self.procedure()
        self.engine.trial_gate = gate.TrialGate(procedure=procedure)
        name = self.engine.register_trial_diagnostic()["step"]
        self.journal.append("K-38", "intent", action="step-start", key="K-38-PT-261-1", ticket="PT-261")
        self.journal.append("K-38", "result", action="step-start", key="K-38-PT-261-1", ok=True)
        mirror = Path(self.journal.mirror_path("K-38"))
        mirror.unlink()
        path = Path(self.journal.path("K-38"))
        corrupt = path.read_bytes() + b'{"type":"step.started","ticket":"PT-176",BROKEN}\n'
        path.write_bytes(corrupt)
        for restarted in (False, True):
            if restarted:
                self.restart()
            with self.subTest(restarted=restarted):
                self.engine._recover_started_steps("K-38", fold(self.journal.read("K-38")))
                self.engine.tick()
                with self.assertRaises(ValueError):
                    self.journal.append("K-38", "card.paused")
                self.assertFalse(self.inventory())
                with self.assertRaises(FlowError):
                    self.engine.resolve_step("K-38", {"step": name, "choice": "run"})
                procedure.start.assert_not_called()
                self.assertEqual(path.read_bytes(), corrupt)
                self.assertFalse(mirror.exists())

    def test_recovery_of_complete_fallback_still_restores_started_step(self):
        name = "K-38-PT-261-1"
        self.journal.append("K-38", "intent", action="step-start", key=name, ticket="PT-261")
        self.journal.append("K-38", "result", action="step-start", key=name, ok=True)
        Path(self.journal.mirror_path("K-38")).unlink()
        self.engine._recover_started_steps("K-38", fold([]))
        self.assertEqual(fold(self.journal.read_complete("K-38")).steps[name]["ticket"], "PT-261")

    def test_ordinary_append_preserves_corrupt_authoritative_mirror(self):
        path = Path(self.journal.mirror_path("K-38"))
        fallback = Path(self.journal.path("K-38"))
        before = fallback.read_bytes()
        corrupt = path.read_bytes() + b"broken\n"
        path.write_bytes(corrupt)
        with self.assertRaises(ValueError):
            self.journal.append("K-38", "card.paused")
        self.assertEqual(path.read_bytes(), corrupt)
        self.assertEqual(fallback.read_bytes(), before)

    def test_unrelated_project_lifecycle_still_runs(self):
        self.project()
        builder = mock.Mock()
        builder.incus.project = 'user-1000'
        builder.incus.operations.return_value = []
        builder.incus.list.return_value = []
        with mock.patch.object(boxstep, "settings_of", return_value=SimpleNamespace(enabled=True)), \
                mock.patch.object(boxstep, "builder_of", return_value=builder), \
                mock.patch.object(lifecycle, "Lifecycle") as managed:
            lifecycle.tick(self.engine)
            managed.return_value.idle.assert_called_once_with("proj")

    def test_missing_corrupt_and_unreadable_box_journal_still_deny(self):
        path = self.project()
        path.unlink()
        self.assertFalse(trialhold.public_inventory(self.engine))
        path.write_text("broken\n")
        self.assertFalse(trialhold.public_inventory(self.engine))
        path.write_text('{"type":"box.ready"}\n')
        original = os.open

        def denied(name, *args, **kwargs):
            if name == str(path):
                raise PermissionError("test odmowy odczytu")
            return original(name, *args, **kwargs)

        with mock.patch.object(os, "open", side_effect=denied):
            self.assertFalse(trialhold.public_inventory(self.engine))

    def test_symlinks_are_not_project_metadata_or_registry_directories(self):
        target = self.root / "target"
        target.mkdir()
        for name in ("proj.md", "proj", "proj/box"):
            path = self.projects / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target, target_is_directory=True)
            with self.subTest(name=name):
                self.assertFalse(trialhold.public_inventory(self.engine))
            path.unlink()

    def test_corrupt_host_history_denies_both_modes_across_restart_without_rewriting(self):
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        board_before = board.read_bytes()
        # Rozgrzany tolerancyjny cache nie może ukryć błędu ścisłego odczytu.
        self.journal.read("K-38")
        original = mirror.read_bytes()
        corrupt = b'{"type":"step.started","step":"K-38-PT-176-1","ticket":"PT-176",BROKEN}\n'
        mirror.write_bytes(original + corrupt)
        for restarted in (False, True):
            if restarted:
                self.restart()
            with self.subTest(restarted=restarted):
                self.assertFalse(self.inventory())
                for bootstrap in (True, False):
                    trial_gate, procedure = self.procedure()
                    with self.subTest(bootstrap=bootstrap), self.assertRaises(gate.TrialDenied):
                        trial_gate.start_registered("new", consent=True, inventory=self.inventory,
                                                    bootstrap=bootstrap)
                    procedure.start.assert_not_called()
                self.assertEqual(mirror.read_bytes(), original + corrupt)
                self.assertEqual(board.read_bytes(), board_before)

    def test_strict_history_rejects_invalid_envelope_sequence_encoding_and_partial_line(self):
        mirror = Path(self.journal.mirror_path("K-38"))
        original = mirror.read_bytes()
        event = {"v": 1, "seq": 2, "card": "K-38", "ts": "now", "type": "step.started"}
        invalid = [b"[]\n", b"{}\n", b"\xff\n", b"\n", json.dumps(event).encode()]
        for changes in ({"seq": 3}, {"card": "K-99"}, {"v": 2}, {"type": None}):
            invalid.append((json.dumps({**event, **changes}) + "\n").encode())
        for suffix in invalid:
            mirror.write_bytes(original + suffix)
            with self.subTest(suffix=suffix):
                # Nie używamy tolerancyjnego read(), który sam może odrzucić złe UTF-8.
                self.assertFalse(trialhold.inventory(self.engine, "K-38", fold([])))

    def test_missing_unsafe_or_unreadable_authoritative_journal_is_not_empty_history(self):
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        mirror.unlink()
        mirror.mkdir()
        self.assertFalse(trialhold.inventory(self.engine, "K-38", fold([])))
        mirror.rmdir()
        mirror.symlink_to(board)
        self.assertFalse(self.inventory())
        mirror.unlink()
        # Brak kopii pozwala czytać prawidłową historię Tablicy; brak obu odmawia.
        self.assertTrue(self.inventory())
        with mock.patch.object(os, "open", side_effect=PermissionError("test odmowy odczytu")):
            self.assertFalse(trialhold.inventory(self.engine, "K-38", fold([])))
        board.unlink()
        self.assertFalse(self.inventory())
        board.write_text("")
        self.assertTrue(self.inventory())

    def test_inventory_refolds_complete_history_instead_of_stale_caller_state(self):
        self.journal.append("K-38", "step.started", step="K-38-PT-176-1", ticket="PT-176")
        self.assertFalse(trialhold.inventory(self.engine, "K-38", fold([])))

    def test_public_decision_denies_incomplete_card_registry(self):
        # Nawet bez rozpoznanych prób niepełny rejestr nie uprawnia do startu.
        original = os.listdir
        roots = {self.journal.mirror, str(self.board / "flow")}
        for index, unreadable in enumerate((roots, {self.journal.mirror}, {str(self.board / "flow")})):
            with self.subTest(unreadable=unreadable):
                _, procedure = self.procedure()
                binding = procedure.snapshot()
                binding = replace(binding, target=binding.target + str(index), attempt=str(index))
                procedure.snapshot = lambda binding=binding: binding
                self.engine.trial_gate = gate.TrialGate(procedure=procedure)
                name = self.engine.register_trial_diagnostic()["step"]

                def denied(path):
                    if os.fspath(path) in unreadable:
                        raise PermissionError("odmowa listowania rejestru")
                    return original(path)

                with mock.patch.object(os, "listdir", side_effect=denied), self.assertRaises(FlowError):
                    self.engine.resolve_step("K-38", {"step": name, "choice": "run"})
                procedure.start.assert_not_called()

    def test_public_decision_always_checks_known_k38(self):
        self.journal.append("K-38", "step.started", step="K-38-PT-176-1", ticket="PT-176")
        _, procedure = self.procedure()
        self.engine.trial_gate = gate.TrialGate(procedure=procedure)
        name = self.engine.register_trial_diagnostic()["step"]
        with mock.patch.object(self.journal, "cards_complete", return_value=[]), self.assertRaises(FlowError):
            self.engine.resolve_step("K-38", {"step": name, "choice": "run"})
        procedure.start.assert_not_called()

    def test_strict_registry_includes_card_without_journal_and_rejects_symlink(self):
        card = self.board / "flow/K-99"
        card.mkdir()
        self.assertIn("K-99", self.journal.cards_complete())
        with self.assertRaises(FileNotFoundError):
            self.journal.read_complete("K-99")
        card.rmdir()
        card.symlink_to(self.board / "flow/K-38", target_is_directory=True)
        with self.assertRaises(ValueError):
            self.journal.cards_complete()

    def test_corrupt_history_preserves_boxes_and_packs_across_restart(self):
        self.project()
        self.journal.append("K-38", "card.started", project="proj")
        self.journal.append("K-38", "card.closed")
        mirror = Path(self.journal.mirror_path("K-38"))
        corrupt = mirror.read_bytes() + b'{"type":"step.started","ticket":"PT-176",BROKEN}\n'
        mirror.write_bytes(corrupt)
        bs.record("proj", "box.ready", "proj", clock=lambda: 0, duration=0)
        for restarted in (False, True):
            if restarted:
                self.restart()
            with self.subTest(restarted=restarted):
                self.assertFalse(self.inventory())
                self.assertFalse(trialhold.public_inventory(self.engine))
                self.assertEqual(trialhold.public_processes(str(self.board)), [])
                incus = mock.Mock()
                incus.instance.return_value = {"status": "Running"}
                incus.list.return_value = []
                settings = SimpleNamespace(enabled=True, idle_hours=0)
                builder = Builder(settings, incus=incus, policy=object(), clock=lambda: 100, wall=lambda: 100)
                builder.steps_source = lambda slug, name: lifecycle.active_steps(builder, slug, name, self.engine)
                with mock.patch.object(boxstep, "settings_of", return_value=settings), \
                        mock.patch.object(boxstep, "builder_of", return_value=builder), \
                        mock.patch("tide_daemon.box.packs.cleanup") as cleanup:
                    lifecycle.tick(self.engine)
                incus.stop.assert_not_called()
                incus.delete.assert_not_called()
                cleanup.assert_not_called()
                # Druga kontrola przy operacji również musi odmówić, niezależnie
                # od wcześniejszego skanu ticka i od rozpoznania projektu.
                self.assertTrue(lifecycle.active_steps(builder, "proj", "proj", self.engine))
                self.assertEqual(mirror.read_bytes(), corrupt)

    def test_registry_error_preserves_all_projects_before_lifecycle(self):
        self.project()
        with mock.patch.object(boxstep, "settings_of", return_value=SimpleNamespace(enabled=True)), \
                mock.patch.object(boxstep, "builder_of") as builder, \
                mock.patch.object(self.journal, "cards_complete", side_effect=PermissionError("rejestr")):
            lifecycle.tick(self.engine)
        builder.assert_not_called()

    def test_diagnostic_public_decision_preserves_corrupt_fallback_across_restart(self):
        _, procedure = self.procedure()
        self.engine.trial_gate = gate.TrialGate(procedure=procedure)
        name = self.engine.register_trial_diagnostic()["step"]
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        mirror.unlink()
        corrupt = board.read_bytes() + b'{"type":"step.started","ticket":"PT-176",BROKEN}\n'
        board.write_bytes(corrupt)
        for restarted in (False, True):
            if restarted:
                self.restart()
            with self.subTest(restarted=restarted), self.assertRaises(FlowError):
                self.engine.resolve_step("K-38", {"step": name, "choice": "run"})
            procedure.start.assert_not_called()
            self.assertEqual(board.read_bytes(), corrupt)
            self.assertFalse(mirror.exists())

    def test_diagnostic_registration_preserves_corrupt_history_before_adapter(self):
        mirror = Path(self.journal.mirror_path("K-38"))
        board = Path(self.journal.path("K-38"))
        original = board.read_bytes()
        corrupt = original + b'{"type":"step.started","ticket":"PT-176",BROKEN}\n'
        for fallback in (False, True):
            if fallback:
                mirror.unlink()
                board.write_bytes(corrupt)
            else:
                mirror.write_bytes(corrupt)
            _, procedure = self.procedure()
            self.engine.trial_gate = gate.TrialGate(procedure=procedure)
            with self.subTest(fallback=fallback), \
                    mock.patch.object(self.engine.trial_gate, "register_bootstrap",
                                      wraps=self.engine.trial_gate.register_bootstrap) as register, \
                    self.assertRaises(FlowError):
                self.engine.register_trial_diagnostic()
            register.assert_not_called()
            self.assertEqual(board.read_bytes(), corrupt if fallback else original)
            if fallback:
                self.assertFalse(mirror.exists())
            else:
                self.assertEqual(mirror.read_bytes(), corrupt)

    def test_valid_fallback_diagnostic_never_uses_tolerant_history(self):
        mirror = Path(self.journal.mirror_path("K-38"))
        mirror.unlink()
        _, procedure = self.procedure()
        self.engine.trial_gate = gate.TrialGate(procedure=procedure)
        with mock.patch.object(self.journal, "read", side_effect=AssertionError("tolerancyjny odczyt")):
            name = self.engine.register_trial_diagnostic()["step"]
            # Także zapis konsumpcji ma odtworzyć wyłącznie pełny fallback.
            mirror.unlink()
            self.engine.resolve_step("K-38", {"step": name, "choice": "run"})
        procedure.start.assert_called_once()
        events = self.journal.read_complete("K-38")
        self.assertEqual(events[-1]["type"], "k38.diagnostic-consumed")

    def assert_lifecycle_preserved(self):
        incus = mock.Mock()
        incus.instance.return_value = {"status": "Running"}
        incus.list.return_value = []
        settings = SimpleNamespace(enabled=True, idle_hours=0)
        builder = Builder(settings, incus=incus, policy=object(), clock=lambda: 100, wall=lambda: 100)
        builder.steps_source = lambda slug, name: lifecycle.active_steps(builder, slug, name, self.engine)
        with mock.patch.object(boxstep, "settings_of", return_value=settings), \
                mock.patch.object(boxstep, "builder_of", return_value=builder), \
                mock.patch("tide_daemon.box.packs.cleanup") as cleanup:
            lifecycle.tick(self.engine)
        incus.stop.assert_not_called()
        incus.delete.assert_not_called()
        cleanup.assert_not_called()
        # Bez wcześniejszego ticka także ostatnia kontrola operacji odmawia.
        for engine in (self.engine, None):
            with self.subTest(engine=engine is not None):
                self.assertTrue(lifecycle.active_steps(builder, "proj", "proj", engine))
        with self.assertRaises(BoxBusy):
            lifecycle.Lifecycle(builder).stop("proj")
        incus.stop.assert_not_called()

    def test_public_demo_without_card_preserves_lifecycle_across_restart(self):
        self.project()
        Path(self.journal.mirror_path("K-38")).unlink()
        board = Path(self.journal.path("K-38"))
        board.unlink()
        board.parent.rmdir()
        ports = self.root / "state/tide/box/proj/ports"
        ports.mkdir(parents=True)
        guard = ports / "proj.demo-1234567890abcdef.lock"
        guard.write_bytes(b"")
        bs.record("proj", "box.ready", "proj", clock=lambda: 0, duration=0)
        for restarted in (False, True):
            if restarted:
                self.restart()
            with self.subTest(restarted=restarted):
                self.assertEqual(self.journal.cards_complete(), [])
                self.assert_lifecycle_preserved()
                self.assertEqual(guard.read_bytes(), b"")

    def test_host_start_intent_without_step_preserves_lifecycle_across_restart(self):
        self.project()
        self.journal.append("K-38", "card.started", project="proj")
        self.journal.append("K-38", "intent", action="step-start", key="K-38-PT-176-1", ticket="PT-176")
        self.journal.append("K-38", "card.closed")
        bs.record("proj", "box.ready", "proj", clock=lambda: 0, duration=0)
        for restarted in (False, True):
            if restarted:
                self.restart()
                for intent in fold(self.journal.read("K-38")).open_intents(include_background=True):
                    self.engine._reconcile("K-38", intent)
            with self.subTest(restarted=restarted):
                self.assertFalse(self.inventory())
                self.assert_lifecycle_preserved()
                self.assertFalse(fold(self.journal.read("K-38")).steps)
