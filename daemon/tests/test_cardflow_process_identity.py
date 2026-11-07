"""PT-277: tożsamość procesu nie może utracić raz rozpoznanej ochrony."""
import copy
import itertools
import json
from pathlib import Path
import random
from types import SimpleNamespace
import unittest
from unittest import mock

from . import test_cardflow_trial_inventory as fixtures
from tide_daemon.cardflow import trialhold
from tide_daemon.cardflow.state import fold
from tide_daemon.cardflow.processidentity import process_identities
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.box import packs


class ProcessIdentityRegression(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InventoryTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def history(self):
        f = self.fixture
        common = dict(step="legacy-step", ticket="PT-999", kind="ticket", unit="old",
                      worktree=str(f.root), stepDir=str(f.root / "old.step"))
        f.journal.append("K-38", "step.started", branch="ticket/pt-176-legacy", **common)
        self.assertFalse(f.inventory())
        return f.journal.append("K-38", "step.started", **common)

    def test_review14_inventory_keeps_omitted_branch(self):
        self.history()
        f = self.fixture
        for restart in (False, True):
            if restart:
                f.restart()
            with self.subTest(restart=restart):
                self.assertFalse(f.inventory())

    def test_review14_outside_keeps_omitted_branch(self):
        step = self.history()
        f = self.fixture
        f.journal.append("K-38", "step.finished", step=step["step"], ticket="PT-999",
                         result="denied", outside=True, command="echo ordinary-test", canAllow=True)
        f.journal.append("K-38", "step.decision", step=step["step"], ticket="PT-999", choice="run")
        f.restart()
        with f.outside_environment() as spawn:
            f.engine._advance("K-38", fold(f.journal.read("K-38")))
            spawn.assert_not_called()

    def test_review14_lifecycle_keeps_omitted_branch(self):
        f = self.fixture
        f.project()
        f.journal.append("K-38", "card.started", project="proj")
        self.history()
        f.restart()
        self.assertTrue(trialhold.preserves_project(fold(trialhold.read_process_history(f.journal, "K-38"))))
        f.assert_lifecycle_preserved()

    def test_review14_procedure_keeps_omitted_branch(self):
        self.history()
        f = self.fixture
        f.restart()
        for bootstrap in (False, True):
            with self.subTest(bootstrap=bootstrap):
                gate, procedure = f.procedure()
                with self.assertRaises(fixtures.gate.TrialDenied):
                    gate.start_registered("new", consent=True, inventory=f.inventory, bootstrap=bootstrap)
                procedure.start.assert_not_called()

    def test_advance_preserves_selected_ticket_with_fresh_identity(self):
        f = self.fixture
        for name, ticket in (("first", "PT-998"), ("second", "PT-999")):
            f.journal.append("K-38", "step.started", step=name, ticket=ticket, kind="ticket")
            f.journal.append("K-38", "step.finished", step=name, ticket=ticket, result="done")
        selected = fold(f.journal.read("K-38"))
        selected.last_step = "first"
        # Pętla fal wybiera kolejne tickety przez last_step. Odświeżenie historii
        # musi zachować wybór, a dane procesu pobrać z nowego, ścisłego odczytu.
        selected.steps["first"] = {**selected.steps["first"], "branch": "stale-branch"}
        with mock.patch.object(f.engine, "_review") as review:
            f.engine._advance("K-38", selected)
        self.assertEqual(review.call_count, 1)
        current = review.call_args.args[2]
        self.assertEqual(current["step"], "first")
        self.assertNotIn("branch", current)

    def test_advance_checks_new_protection_for_selected_ticket(self):
        f = self.fixture
        f.journal.append("K-38", "step.started", step="first", ticket="PT-999", kind="ticket")
        f.journal.append("K-38", "step.finished", step="first", ticket="PT-999", result="done")
        selected = fold(f.journal.read("K-38"))
        f.journal.append("K-38", "step.started", step="first", ticket="PT-999", kind="ticket",
                         branch="ticket/pt-176-old")
        f.journal.append("K-38", "step.started", step="second", ticket="PT-998", kind="ticket")
        with mock.patch.object(f.engine, "_review") as review:
            f.engine._advance("K-38", selected)
        self.assertFalse(review.called)


def generated_bad_histories():
    """Przestrzeń luk review 9–13: braki, osierocenia, kolizje i kolejność."""
    start = dict(type="step.started", step="legacy-step", ticket="PT-999", kind="ticket",
                 branch="ticket/ordinary", worktree="/work", stepDir="/step", runner="host", unit="old.scope")
    for field in ("step", "ticket", "kind", "branch", "worktree", "stepDir", "runner", "unit"):
        for value in (None, [], 17):
            yield [{**start, field: value}]
    for field in ("step", "ticket"):
        yield [{k: v for k, v in start.items() if k != field}]
    yield [dict(type="step.started", kind="ticket", unit="old.scope")]
    yield [dict(type="intent", action="step-start", key="K-38-PT-176-1")]
    for kind in ("outside.started", "outside.finished", "step.finished", "step.decision",
                 "step.entered", "step.console_opened", "step.console_closed"):
        evidence = dict(type=kind, step="legacy-step", outside=True, choice="run")
        yield [evidence]
        yield [{k: v for k, v in evidence.items() if k != "step"}]
        yield [start, {**evidence, "ticket": "PT-176"}]
        yield [start, {**evidence, "ticket": None}]
        yield [evidence, start]
    for field in ("consoleUnit", "processFile"):
        entered = dict(type="step.entered", step=start["step"],
                       consoleUnit="console.scope", processFile="/console.json")
        for value in (None, [], 17):
            yield [start, {**entered, field: value}]
        yield [start, entered, {**entered, field: "other"}]
        yield [start, entered, {"type": "step.console_opened", "step": start["step"], field: "other"}]
    for kind in ("step.entered", "step.console_opened", "step.console_closed"):
        for fields in ({}, {"consoleUnit": "old.scope", "processFile": "/console.json"}):
            yield [dict(type=kind, step="K-38-PT-176-1", **fields)]
    for value in (None, [], 17, ""):
        yield [dict(start, runner="box", box="proj", boxSlug=value)]
    yield [dict(type="outside.started", step="K-38-PT-176-1", unit="old.scope")]
    for field in ("ticket", "kind", "branch", "worktree", "stepDir", "runner", "unit"):
        other = {**start, field: "PT-176" if field == "ticket" else "other"}
        # Skrócony zapis pośrodku nie może zatrzeć kolizji ani ochrony.
        short = {k: v for k, v in start.items() if k != field}
        yield [start, short, other]
        yield [other, short, start]
    intent = {**start, "type": "intent", "action": "step-start", "key": start["step"], "ticket": "PT-176"}
    yield [intent, {"type": "result", "action": "step-start", "key": start["step"], "ok": True}, start]


def generated_project_histories():
    """Projekt pudełka może różnić się od projektu karty; żaden ślad go nie zwalnia."""
    start = dict(type="step.started", step="legacy", ticket="PT-176", runner="box",
                 boxSlug="proj", box="proj")
    finished = dict(type="step.finished", step="legacy", ticket="PT-176", result="done")
    for source in ("step.started", "intent", "result", "step.entered", "step.finished"):
        resource = {**start, "type": source}
        if source in ("intent", "result"):
            resource.update(action="step-start", key=start["step"])
        short = {k: v for k, v in start.items() if k not in ("box", "boxSlug")}
        for later in ([], [finished], [short, finished],
                      [dict(start, boxSlug="third"), finished],
                      [dict(type="card.started", project="new"), finished]):
            prefix = [] if source == "step.started" else [short]
            yield [dict(type="card.started", project="plasma-tide"), *prefix, resource, *later]


class ProcessIdentityProperties(unittest.TestCase):
    def test_generated_prefixes_never_lose_protection_or_errors(self):
        rng = random.Random(277)
        histories = list(generated_bad_histories()) + list(generated_project_histories())
        atoms = [event for history in histories for event in history]
        histories.extend([rng.choice(atoms) for _ in range(rng.randrange(1, 20))] for _ in range(300))
        rank = {"ok": 0, "unknown": 1, "inconsistent": 2}
        for history in histories:
            before = copy.deepcopy(history)
            previous = {}
            for length in range(len(history) + 1):
                current = process_identities(history[:length])
                for key, identity in previous.items():
                    self.assertIn(key, current)
                    self.assertGreaterEqual(rank[current[key].state], rank[identity.state])
                    if identity.protected:
                        self.assertTrue(current[key].protected)
                    self.assertTrue(identity.projects <= current[key].projects)
                self.assertEqual(current, process_identities(history[:length]))
                previous = current
            self.assertEqual(history, before, "reduktor zmienił historię wejściową")

    def test_omission_permutations_preserve_recognized_trial(self):
        protected = dict(type="step.started", step="ordinary", ticket="PT-999", branch="ticket/pt-176-old")
        omitted = {k: v for k, v in protected.items() if k != "branch"}
        neutral = {"type": "card.started", "project": "other"}
        for history in itertools.permutations((protected, omitted, neutral)):
            identity = process_identities(history)["ordinary"]
            self.assertEqual(identity.state, "ok")
            self.assertTrue(identity.held)
            self.assertEqual(identity.data["branch"], protected["branch"])
            self.assertTrue(trialhold.preserves_project(fold(list(history))))

    def test_new_order_does_not_move_historical_process_to_another_project(self):
        history = [{"type": "card.started", "project": "old"},
                   {"type": "step.started", "step": "legacy", "ticket": "PT-176"},
                   {"type": "card.started", "project": "new"}]
        st = fold(history)
        self.assertTrue(trialhold.preserves_project(st, "old"))
        self.assertFalse(trialhold.preserves_project(st, "new"))

    def test_all_explicit_projects_remain_protected_after_later_results(self):
        for index, history in enumerate(generated_project_histories()):
            with self.subTest(history=index):
                st = fold(history)
                identity = st.process_identities["legacy"]
                self.assertTrue({"plasma-tide", "proj"} <= identity.projects)
                self.assertTrue(trialhold.preserves_project(st, "proj"))
                if any(e.get("boxSlug") == "third" for e in history):
                    self.assertTrue(trialhold.preserves_project(st, "third"))
                    self.assertEqual(identity.state, "inconsistent")
                else:
                    self.assertEqual(identity.state, "ok")

    def test_console_trace_inherits_and_checks_started_identity(self):
        start = dict(type="step.started", step="ordinary", ticket="PT-999")
        entered = dict(type="step.entered", step="ordinary", consoleUnit="console.scope",
                       processFile="/console.json")
        history = [start, entered, dict(type="step.console_opened", step="ordinary")]
        identity = process_identities(history)["ordinary"]
        self.assertEqual(identity.state, "ok")
        self.assertFalse(identity.held)
        self.assertEqual(identity.data["consoleUnit"], "console.scope")
        self.assertEqual(identity.data["processFile"], "/console.json")
        identity = process_identities(history + [dict(entered, ticket="PT-176")])["ordinary"]
        self.assertEqual(identity.state, "inconsistent")
        self.assertTrue(identity.protected)


class GeneratedHistoryBarriers(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.InventoryTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.project()

    def test_explicit_box_project_denies_inventory_and_lifecycle_after_restart(self):
        f = self.f
        paths = [Path(f.journal.path("K-38")), Path(f.journal.mirror_path("K-38"))]
        initial = paths[0].read_bytes()
        for index, history in enumerate(generated_project_histories()):
            for path in paths:
                path.write_bytes(initial)
            f.restart()
            for event in history:
                f.journal.append("K-38", event["type"], **{k: v for k, v in event.items() if k != "type"})
            for restarted in (False, True):
                if restarted:
                    f.restart()
                with self.subTest(history=index, restarted=restarted):
                    self.assertFalse(f.inventory())
                    for bootstrap in (False, True):
                        gate, procedure = f.procedure()
                        with self.assertRaises(fixtures.gate.TrialDenied):
                            gate.start_registered("new", consent=True, inventory=f.inventory, bootstrap=bootstrap)
                        procedure.start.assert_not_called()
                    f.assert_lifecycle_preserved()

    def test_every_generated_unknown_history_denies_process_and_lifecycle_after_restart(self):
        f = self.f
        mirror = Path(f.journal.mirror_path("K-38"))
        board = Path(f.journal.path("K-38"))
        initial = f.journal.read_complete("K-38")
        for index, history in enumerate(generated_bad_histories()):
            self.assertTrue(any(i.state != "ok" for i in process_identities(history).values()))
            # Zapisujemy także wadliwe pola, których ścisły parser nie przyjmuje.
            events = [{**e, "v": 1, "seq": seq, "card": "K-38", "ts": "now"}
                      for seq, e in enumerate(initial + history, 1)]
            content = "".join(json.dumps(e) + "\n" for e in events).encode()
            mirror.write_bytes(content)
            board.write_bytes(content)
            for restarted in (False, True):
                if restarted:
                    f.restart()
                with self.subTest(history=index, restarted=restarted):
                    st = fold([])
                    # Stan przekazany do silnika jest celowo stary i bez ochrony.
                    stale = dict(step="legacy-step", ticket="PT-999", kind="ticket")
                    with f.outside_environment() as spawn:
                        f.engine._advance("K-38", fold([]))
                        f.engine.tick()
                        f.engine._recover_started_steps("K-38", fold([]))
                        f.engine._reconcile("K-38", {**stale, "action": "step-start", "key": "legacy-step"})
                        f.engine._recover_console_openings("K-38", st)
                        self.assertFalse(f.engine._stop("K-38", stale, "stopped", ""))
                        self.assertFalse(f.engine._stop_for_enter("K-38", stale))
                        self.assertFalse(f.engine._stop_interactive_transfer("K-38", stale))
                        f.engine._poll("K-38", stale)
                        f.engine._guard("K-38", stale)
                        calls = [
                            lambda: f.engine._start_outside("K-38", stale, "echo ordinary-test"),
                            lambda: f.engine._resume_step("K-38", fold([]), stale, {"choice": "retry"}),
                            lambda: f.engine.enter("K-38", "legacy-step"),
                            lambda: f.engine._launch(SimpleNamespace(id="K-38"), None, None, "PT-999",
                                                     None, None, "new-step", []),
                            lambda: f.engine._launch_full_planning(SimpleNamespace(id="K-38"), None, None,
                                                                   None, "new-step", [], [], ""),
                        ]
                        for call in calls:
                            with self.assertRaises(FlowError):
                                call()
                        self.assertFalse(spawn.called, "niepełna historia dotarła do Popen")
                    for bootstrap in (False, True):
                        gate, procedure = f.procedure()
                        with self.assertRaises(fixtures.gate.TrialDenied):
                            gate.start_registered("new", consent=True,
                                                  inventory=lambda: trialhold.inventory(f.engine, "K-38", fold([])),
                                                  bootstrap=bootstrap)
                        self.assertFalse(procedure.start.called)
                    gate, procedure = f.procedure()
                    f.engine.trial_gate = gate
                    with self.assertRaises(FlowError):
                        f.engine.register_trial_diagnostic()
                    self.assertFalse(procedure.start.called)
                    f.assert_lifecycle_preserved()
                    incus = mock.Mock()
                    builder = fixtures.Builder(SimpleNamespace(), incus=incus, policy=object())
                    for operation in (lambda: fixtures.lifecycle.Lifecycle(builder).start("proj"),
                                      lambda: builder.reset("proj")):
                        with self.assertRaises(fixtures.BoxBusy):
                            operation()
                    with self.assertRaises(packs.PackError):
                        packs.cleanup("proj", "K-38", incus, f.journal)
                    self.assertFalse(incus.stop.called)
                    self.assertFalse(incus.start.called)
                    self.assertFalse(incus.delete.called)
                    self.assertFalse(incus.device_remove.called)
                    self.assertEqual(mirror.read_bytes(), content)
                    self.assertEqual(board.read_bytes(), content)
