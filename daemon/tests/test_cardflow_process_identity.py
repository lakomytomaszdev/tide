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

    def test_review2_resource_identity_never_stops_actual_box(self):
        f = self.fixture
        f.project()
        paths = [Path(f.journal.path("K-38")), Path(f.journal.mirror_path("K-38"))]
        initial = paths[0].read_bytes()
        for resource in ({}, {"boxSlug": "proj "}, {"boxSlug": "other"}):
            for path in paths:
                path.write_bytes(initial)
            f.restart()
            f.journal.append("K-38", "step.started", step="legacy", ticket="PT-176",
                             runner="box", box="proj", **resource)
            f.journal.append("K-38", "step.finished", step="legacy", ticket="PT-176", result="done")
            f.restart()
            with self.subTest(resource=resource):
                incus = mock.Mock()
                incus.instance.return_value = {"status": "Running"}
                builder = fixtures.Builder(SimpleNamespace(), incus=incus, policy=object())
                builder.steps_source = lambda slug, name: fixtures.lifecycle.active_steps(
                    builder, slug, name, f.engine)
                try:
                    fixtures.lifecycle.Lifecycle(builder).stop("proj")
                except fixtures.BoxBusy:
                    pass
                incus.stop.assert_not_called()
                f.assert_lifecycle_preserved()
                st = fold(f.journal.read("K-38"))
                self.assertNotEqual(st.process_identities["legacy"].state, "ok")
                self.assertTrue(trialhold.preserves_project(st, "proj"))
                self.assertTrue(trialhold.preserves_project(st, "unrelated"))

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

    def test_review3_invalid_card_project_never_stops_host_trial_box(self):
        f = self.fixture
        f.project()
        paths = [Path(f.journal.path("K-38")), Path(f.journal.mirror_path("K-38"))]
        initial = paths[0].read_bytes()
        for project in ("proj ", "proj\n", "../proj", "Proj"):
            for path in paths:
                path.write_bytes(initial)
            f.journal.append("K-38", "card.started", project=project)
            f.journal.append("K-38", "intent", action="step-start", key="legacy", ticket="PT-176")
            f.journal.append("K-38", "step.started", step="legacy", ticket="PT-176", runner="host")
            f.journal.append("K-38", "step.finished", step="legacy", ticket="PT-176", result="done")
            f.restart()
            with self.subTest(project=project):
                incus = mock.Mock()
                incus.instance.return_value = {"status": "Running"}
                builder = fixtures.Builder(SimpleNamespace(), incus=incus, policy=object(),
                                           clock=lambda: 100, wall=lambda: 100)
                builder.steps_source = lambda slug, name: fixtures.lifecycle.active_steps(
                    builder, slug, name, f.engine)
                with self.assertRaises(fixtures.BoxBusy):
                    fixtures.lifecycle.Lifecycle(builder).stop("proj")
                incus.stop.assert_not_called()
                with self.assertRaises(ValueError):
                    trialhold.read_process_history(f.journal, "K-38")

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

    def review4_history(self):
        f = self.fixture
        f.project()
        f.journal.append("K-38", "card.started", project="proj")
        step = f.journal.append("K-38", "step.started", step="legacy", ticket="PT-999",
                                kind="ticket", runner="host", unit="old.scope",
                                worktree=str(f.root), stepDir=str(f.root / "old.step"))
        f.journal.append("K-38", "step.finished", step="legacy", ticket="PT-999",
                         result="denied", outside=True, command="echo ordinary-test", canAllow=True)
        f.journal.append("K-38", "step.decision", step="legacy", ticket="PT-999", choice="run")
        # Odtwarzamy zapis starej usługi bez korzystania z nowego walidatora.
        events = f.journal.read_complete("K-38")
        for event in events:
            if event.get("step") == "legacy":
                event["ticket"] = "PT-176 "
        content = "".join(json.dumps(event) + "\n" for event in events)
        for path in (f.journal.path("K-38"), f.journal.mirror_path("K-38")):
            Path(path).write_text(content)
        step["ticket"] = "PT-176 "
        return step

    def test_review4_noncanonical_ticket_denies_outside_across_restart(self):
        step = self.review4_history()
        f = self.fixture
        paths = [Path(f.journal.path("K-38")), Path(f.journal.mirror_path("K-38"))]
        initial = paths[0].read_bytes()
        for restarted in (False, True):
            for path in paths:
                path.write_bytes(initial)
            if restarted:
                f.restart()
            with self.subTest(restarted=restarted), f.outside_environment() as spawn:
                try:
                    f.engine._start_outside("K-38", step, "echo ordinary-test")
                except FlowError:
                    pass
                spawn.assert_not_called()

    def test_review4_noncanonical_ticket_preserves_box_across_restart(self):
        self.review4_history()
        f = self.fixture
        for restarted in (False, True):
            if restarted:
                f.restart()
            with self.subTest(restarted=restarted):
                incus = mock.Mock()
                incus.instance.return_value = {"status": "Running"}
                builder = fixtures.Builder(SimpleNamespace(), incus=incus, policy=object())
                builder.steps_source = lambda slug, name: fixtures.lifecycle.active_steps(
                    builder, slug, name, f.engine)
                try:
                    fixtures.lifecycle.Lifecycle(builder).stop("proj")
                except fixtures.BoxBusy:
                    pass
                incus.stop.assert_not_called()

    def test_review6_unknown_type_blocks_unrelated_outside_and_box_stop(self):
        f = self.fixture
        f.project()
        f.journal.append("K-38", "card.started", project="proj")
        paths = [Path(f.journal.path("K-38")), Path(f.journal.mirror_path("K-38"))]
        initial = paths[0].read_bytes()
        for source, restarted in itertools.product(
                ("step.started ", "STEP.STARTED", "intent ", "result "), (False, True)):
            for path in paths:
                path.write_bytes(initial)
            f.restart()
            f.journal.append("K-38", source, step="legacy", key="legacy", action="step-start",
                             ticket="PT-176", kind="ticket", runner="host", unit="legacy.scope",
                             worktree=str(f.root), stepDir=str(f.root / "legacy.step"))
            step = f.outside_history()
            if restarted:
                f.restart()
            with self.subTest(source=source, restarted=restarted):
                identities = process_identities(f.journal.read_complete("K-38"))
                with self.subTest(barrier="model"):
                    self.assertIn("legacy", identities)
                    self.assertEqual(identities["legacy"].state, "unknown")
                    self.assertTrue(identities["legacy"].protected)
                    self.assertEqual(identities["legacy"].data["unit"], "legacy.scope")
                with self.subTest(barrier="inventory"):
                    self.assertFalse(f.inventory())
                with self.subTest(barrier="outside"), f.outside_environment() as spawn:
                    try:
                        f.engine._start_outside("K-38", step, "echo ordinary-test")
                    except FlowError:
                        pass
                    self.assertEqual(spawn.call_count, 0)
                with self.subTest(barrier="lifecycle"):
                    f.assert_lifecycle_preserved()

    def test_review7_incomplete_outside_unit_blocks_unrelated_process_and_box_stop(self):
        f = self.fixture
        f.project()
        paths = [Path(f.journal.path("K-38")), Path(f.journal.mirror_path("K-38"))]
        initial = paths[0].read_bytes()
        for fields, restarted in itertools.product(({}, {"unit": ""}, {"unit": "   "}), (False, True)):
            for path in paths:
                path.write_bytes(initial)
            f.restart()
            for event in outside_unit_history("PT-999", fields):
                f.journal.append("K-38", event["type"], **{k: v for k, v in event.items() if k != "type"})
            step = f.outside_history()
            if restarted:
                f.restart()
            with self.subTest(fields=fields, restarted=restarted):
                with self.subTest(barrier="model"):
                    identity = process_identities(f.journal.read_complete("K-38"))["legacy-step"]
                    self.assertEqual(identity.state, "unknown")
                with self.subTest(barrier="inventory"):
                    self.assertFalse(f.inventory())
                with self.subTest(barrier="outside"), f.outside_environment() as spawn:
                    try:
                        f.engine._start_outside("K-38", step, "echo ordinary-test")
                    except FlowError:
                        pass
                    spawn.assert_not_called()
                with self.subTest(barrier="lifecycle"):
                    f.assert_lifecycle_preserved()

    def test_review8_incomplete_console_blocks_unrelated_process_and_box_stop(self):
        f = self.fixture
        f.project()
        paths = [Path(f.journal.path("K-38")), Path(f.journal.mirror_path("K-38"))]
        initial = paths[0].read_bytes()
        variants = ({}, {"consoleUnit": "", "processFile": ""},
                    {"consoleUnit": "   ", "processFile": "   "},
                    {"consoleUnit": "console.scope"}, {"processFile": "/console.json"})
        for fields, restarted in itertools.product(variants, (False, True)):
            for path in paths:
                path.write_bytes(initial)
            f.restart()
            for event in console_history("PT-999", fields):
                f.journal.append("K-38", event["type"], **{k: v for k, v in event.items() if k != "type"})
            f.journal.append("K-38", "step.console_opened", step="legacy-step")
            step = f.outside_history()
            if restarted:
                f.restart()
            with self.subTest(fields=fields, restarted=restarted):
                with self.subTest(barrier="model"):
                    identity = process_identities(f.journal.read_complete("K-38"))["legacy-step"]
                    self.assertNotEqual(identity.state, "ok")
                with self.subTest(barrier="inventory"):
                    self.assertFalse(f.inventory())
                with self.subTest(barrier="outside"), f.outside_environment() as spawn:
                    try:
                        f.engine._start_outside("K-38", step, "echo ordinary-test")
                    except FlowError:
                        pass
                    spawn.assert_not_called()
                with self.subTest(barrier="lifecycle"):
                    f.assert_lifecycle_preserved()

    def test_review5_incomplete_action_blocks_unrelated_outside_and_box_stop(self):
        f = self.fixture
        f.project()
        f.journal.append("K-38", "card.started", project="proj")
        paths = [Path(f.journal.path("K-38")), Path(f.journal.mirror_path("K-38"))]
        initial = paths[0].read_bytes()
        for source, fields, restarted in itertools.product(
                ("intent", "result"), ({}, {"action": "step-start "}), (False, True)):
            for path in paths:
                path.write_bytes(initial)
            f.restart()
            f.journal.append("K-38", source, key="legacy", ticket="PT-176", kind="ticket",
                             runner="host", unit="legacy.scope", worktree=str(f.root),
                             stepDir=str(f.root / "legacy.step"), **fields)
            step = f.outside_history()
            if restarted:
                f.restart()
            with self.subTest(source=source, fields=fields, restarted=restarted):
                identities = process_identities(f.journal.read_complete("K-38"))
                self.assertIn("legacy", identities)
                self.assertNotEqual(identities["legacy"].state, "ok")
                self.assertTrue(identities["legacy"].protected)
                self.assertFalse(f.inventory())
                with f.outside_environment() as spawn:
                    with self.assertRaises(FlowError):
                        f.engine._start_outside("K-38", step, "echo ordinary-test")
                    spawn.assert_not_called()
                f.assert_lifecycle_preserved()


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
    yield from generated_bad_resource_histories()
    yield from generated_bad_card_project_histories()
    yield from generated_bad_ticket_histories()
    yield from generated_bad_action_histories()
    yield from generated_bad_type_histories()
    yield from generated_bad_outside_unit_histories()
    yield from generated_bad_console_histories()


def console_history(ticket, fields):
    return [dict(type="card.started", project="proj"),
            dict(type="step.started", step="legacy-step", ticket=ticket, kind="ticket",
                 runner="host", unit="legacy.scope"),
            dict(type="step.entered", step="legacy-step", **fields)]


def generated_bad_console_histories():
    """M1 rundy 8: dowody własne wejścia i dziedziczone otwarcia/zamknięcia."""
    complete = dict(consoleUnit="console.scope", processFile="/console.json")
    variants = [{}]
    for field in complete:
        variants.append({k: v for k, v in complete.items() if k != field})
        variants.extend({**complete, field: value}
                        for value in ("", "   ", "\t\n", "\u00a0", None, [], {}, 17))
    for ticket, fields in itertools.product(("PT-176", "PT-999"), variants):
        history = console_history(ticket, fields)
        good = dict(type="step.entered", step="legacy-step", **complete)
        opened = dict(type="step.console_opened", step="legacy-step")
        closed = dict(type="step.console_closed", step="legacy-step")
        done = dict(type="step.finished", step="legacy-step", result="done")
        for later in ([], [opened], [closed, done], [good, opened, closed, done], [closed, good, done]):
            yield [*history, *later]
        # Każde wejście wymaga własnych dowodów, także po kompletnym wejściu.
        yield [*history[:-1], good, history[-1], closed, done]
        for kind in ("step.console_opened", "step.console_closed"):
            event = dict(type=kind, step="legacy-step", **fields)
            # Otwarcie i zamknięcie bez pełnych wcześniejszych danych.
            yield [*history[:-1], event, good, closed, done]
            # Pominięcie dziedziczy, lecz jawna pusta/błędna wartość nie może
            # zostać zastąpiona wcześniejszą poprawną wartością.
            if any(not isinstance(value, str) or not value.strip() for value in fields.values()):
                yield [*history[:-1], good, event, closed, done]


def outside_unit_history(ticket, fields):
    """Jednostka kroku nie zastępuje jednostki późniejszego procesu outside."""
    return [dict(type="card.started", project="proj"),
            dict(type="step.started", step="legacy-step", ticket=ticket, kind="ticket",
                 runner="host", unit="legacy.scope"),
            dict(type="step.finished", step="legacy-step", ticket=ticket, result="denied",
                 outside=True, command="echo legacy"),
            dict(type="step.decision", step="legacy-step", ticket=ticket, choice="run"),
            dict(type="outside.started", step="legacy-step", ticket=ticket, command="echo legacy",
                 log="/missing/log", exitFile="/missing/exit", **fields)]


def generated_bad_outside_unit_histories():
    """M1 rundy 7: brak jednostki outside pozostaje nieznany po uzupełnieniu."""
    variants = [{}] + [{"unit": value} for value in ("", "   ", "\t\n", "\u00a0", None, [], 17)]
    for ticket, fields in itertools.product(("PT-176", "PT-999"), variants):
        history = outside_unit_history(ticket, fields)
        good = {**history[-1], "unit": "outside.scope"}
        finished = dict(type="outside.finished", step="legacy-step", ticket=ticket, exitCode=0)
        for later in ([], [finished], [good], [good, finished], [finished, good]):
            yield [*history, *later]


def generated_bad_type_histories():
    """B1 rundy 6: nieznany typ, brak tożsamości, kolejność i uzupełnienia."""
    sources = ("step.started", "intent", "result", "step.finished", "step.decision",
               "outside.started", "outside.finished", "step.entered", "step.console_opened",
               "step.console_closed", "card.started", "plan.approved")
    variants = [{}] + [{"type": value} for value in (None, [], {}, 17, "", "future.event")]
    variants += [{"type": value} for source in sources
                 for value in (source + " ", source.upper(), " " + source)]
    for ticket, fields in itertools.product(("PT-176", "PT-999"), variants):
        good = dict(type="step.started", step="legacy-step", ticket=ticket, kind="ticket",
                    runner="host", unit="old.scope", worktree="/work", stepDir="/step")
        bad = {k: v for k, v in good.items() if k != "type"}
        bad.update(fields)
        done = dict(type="step.finished", step="legacy-step", result="done")
        prefix = [dict(type="card.started", project="proj")]
        yield [*prefix, bad]
        yield [*prefix, good, bad]
        yield [*prefix, bad, good, done]
        # Nie zakładamy, że nieznany dyskryminator używa pola step zamiast key.
        keyed = {k: v for k, v in bad.items() if k != "step"}
        yield [*prefix, {**keyed, "key": "legacy-step", "action": "step-start"}, good, done]
        yield [*prefix, keyed, good, done]
        # Nieznane zdarzenie bez jakichkolwiek dowodów też nie jest metadanymi.
        yield [*prefix, fields, good, done]


def generated_bad_action_histories():
    """B1 rundy 5: dyskryminator nie może usuwać śladu ani późniejszego błędu."""
    variants = [{}] + [{"action": value} for value in (
        None, [], {}, 17, "", "step-start ", " step-start", "STEP-START", "future-action")]
    for source, ticket, fields in itertools.product(("intent", "result"), ("PT-176", "PT-999"), variants):
        good = dict(type="step.started", step="legacy-step", ticket=ticket, kind="ticket",
                    runner="host", unit="old.scope", worktree="/work", stepDir="/step")
        bad = {**good, "type": source, "key": "legacy-step", **fields}
        del bad["step"]
        prefix = [dict(type="card.started", project="proj")]
        yield [*prefix, bad]
        yield [*prefix, good, bad]
        yield [*prefix, bad, good, dict(type="step.finished", step="legacy-step", result="done")]
        # Ani późniejszy poprawny dyskryminator, ani brak nazwy nie usuwa niepewności.
        yield [*prefix, {k: v for k, v in bad.items() if k != "key"},
               {**bad, "action": "step-start"}, good]


def generated_bad_ticket_histories():
    """B1 rundy 4: niekanoniczne ID, każdy ślad i późniejsze uzupełnienia."""
    for ticket in ("PT-176", "PT-999"):
        prefix, number = ticket.split("-")
        variants = [ticket + space for space in (" ", "\n", "\t", "\u00a0")]
        variants += [" " + ticket, "\t" + ticket, ticket.lower(), f"{prefix}-0{number}",
                     f"{prefix}-+{number}", f"{prefix}-{'1' * 7}",
                     f"{prefix}-" + "".join(chr(ord(c) + 0xFEE0) for c in number),
                     f"{prefix}_{number}", " ", "unknown"]
        good = dict(type="step.started", step="legacy-step", ticket=ticket, kind="ticket", runner="host")
        done = dict(type="step.finished", step="legacy-step", result="done")
        for value in variants:
            for source in ("step.started", "intent", "result", "outside.started", "outside.finished",
                           "step.finished", "step.decision", "step.entered",
                           "step.console_opened", "step.console_closed"):
                event = dict(type=source, step="legacy-step", ticket=value)
                if source in ("intent", "result"):
                    event.update(action="step-start", key="legacy-step")
                if source in ("step.started", "intent"):
                    event.update(kind="ticket", runner="host")
                    prefix_events = []
                else:
                    prefix_events = [good]
                yield [*prefix_events, event]
                yield [*prefix_events, event, good, done]
            # Sama zmiana rodzaju kroku nie legalizuje wadliwego ID.
            yield [dict(good, ticket=value, kind="planning")]


def generated_bad_card_project_histories():
    """B1 rundy 3: wadliwy kontekst, uzupełnienia i zmiany zlecenia."""
    good = dict(type="card.started", project="proj")
    variants = [dict(type="card.started")]
    variants.extend(dict(type="card.started", project=value) for value in (
        None, [], 17, "", "proj ", "proj\n", "../proj", "Proj", "x" * 65))
    for ticket in ("PT-176", "PT-999"):
        start = dict(type="step.started", step="legacy-step", ticket=ticket, runner="host")
        intent = dict(type="intent", action="step-start", key="legacy-step", ticket=ticket)
        done = dict(type="step.finished", step="legacy-step", ticket=ticket, result="done")
        for bad in variants:
            yield [bad, intent, start, done]
            yield [bad, intent, good, start, done]
            yield [bad, start, good, done]
            yield [good, start, bad, done, good, done]
            yield [bad, start, dict(type="card.started", project="other"), done]


def generated_bad_resource_histories():
    """B1 rundy 2: niepełne i sprzeczne powiązania runner/pudełko/projekt."""
    base = dict(step="legacy-step", ticket="PT-999", kind="ticket", card="K-38")
    valid = dict(runner="box", box="proj", boxSlug="proj")
    variants = [{k: v for k, v in valid.items() if k != field} for field in valid]
    for field, values in (
        ("runner", (None, [], "other", "host", "")),
        ("boxSlug", (None, [], "", "proj ", "proj\n", "../proj", "Proj", "x" * 65, "other")),
        ("box", (None, [], "", "other", "proj-k-39")),
    ):
        variants.extend({**valid, field: value} for value in values)
    good = dict(type="step.started", **base, **valid)
    done = dict(type="step.finished", **base, result="done")
    for source in ("step.started", "intent", "result", "step.entered", "step.finished",
                   "outside.started", "step.decision"):
        for resource in variants:
            event = dict(type=source, **base, **resource)
            if source in ("intent", "result"):
                event.update(action="step-start", key=base["step"])
            prefix = [] if source in ("step.started", "intent") else [dict(type="step.started", **base)]
            yield [*prefix, event]
            # Późniejsze uzupełnienie ani done nie usuwa niepewności zakresu.
            yield [*prefix, event, good, done]


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
    def test_incomplete_outside_units_remain_unknown_after_completion_and_repair(self):
        for index, history in enumerate(generated_bad_outside_unit_histories()):
            with self.subTest(history=index):
                identity = process_identities(history)["legacy-step"]
                self.assertEqual(identity.state, "unknown")
                self.assertTrue(identity.held)
                self.assertEqual(identity.protected, history[1]["ticket"] == "PT-176")
                self.assertEqual(identity.data["unit"], "legacy.scope")

    def test_complete_outside_has_separate_unit_and_allows_unitless_completion(self):
        history = outside_unit_history("PT-999", {"unit": "outside.scope"})
        for later in ([], [dict(type="outside.finished", step="legacy-step", exitCode=0)]):
            with self.subTest(later=later):
                identity = process_identities(history + later)["legacy-step"]
                self.assertEqual(identity.state, "ok")
                self.assertFalse(identity.held)
                self.assertEqual(identity.data["unit"], "legacy.scope")
                self.assertEqual(identity.outside_unit, "outside.scope")

    def test_unknown_types_remain_unknown_with_valid_context_and_later_completion(self):
        for index, history in enumerate(generated_bad_type_histories()):
            with self.subTest(history=index):
                identities = process_identities(history)
                self.assertTrue(any(i.state != "ok" for i in identities.values()))
                if any(e.get("ticket") == "PT-176" for e in history):
                    self.assertTrue(any(i.protected for i in identities.values()))

    def test_unknown_type_preserves_process_fields_and_protection_without_normalizing(self):
        for name_field in ("step", "key"):
            bad = dict(type="intent ", action="step-start", ticket="PT-176", kind="ticket",
                       runner="host", unit="legacy.scope", branch="ticket/pt-176-old",
                       consoleUnit="console.scope", processFile="/console.json")
            bad[name_field] = "legacy"
            history = [dict(type="card.started", project="proj"), bad]
            identity = process_identities(history)["legacy"]
            with self.subTest(name_field=name_field):
                self.assertEqual(identity.state, "unknown")
                self.assertTrue(identity.protected)
                for key, value in bad.items():
                    self.assertEqual(identity.data[key], value)

    def test_noncanonical_tickets_stay_held_with_valid_project_and_later_completion(self):
        for index, history in enumerate(generated_bad_ticket_histories()):
            with self.subTest(history=index):
                identity = process_identities([dict(type="card.started", project="proj"), *history])["legacy-step"]
                self.assertNotEqual(identity.state, "ok")
                self.assertTrue(identity.held)

    def test_canonical_tickets_and_ticketless_card_steps_remain_valid(self):
        for ticket in ("PT-176", "PT-999", "A1-1", "PROJECT123-999999"):
            history = [dict(type="card.started", project="proj"),
                       dict(type="step.started", step="legacy", ticket=ticket, kind="ticket"),
                       dict(type="step.finished", step="legacy", result="done")]
            with self.subTest(ticket=ticket):
                identity = process_identities(history)["legacy"]
                self.assertEqual(identity.state, "ok")
                self.assertEqual(identity.protected, ticket == "PT-176")
        for kind in ("planning", "plan-fix", "plan-import", "plan-review", "plan-amend",
                     "plan-revise", "close", "interactive"):
            for fields in ({}, {"ticket": ""}):
                history = [dict(type="card.started", project="proj"),
                           dict(type="intent", action="step-start", key="legacy", kind=kind, **fields),
                           dict(type="step.started", step="legacy", kind=kind, **fields),
                           dict(type="step.finished", step="legacy", result="done", **fields)]
                with self.subTest(kind=kind, fields=fields):
                    identity = process_identities(history)["legacy"]
                    self.assertEqual(identity.state, "ok")
                    self.assertFalse(identity.held)

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
            history = [dict(type="card.started", project="proj"), *history]
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
                elif any(e.get("runner") == "box" and "boxSlug" not in e for e in history):
                    self.assertEqual(identity.state, "unknown")
                    self.assertIn("", identity.projects)
                else:
                    self.assertEqual(identity.state, "ok")

    def test_valid_resource_names_use_shared_naming_contract(self):
        from tide_daemon.box import state as bs

        for slug in ("proj", "my.project_name", "123", "x" * 64):
            for box in (bs.box_name(slug), bs.card_box_name(slug, "K-38")):
                start = dict(type="step.started", step="legacy", ticket="PT-176", card="K-38",
                             runner="box", box=box, boxSlug=slug)
                # Zdarzenia uzupełniające nie muszą powtarzać całej tożsamości.
                history = [dict(type="card.started", project="plasma-tide"), start,
                           dict(type="result", action="step-start", key="legacy", boxSlug=slug),
                           dict(type="step.finished", step="legacy", ticket="PT-176", result="done")]
                with self.subTest(slug=slug, box=box):
                    identity = process_identities(history)["legacy"]
                    self.assertEqual(identity.state, "ok")
                    self.assertEqual(identity.projects, {"plasma-tide", slug})
                    self.assertTrue(identity.held)

    def test_bad_resources_keep_unknown_scope_after_completion(self):
        for index, history in enumerate(generated_bad_resource_histories()):
            with self.subTest(history=index):
                st = fold(history)
                identity = st.process_identities["legacy-step"]
                self.assertNotEqual(identity.state, "ok")
                self.assertIn("", identity.projects)
                self.assertTrue(trialhold.preserves_project(st, "unrelated"))

    def test_bad_card_projects_keep_unknown_scope_after_later_orders(self):
        for index, history in enumerate(generated_bad_card_project_histories()):
            with self.subTest(history=index):
                st = fold(history)
                identity = st.process_identities["legacy-step"]
                self.assertEqual(identity.state, "unknown")
                self.assertIn("", identity.projects)
                self.assertTrue(trialhold.preserves_project(st, "proj"))
                self.assertTrue(trialhold.preserves_project(st, "unrelated"))

    def test_valid_card_projects_keep_precise_host_scope(self):
        for project in ("proj", "my.project_name", "123", "x" * 64):
            history = [dict(type="card.started", project=project),
                       dict(type="step.started", step="legacy", ticket="PT-176", runner="host")]
            with self.subTest(project=project):
                st = fold(history)
                self.assertEqual(st.process_identities["legacy"].state, "ok")
                self.assertEqual(st.process_identities["legacy"].projects, {project})
                self.assertTrue(trialhold.preserves_project(st, project))
                self.assertFalse(trialhold.preserves_project(st, "unrelated"))

    def test_missing_card_context_cannot_be_repaired_after_process_start(self):
        start = dict(type="step.started", step="legacy", ticket="PT-999", runner="host")
        history = [start, dict(type="card.started", project="proj"), start]
        for length in (1, 2, 3):
            with self.subTest(length=length):
                st = fold(history[:length])
                self.assertEqual(st.process_identities["legacy"].state, "unknown")
                self.assertTrue(trialhold.preserves_project(st, "unrelated"))

    def test_console_trace_inherits_and_checks_started_identity(self):
        start = dict(type="step.started", step="ordinary", ticket="PT-999", unit="")
        entered = dict(type="step.entered", step="ordinary", consoleUnit="console.scope",
                       processFile="/console.json")
        history = [dict(type="card.started", project="proj"), start, entered,
                   dict(type="step.console_opened", step="ordinary"),
                   dict(type="step.console_closed", step="ordinary")]
        identity = process_identities(history)["ordinary"]
        self.assertEqual(identity.state, "ok")
        self.assertFalse(identity.held)
        self.assertEqual(identity.data["consoleUnit"], "console.scope")
        self.assertEqual(identity.data["processFile"], "/console.json")
        identity = process_identities(history + [dict(entered, ticket="PT-176")])["ordinary"]
        self.assertEqual(identity.state, "inconsistent")
        self.assertTrue(identity.protected)

    def test_incomplete_console_evidence_cannot_be_repaired_or_finished(self):
        for index, history in enumerate(generated_bad_console_histories()):
            with self.subTest(history=index):
                identity = process_identities(history)["legacy-step"]
                self.assertNotEqual(identity.state, "ok")
                self.assertTrue(identity.held)


class GeneratedHistoryBarriers(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.InventoryTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.project()

    def test_known_nonprocess_events_allow_inventory_and_outside_after_restart(self):
        f = self.f
        # Zdarzenia metadanych emitowane przez przepływ karty nie oznaczają startu.
        sources = ("card.inputs", "card.paused", "card.resumed", "card.finished", "card.closed",
                   "card.main-merged", "card.moved", "card.stalled", "plan.checked", "plan.imported",
                   "plan.proposed", "plan.reviewer-selected", "plan.approval_requested", "plan.returned",
                   "plan.revision-discarded", "plan.revision-failed", "plan.revision-ready",
                   "plan.revision-approved", "plan.reviewed", "plan.ticket-edited", "plan.approved",
                   "plan.finished", "ticket.assigned", "ticket.rejected", "ticket.imported",
                   "reviewer.reassigned", "review.verdict", "review.question", "review.decision",
                   "review.reviewer-note", "step.waiting", "step.retry", "step.transfer_requested",
                   "step.wip", "step.limit", "step.notice", "step.activity", "step.reported", "step.cleanup",
                   "flow.error", "flow.notice", "permission.rechecked", "refs.guard", "box.wait",
                   "box.rebuild-requested", "box.ports", "box.risk", "packs.removed", "k38.handoff",
                   "k38.inventory", "k38.public-inventory", "k38.diagnostic-requested",
                   "k38.diagnostic-consumed", "youtrack.reset", "youtrack.retry")
        paths = [Path(f.journal.path("K-38")), Path(f.journal.mirror_path("K-38"))]
        initial = paths[0].read_bytes()
        for source in sources:
            for path in paths:
                path.write_bytes(initial)
            f.restart()
            f.journal.append("K-38", source, step="legacy", ticket="PT-176", key="PT-176",
                             branch="ticket/pt-176-old", worktree=str(f.root), rows=[], map={},
                             value="", items=[])
            with self.subTest(source=source):
                self.assertEqual(process_identities(f.journal.read_complete("K-38")), {})
                step = f.outside_history()
                f.restart()
                self.assertTrue(f.inventory())
                with f.outside_environment() as spawn:
                    f.engine._start_outside("K-38", step, "echo ordinary-test")
                    spawn.assert_called_once()

    def test_known_nonprocess_actions_allow_inventory_and_outside_after_restart(self):
        f = self.f
        paths = [Path(f.journal.path("K-38")), Path(f.journal.mirror_path("K-38"))]
        initial = paths[0].read_bytes()
        for action in ("reserve", "card-branch", "ticket-branch", "review-branch", "step-reopen",
                       "plan-commit", "cleanup", "merge", "youtrack-create", "youtrack-link", "youtrack-note"):
            for path in paths:
                path.write_bytes(initial)
            f.restart()
            # Rezerwacja/gałąź chronionego ticketu nie oznacza startu jego procesu.
            for source in ("intent", "result"):
                f.journal.append("K-38", source, action=action, key="PT-176", ticket="PT-176",
                                 branch="ticket/pt-176-old", worktree=str(f.root), ok=True)
            with self.subTest(action=action):
                self.assertEqual(process_identities(f.journal.read_complete("K-38")), {})
                step = f.outside_history()
                f.restart()
                self.assertTrue(f.inventory())
                with f.outside_environment() as spawn:
                    f.engine._start_outside("K-38", step, "echo ordinary-test")
                    spawn.assert_called_once()

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
