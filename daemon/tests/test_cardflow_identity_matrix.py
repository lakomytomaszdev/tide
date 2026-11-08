"""PT-288: generator z modelu, sondy PT-277 rund 8–10 i macierz barier przed/po restarcie.

Kontrole ujemne dowodzą braku skutku licznikami atrap i niezmiennością dzienników;
kontrole dodatnie obserwują konkretny skutek na atrapie właściwego zasobu.
"""
import collections
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from . import cardflow_identity_generator as gen
from . import test_cardflow_trial_inventory as fixtures
from tide_daemon.box import packs, trialgate as gate
from tide_daemon.cardflow import boxstep, trialhold
from tide_daemon.cardflow.engine import FlowError
from tide_daemon.cardflow.processidentity import process_identities
from tide_daemon.cardflow.state import fold

RANK = {"ok": 0, "unknown": 1, "inconsistent": 2}


def verdict(history):
    identities = process_identities(list(history))
    worst = max((RANK[i.state] for i in identities.values()), default=0)
    return identities, worst


class GeneratedModelHistories(unittest.TestCase):
    def test_every_field_mutation_follows_its_role(self):
        for case in gen.cases():
            with self.subTest(kind=case.kind, field=case.field, variant=case.variant, ticket=case.ticket,
                              group=case.group):
                before = json.dumps(case.history, sort_keys=True)
                identities, worst = verdict(case.history)
                if case.expected == gen.LEGAL:
                    self.assertEqual(worst, 0, {str(k): v.state for k, v in identities.items()})
                else:
                    self.assertGreaterEqual(worst, max(1, RANK.get(case.exact, 1)),
                                            {str(k): v.state for k, v in identities.items()})
                    # Wadliwy ślad nie traci ochrony ani nazwy procesu; chroniony ticket pozostaje chroniony.
                    if any(e.get("ticket") == "PT-176" for e in case.history):
                        self.assertTrue(any(i.protected for i in identities.values()))
                self.assertEqual(json.dumps(case.history, sort_keys=True), before)

    def test_bad_histories_never_recover_in_any_later_prefix(self):
        for case in gen.cases():
            if case.expected != gen.BAD:
                continue
            previous = {}
            for length in range(1, len(case.history) + 1):
                current = process_identities(list(case.history[:length]))
                for key, identity in previous.items():
                    self.assertIn(key, current)
                    self.assertGreaterEqual(RANK[current[key].state], RANK[identity.state])
                    self.assertTrue(identity.projects <= current[key].projects)
                    if identity.protected:
                        self.assertTrue(current[key].protected)
                previous = current

    def test_generator_accounts_for_every_model_event_field_and_variant(self):
        seen = collections.defaultdict(set)
        groups = collections.defaultdict(set)
        for case in gen.cases():
            seen[(case.kind, case.field)].add((case.variant, case.ticket))
            groups[case.kind].add((case.group, case.ticket))
        for kind in gen.model_kinds():
            for field in gen.fields_of(kind):
                with self.subTest(kind=kind, field=field):
                    self.assertEqual(seen[(kind, field)], set(itertools.product(gen.VARIANTS, gen.TICKETS)))
            with self.subTest(kind=kind):
                self.assertIn(("field", "PT-176"), groups[kind])
        for kind, required in gen.PREDECESSORS.items():
            for name in required:
                self.assertEqual({v for v, _ in seen[(kind, name)]}, {"removed", "moved", "foreign"})
            # Poprzednik z innego projektu, zlecenia i próby (M2 review 1).
            self.assertEqual(seen[(kind, "domain")], set(itertools.product(
                ("foreign-project", "new-order", "new-attempt"), gen.TICKETS)))
        # Rozróżnienie legalnych i wadliwych mutacji: obie klasy są niepuste dla każdego zdarzenia.
        outcomes = collections.defaultdict(set)
        for case in gen.cases():
            outcomes[case.kind].add(case.expected)
        for kind in gen.model_kinds():
            self.assertEqual(outcomes[kind], {gen.LEGAL, gen.BAD}, kind)

    def test_generator_accounts_for_every_box_event_field_and_variant(self):
        """M1 review 1: BOX_EVENTS (koperta i ślad kroku) mają pełną macierz mutacji."""
        seen = collections.defaultdict(set)
        outcomes = collections.defaultdict(set)
        for case in gen.box_cases():
            seen[(case.kind, case.field)].add((case.variant, case.ticket))
            outcomes[case.kind].add(case.expected)
        for kind in gen.pi.BOX_EVENTS:
            for field in gen.box_fields(kind):
                with self.subTest(kind=kind, field=field):
                    self.assertEqual(seen[(f"box:{kind}", field)], set(itertools.product(gen.VARIANTS, gen.TICKETS)))
            self.assertEqual(outcomes[f"box:{kind}"], {gen.LEGAL, gen.BAD}, kind)

    def test_every_box_field_mutation_follows_its_role(self):
        for case in gen.box_cases():
            with self.subTest(kind=case.kind, field=case.field, variant=case.variant, ticket=case.ticket,
                              group=case.group):
                errors = [gen.pi.box_event_error(e, "proj") for e in case.box]
                if case.expected == gen.LEGAL:
                    self.assertEqual(errors, [""] * len(case.box))
                else:
                    self.assertTrue(any(errors))

    def test_reviewer_noncanonical_box_trace_is_rejected(self):
        """Sonda M1: `box=" proj"`, `step=" demo-old"` nie są przycinane ani pomijane."""
        for event in ({**gen.box_event("box.use", "demo-old"), "box": " proj"},
                      gen.box_event("box.use", " demo-old"),
                      {**gen.box_event("box.use", " demo-old"), "box": " proj"},
                      {**gen.box_event("box.use", "demo-old"), "box": "other-box"},
                      {**gen.box_event("box.use", "demo-old"), "box": "proj-k-x"}):
            with self.subTest(event=event):
                self.assertTrue(gen.pi.box_event_error(event, "proj"))
        for box in ("proj", "proj-k-38"):
            self.assertEqual(gen.pi.box_event_error({**gen.box_event("box.use", "demo-old"), "box": box}, "proj"), "")

    def test_reviewer_foreign_project_and_attempt_predecessors_are_not_inherited(self):
        """Sonda M2: outside.finished po zmianie projektu albo próby bez nowego startu outside."""
        before, finish = gen.base("outside.finished", "PT-999")
        start = next(e for e in before if e["type"] == "step.started")
        for history in ([*before, {"type": "card.started", "project": gen.OTHER_PROJECT}, finish],
                        [*before, {**start, "attempt": 2}, finish],
                        [*[{**e, "attempt": 1} if e is start else e for e in before], {**start, "attempt": 2},
                         finish]):
            with self.subTest(history=history):
                self.assertEqual(process_identities(history)[gen.STEP].state, "unknown")
        # Nowa próba z własnym startem outside jest legalna; stara generacja nie psuje nowej.
        outside = next(e for e in before if e["type"] == "outside.started")
        history = [*before, {**start, "attempt": 2}, outside, finish]
        self.assertEqual(process_identities(history)[gen.STEP].state, "ok")

    def test_legal_omissions_from_round_8_and_inherited_units_stay_ok(self):
        for name, history in gen.legal_histories():
            with self.subTest(name=name):
                identities, worst = verdict(history)
                self.assertEqual(worst, 0)
                self.assertFalse(identities[gen.STEP].held)

    def test_console_ack_inherits_only_from_the_same_steps_complete_console(self):
        # Start rozmowy opiekuna z własnym kompletem Konsoli jest poprzednikiem odzyskanego otwarcia.
        chat = gen._start("PT-999", unit="", kind="ticket", consoleUnit="console.scope", processFile="/console.json")
        plain = gen._start("PT-999", unit="")
        for kind in ("step.console_opened", "step.console_closed"):
            ack = {"type": kind, "step": gen.STEP}
            self.assertEqual(process_identities([gen._card(), chat, ack])[gen.STEP].state, "ok")
            self.assertEqual(process_identities([gen._card(), chat, {**ack, "consoleUnit": "other"}])[gen.STEP].state,
                             "inconsistent")
            # Wejście lub Konsola innego kroku nie są poprzednikiem; późniejsze wejście nie naprawia.
            other = {**gen._entered(), "step": gen.OTHER_STEP}
            for history in ([gen._card(), plain, other, ack], [gen._card(), plain, ack, gen._entered()],
                            [gen._card(), {**chat, "step": gen.OTHER_STEP}, plain, ack]):
                self.assertEqual(process_identities(history)[gen.STEP].state, "unknown")

    def test_trimmed_text_never_becomes_trusted_identity(self):
        for value in (" outside.scope", "outside.scope ", "\toutside.scope"):
            before, event = gen.base("outside.finished", "PT-999")
            self.assertNotEqual(process_identities([*before, {**event, "unit": value}])[gen.STEP].state, "ok")
            before, event = gen.base("outside.started", "PT-999")
            self.assertEqual(process_identities([*before, {**event, "unit": value}])[gen.STEP].state, "unknown")
        before, event = gen.base("step.started", "PT-999")
        self.assertEqual(process_identities([*before, {**event, "step": " " + gen.STEP}])[" " + gen.STEP].state,
                         "unknown")


def round10_history(ticket, fields):
    """Sonda rundy 10: outside.started(unit="outside.scope") i jawny unit zakończenia."""
    before, start = gen.base("outside.started", ticket)
    events = [{k: v for k, v in e.items() if k not in ("runner", "box", "boxSlug", "card")} for e in (*before, start)]
    return [*events, {**gen._outside_finish(ticket), **fields}]


ROUND10_UNITS = (("other.scope", "inconsistent"), (None, "unknown"), ("", "unknown"), ("   ", "unknown"),
                 ("\t", "unknown"), ([], "unknown"), (["outside.scope"], "unknown"), (17, "unknown"),
                 (0, "unknown"), ({}, "unknown"))


class Harness(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.InventoryTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.project()
        self.paths = [Path(self.f.journal.path("K-38")), Path(self.f.journal.mirror_path("K-38"))]
        self.initial = self.f.journal.read_complete("K-38")

    def write(self, history):
        """Zapis surowy (także pól odrzucanych przez ścisły parser) do obu kopii dziennika."""
        events = [{**e, "v": 1, "seq": seq, "card": "K-38", "ts": "2026-11-08T00:00:00+01:00"}
                  for seq, e in enumerate([*self.initial, *history], 1)]
        content = "".join(json.dumps(e) + "\n" for e in events).encode()
        for path in self.paths:
            path.write_bytes(content)
        self.f.restart()
        return content

    def append(self, history):
        for path in self.paths:
            path.write_bytes("".join(json.dumps(e) + "\n" for e in self.initial).encode())
        self.f.restart()
        for event in history:
            self.f.journal.append("K-38", event["type"], **{k: v for k, v in event.items() if k != "type"})


class Review10OutsideCompletionUnit(Harness):
    """Port sondy PT-277 rundy 10 z zachowaniem negatywnych asercji barier."""

    def test_explicit_wrong_completion_unit_denies_every_barrier_across_restart(self):
        f = self.f
        failed = []
        for (unit, state), ticket, restarted in itertools.product(ROUND10_UNITS, gen.TICKETS, (False, True)):
            history = round10_history(ticket, {"unit": unit})
            self.write(history)
            if restarted:
                f.restart()
            with self.subTest(unit=unit, ticket=ticket, restarted=restarted):
                identity = process_identities(f.journal.read_complete("K-38"))[gen.STEP]
                self.assertEqual(identity.state, state)
                self.assertEqual(identity.protected, ticket == "PT-176")
                with self.assertRaises(ValueError):
                    trialhold.read_process_history(f.journal, "K-38")
                self.assertFalse(f.inventory())
                with f.outside_environment() as spawn:
                    with self.assertRaises(FlowError):
                        f.engine._outside_ready("K-38", {"step": gen.STEP, "ticket": ticket},
                                                {"command": "echo legacy"}, {"choice": "run"})
                    spawn.assert_not_called()
                independent = f.outside_history()
                with f.outside_environment() as spawn:
                    with self.assertRaises(FlowError):
                        f.engine._start_outside("K-38", independent, "echo ordinary-test")
                    self.assertEqual(spawn.call_count, 0)
                f.assert_lifecycle_preserved()
                if identity.state == "ok":
                    failed.append((unit, ticket, restarted))
        self.assertEqual(failed, [])

    def test_omitted_or_matching_completion_unit_accepts_completion_across_restart(self):
        f = self.f
        for fields, restarted in itertools.product(({}, {"unit": "outside.scope"}), (False, True)):
            self.append(round10_history("PT-999", fields))
            if restarted:
                f.restart()
            with self.subTest(fields=fields, restarted=restarted):
                st = fold(trialhold.read_process_history(f.journal, "K-38"))
                self.assertEqual(st.process_identities[gen.STEP].state, "ok")
                with f.outside_environment() as spawn:
                    decision = f.engine._outside_ready("K-38", st.steps[gen.STEP], st.finished[gen.STEP],
                                                       st.step_decisions[gen.STEP])
                    spawn.assert_not_called()
                # Zakończenie zostało zaakceptowane: wznowienie dostaje kod wyjścia i koniec wyniku.
                self.assertIn("Kod wyjścia: 0", decision["answer"])
                self.assertEqual(decision["choice"], "run")
                independent = f.outside_history()
                with f.outside_environment() as spawn:
                    f.engine._start_outside("K-38", independent, "echo ordinary-test")
                    spawn.assert_called_once()
                    self.assertEqual(spawn.call_args.args[0][-1], "echo ordinary-test")


class Review9And8Probes(Harness):
    """Porty sond rund 9 (zakończenie bez startu) i 8 (niepełna Konsola)."""

    def test_round9_completion_without_separate_start_denies_across_restart(self):
        f = self.f
        for ticket, late, unit, restarted in itertools.product(gen.TICKETS, (False, True),
                                                                ({}, {"unit": "outside.scope"}), (False, True)):
            before, start = gen.base("outside.started", ticket)
            history = [*before, {**gen._outside_finish(ticket), **unit}, *([start] if late else [])]
            self.write(history)
            if restarted:
                f.restart()
            with self.subTest(ticket=ticket, late=late, unit=unit, restarted=restarted):
                self.assertEqual(process_identities(history)[gen.STEP].state, "unknown")
                self.assertFalse(f.inventory())
                with f.outside_environment() as spawn:
                    with self.assertRaises(FlowError):
                        f.engine._outside_ready("K-38", {"step": gen.STEP, "ticket": ticket},
                                                {"command": "echo legacy"}, {"choice": "run"})
                    with self.assertRaises(FlowError):
                        f.engine._start_outside("K-38", {"step": "K-38-PT-999-9", "ticket": "PT-999"}, "echo x")
                    spawn.assert_not_called()
                f.assert_lifecycle_preserved()

    def test_round8_incomplete_console_denies_across_restart(self):
        f = self.f
        complete = {"consoleUnit": "console.scope", "processFile": "/console.json"}
        variants = [{}, *({**complete, k: v} for k in complete for v in ("", "   ", None, [], 17)),
                    *({k: v for k, v in complete.items() if k != missing} for missing in complete)]
        for fields, ticket, restarted in itertools.product(variants, gen.TICKETS, (False, True)):
            before, _ = gen.base("step.entered", ticket)
            entered = {**{k: v for k, v in gen._entered().items() if k not in complete}, **fields}
            history = [*before, entered, {"type": "step.console_opened", "step": gen.STEP},
                       {"type": "step.console_closed", "step": gen.STEP}]
            self.write(history)
            if restarted:
                f.restart()
            with self.subTest(fields=fields, ticket=ticket, restarted=restarted):
                self.assertNotEqual(process_identities(history)[gen.STEP].state, "ok")
                self.assertFalse(f.inventory())
                with f.outside_environment() as spawn:
                    with self.assertRaises(FlowError):
                        f.engine._start_outside("K-38", {"step": "K-38-PT-999-9", "ticket": "PT-999"}, "echo x")
                    spawn.assert_not_called()
                f.assert_lifecycle_preserved()

    def test_round8_complete_console_with_empty_step_unit_allows_effects_across_restart(self):
        f = self.f
        for restarted in (False, True):
            before, entered = gen.base("step.entered", "PT-999")
            history = [{k: v for k, v in e.items() if k not in ("runner", "box", "boxSlug")} for e in before]
            history += [entered, {"type": "step.console_opened", "step": gen.STEP},
                        {"type": "step.console_closed", "step": gen.STEP},
                        {"type": "step.finished", "step": gen.STEP, "ticket": "PT-999", "result": "done"}]
            self.append(history)
            if restarted:
                f.restart()
            with self.subTest(restarted=restarted):
                st = fold(trialhold.read_process_history(f.journal, "K-38"))
                self.assertEqual(st.process_identities[gen.STEP].data["unit"], "")
                self.assertEqual(st.process_identities[gen.STEP].data["consoleUnit"], "console.scope")
                step = f.outside_history()
                with f.outside_environment() as spawn:
                    f.engine._start_outside("K-38", step, "echo ordinary-test")
                    spawn.assert_called_once()


# ------------------------------------------------------------------ macierz barier

def _incus(**extra):
    incus = mock.Mock(project="tide")
    incus.instance.return_value = {"status": "Running"}
    incus.operations.return_value = []
    incus.list.return_value = []
    for key, value in extra.items():
        setattr(incus, key, value)
    return incus


def _running_until_stopped():
    """Atrapa Incusa: pudełko projektu działa, dopóki nie dostanie incus.stop."""
    incus = _incus()
    stopped = []
    incus.stop.side_effect = lambda name: stopped.append(name)
    incus.instance.side_effect = lambda name: {"status": "Stopped" if name in stopped else "Running"} \
        if name == "proj" else None
    return incus


def _builder(f, incus, idle_hours=0):
    settings = SimpleNamespace(enabled=True, idle_hours=idle_hours, ip_wait_seconds=1)
    builder = fixtures.Builder(settings, incus=incus, policy=object(), clock=lambda: 100, wall=lambda: 10 ** 10)
    builder.steps_source = lambda slug, name: fixtures.lifecycle.active_steps(builder, slug, name, f.engine)
    return builder, settings


# Bariery katalogu (`test_cardflow_identity_catalog.BARRIERS`) wykonywane przez macierz ujemną.
# Katalog sprawdza, że to dokładnie wszystkie bariery z kodu.
MATRIX_BARRIERS = ("outside", "process-check", "console", "advance-resume", "model", "inventory", "recovery",
                   "poll-stop", "step-launch", "diagnostic", "box-lifecycle", "packs", "idle-tick", "box-network")
# Dziennik pudełka czytają wyłącznie te bariery (public_processes/active_steps); pozostałe czytają
# tylko dziennik karty. Nieosiągalność sprawdza `test_cardflow_identity_catalog` (AST konsumentów).
BOX_JOURNAL_BARRIERS = ("inventory", "diagnostic", "box-lifecycle", "idle-tick")

# Zakończony krok pudełka i osierocona rezerwacja portów: przy znanej historii recover_network
# zwolniłby je (kontrola dodatnia BoxNetworkRecovery), więc brak zwolnień dowodzi odmowy.
COMPANION = ({"type": "step.started", "step": "box-step", "ticket": "PT-999", "kind": "ticket", "runner": "box",
              "box": "proj", "boxSlug": "proj", "unit": "box.scope"},
             {"type": "step.finished", "step": "box-step", "ticket": "PT-999", "result": "done"},
             {"type": "box.ports", "step": "never-started", "box": "proj", "boxSlug": "proj",
              "owner": "K-38/never-started"})


def _deny_box(test, f, ran):
    """Bariery czytające dziennik pudełka (public_processes/active_steps)."""
    test.assertFalse(trialhold.public_inventory(f.engine))
    test.assertFalse(trialhold.inventory(f.engine, "K-38", fold([])))
    ran.add("inventory")
    gate_, procedure = f.procedure()
    with test.assertRaises(gate.TrialDenied):
        gate_.start_registered("new", consent=True, bootstrap=True,
                               inventory=lambda: trialhold.inventory(f.engine, "K-38", fold([])))
    test.assertFalse(procedure.start.called)
    ran.add("diagnostic")
    incus = _incus()
    builder, _ = _builder(f, incus)
    with mock.patch("tide_daemon.box.admission.read_memory", return_value=(64 * 1024 ** 3, 60 * 1024 ** 3)):
        for operation in (lambda: fixtures.lifecycle.Lifecycle(builder).start("proj"),
                          lambda: builder.reset("proj"), lambda: fixtures.lifecycle.Lifecycle(builder).stop("proj")):
            with test.assertRaises(fixtures.BoxBusy):
                operation()
    incus.list.return_value = [{"name": "proj"}]   # destroy rozważa wyłącznie istniejące instancje
    with test.assertRaises(fixtures.BoxBusy):
        builder.destroy("proj")
    incus.instance.return_value = {"status": "Running", "config": {
        fixtures.bs.COPY_OF_KEY: "proj", fixtures.bs.CARD_KEY: "K-38"}}
    fixtures.lifecycle.Lifecycle(builder).close_card("proj", "K-38")
    for name in ("stop", "start", "delete", "snapshot_restore"):
        test.assertFalse(getattr(incus, name).called, name)
    ran.add("box-lifecycle")
    f.assert_lifecycle_preserved()
    ran.add("idle-tick")


def deny_everywhere(test, f, content):
    """Ujemna kontrola wszystkich barier; brak skutku potwierdzają liczniki i bajty dzienników.

    Zwraca zbiór wykonanych barier (raport pokrycia: historia × bariera × restart)."""
    ran = set()
    stale = dict(step=gen.STEP, ticket="PT-999", kind="ticket")
    with f.outside_environment() as spawn:
        for barrier, call in (
                ("outside", lambda: f.engine._start_outside("K-38", {"step": "K-38-PT-999-9", "ticket": "PT-999"},
                                                            "echo x")),
                ("outside", lambda: f.engine._outside_ready("K-38", stale, {"command": "echo x"}, {"choice": "run"})),
                ("process-check", lambda: f.engine._process_check("K-38", stale)),
                ("console", lambda: f.engine.enter("K-38", gen.STEP))):
            with test.assertRaises(FlowError):
                call()
            ran.add(barrier)
        f.engine._advance("K-38", fold([]))
        ran.add("advance-resume")
        test.assertFalse(spawn.called, "niepełna historia dotarła do Popen")
    # Stan wywołującego jest celowo pusty: inwentaryzacja sama ściśle czyta pełną historię.
    test.assertFalse(trialhold.inventory(f.engine, "K-38", fold([])))
    ran.add("inventory")
    with test.assertRaises(ValueError):
        trialhold.read_process_history(f.journal, "K-38")
    ran.add("model")
    with f.outside_environment() as spawn:
        f.engine.tick()
        f.engine._recover_started_steps("K-38", fold([]))
        ran.add("recovery")
        f.engine._recover_console_openings("K-38", fold([]))
        test.assertFalse(f.engine._stop_for_enter("K-38", stale))
        test.assertFalse(f.engine._stop_interactive_transfer("K-38", stale))
        test.assertFalse(f.engine._stop("K-38", stale, "stopped", ""))
        f.engine._poll("K-38", stale)
        f.engine._guard("K-38", stale)
        ran.add("poll-stop")
        for barrier, call in (
                ("advance-resume", lambda: f.engine._resume_step("K-38", fold([]), stale, {"choice": "retry"})),
                ("step-launch", lambda: f.engine._launch(SimpleNamespace(id="K-38"), None, None, "PT-999", None,
                                                         None, "new-step", [])),
                ("step-launch", lambda: f.engine._launch_full_planning(SimpleNamespace(id="K-38"), None, None, None,
                                                                       "new-step", [], [], ""))):
            with test.assertRaises(FlowError):
                call()
            ran.add(barrier)
        test.assertFalse(spawn.called)
    gate_, procedure = f.procedure()
    f.engine.trial_gate = gate_
    with test.assertRaises(FlowError):
        f.engine.register_trial_diagnostic()
    with test.assertRaises(gate.TrialDenied):
        gate_.start_registered("new", consent=True, bootstrap=True,
                               inventory=lambda: trialhold.inventory(f.engine, "K-38", fold([])))
    test.assertFalse(procedure.start.called)
    ran.add("diagnostic")
    incus = _incus()
    incus.device_show.return_value = ["tide-pack-k-38-logs"]
    builder, _ = _builder(f, incus)
    with mock.patch("tide_daemon.box.admission.read_memory", return_value=(64 * 1024 ** 3, 60 * 1024 ** 3)):
        for operation in (lambda: fixtures.lifecycle.Lifecycle(builder).start("proj"),
                          lambda: builder.reset("proj"), lambda: fixtures.lifecycle.Lifecycle(builder).stop("proj")):
            with test.assertRaises(fixtures.BoxBusy):
                operation()
    incus.instance.return_value = {"status": "Running", "config": {
        fixtures.bs.COPY_OF_KEY: "proj", fixtures.bs.CARD_KEY: "K-38"}}
    fixtures.lifecycle.Lifecycle(builder).close_card("proj", "K-38")
    ran.add("box-lifecycle")
    with test.assertRaises(packs.PackError):
        packs.cleanup("proj", "K-38", incus, f.journal)
    ran.add("packs")
    for name in ("stop", "start", "delete", "device_remove", "snapshot_restore"):
        test.assertFalse(getattr(incus, name).called, name)
    f.engine.box_proxy = mock.Mock(listen="", port=0)
    with mock.patch.object(boxstep, "release_ports") as ports, \
            mock.patch.object(boxstep, "_release_resources") as resources, \
            mock.patch("tide_daemon.box.refguard.finish", return_value={}) as guard:
        boxstep.recover_network(f.engine)
    for effect in (ports, resources, guard):
        effect.assert_not_called()
    ran.add("box-network")
    f.assert_lifecycle_preserved()
    ran.add("idle-tick")
    for path in (Path(f.journal.path("K-38")), Path(f.journal.mirror_path("K-38"))):
        test.assertEqual(path.read_bytes(), content)
    return ran


class BarrierMatrixNegative(Harness):
    def run_cases(self, ticket):
        seen = set()
        executed = collections.Counter()
        for case in gen.cases():
            if case.expected != gen.BAD or case.ticket != ticket:
                continue
            key = json.dumps(case.history, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            content = self.write([*case.history, *COMPANION])
            for restarted in (False, True):
                if restarted:
                    self.f.restart()
                with self.subTest(kind=case.kind, field=case.field, variant=case.variant, group=case.group,
                                  restarted=restarted):
                    # Każda wadliwa historia (pola, poprzedniki, domeny, poprawki po błędzie)
                    # przechodzi przez wszystkie bariery przed i po restarcie.
                    ran = deny_everywhere(self, self.f, content)
                    self.assertEqual(ran, set(MATRIX_BARRIERS))
                    executed.update((case.group, restarted, barrier) for barrier in ran)
        # Raport wykonanych kombinacji: każda grupa × restart × bariera ma tyle przebiegów, ile historii.
        groups = collections.Counter(c.group for c in gen.cases() if c.expected == gen.BAD and c.ticket == ticket)
        for group in groups:
            for restarted, barrier in itertools.product((False, True), MATRIX_BARRIERS):
                self.assertGreater(executed[(group, restarted, barrier)], 0, (group, restarted, barrier))
        return executed

    def test_every_generated_bad_history_of_ordinary_ticket_denies_all_barriers(self):
        self.run_cases("PT-999")

    def test_every_generated_bad_history_of_protected_ticket_denies_all_barriers(self):
        self.run_cases("PT-176")


class BoxJournalMatrix(Harness):
    """M1 review 1: wadliwy dziennik pudełka odmawia w barierach, które go czytają; poprawny przepuszcza."""

    def write_box(self, journal):
        path = self.f.projects / "proj/box/journal.jsonl"
        content = "".join(json.dumps(e) + "\n" for e in journal).encode()
        path.write_bytes(content)
        return path, content

    def test_every_generated_bad_box_journal_denies_box_barriers_across_restart(self):
        card = self.write([])
        for case in gen.box_cases():
            if case.expected != gen.BAD:
                continue
            path, content = self.write_box(case.box)
            for restarted in (False, True):
                if restarted:
                    self.f.restart()
                with self.subTest(kind=case.kind, field=case.field, variant=case.variant, ticket=case.ticket,
                                  group=case.group, restarted=restarted):
                    self.assertEqual(trialhold.public_processes(str(self.f.board)),
                                     [{"state": "unknown", "path": str(path)}])
                    ran = set()
                    _deny_box(self, self.f, ran)
                    self.assertEqual(ran, set(BOX_JOURNAL_BARRIERS))
                    self.assertEqual(path.read_bytes(), content)
                    # Dziennik karty dostaje wyłącznie zapis inwentaryzacji publicznych prób (metadane).
                    for journal in (Path(self.f.journal.path("K-38")), Path(self.f.journal.mirror_path("K-38"))):
                        data = journal.read_bytes()
                        self.assertTrue(data.startswith(card))
                        added = [json.loads(line)["type"] for line in data[len(card):].splitlines()]
                        self.assertLessEqual(set(added), {"k38.public-inventory"})

    def test_reviewer_noncanonical_demo_trace_denies_stop_and_procedure_across_restart(self):
        """Sonda M1: `box=" proj"`, `step=" demo-old"` — wcześniej incus.stop=1 i procedure.start=1."""
        self.write([])
        event = {**gen.box_event("box.use", " demo-old"), "box": " proj"}
        self.write_box([gen.box_event("box.ready"), event])
        for restarted in (False, True):
            if restarted:
                self.f.restart()
            with self.subTest(restarted=restarted):
                self.assertNotEqual(trialhold.public_processes(str(self.f.board)), [])
                incus = _running_until_stopped()
                builder, _ = _builder(self.f, incus)
                with self.assertRaises(fixtures.BoxBusy):
                    fixtures.lifecycle.Lifecycle(builder).stop("proj")
                self.assertEqual(incus.stop.call_count, 0)
                trial_gate, procedure = self.f.procedure()
                with self.assertRaises(gate.TrialDenied):
                    trial_gate.start_registered("new", consent=True, inventory=self.f.inventory)
                self.assertEqual(procedure.start.call_count, 0)

    def test_valid_box_journals_allow_stop_and_procedure_across_restart(self):
        """Kontrola dodatnia: legalne mutacje (inny krok, inny znacznik, kopia karty) przepuszczają skutki."""
        self.write([])
        legal = [c for c in gen.box_cases() if c.expected == gen.LEGAL and c.ticket == "PT-999"]
        self.assertTrue(legal)
        for case in legal:
            # Inny krok w legalnej mutacji naprawdę zajmuje pudełko: zwalniamy oba, aby skutek
            # zależał wyłącznie od tożsamości dziennika, nie od zwykłego zajęcia (osobna kontrola hostowa).
            self.write_box([*case.box, *(gen.box_event("box.release", s) for s in (gen.OTHER_STEP, gen.BOX_STEPS["PT-999"]))])
            for restarted in (False, True):
                if restarted:
                    self.f.restart()
                with self.subTest(kind=case.kind, field=case.field, variant=case.variant, restarted=restarted):
                    self.assertEqual(trialhold.public_processes(str(self.f.board)), [])
                    incus = _running_until_stopped()
                    builder, _ = _builder(self.f, incus)
                    with mock.patch("tide_daemon.box.admission.snapshot"):
                        fixtures.lifecycle.Lifecycle(builder).stop("proj")
                    incus.stop.assert_called_once_with("proj")
                    trial_gate, procedure = self.f.procedure()
                    trial_gate.start_registered("new", consent=True, inventory=self.f.inventory)
                    procedure.start.assert_called_once()


class BoxNetworkRecovery(Harness):
    """recover_network: zwolnienia i odtwarzanie wyłącznie przy znanej pełnej historii."""

    def recover(self, bad, restarted):
        history = [{"type": "card.started", "project": "proj"},
                   {"type": "step.started", "step": "box-step", "ticket": "PT-999", "kind": "ticket",
                    "runner": "box", "box": "proj", "boxSlug": "proj", "unit": "box.scope"},
                   {"type": "step.finished", "step": "box-step", "ticket": "PT-999", "result": "done"},
                   {"type": "box.ports", "step": "never-started", "box": "proj", "boxSlug": "proj",
                    "owner": "K-38/never-started"}]
        if bad:
            history.append({**gen._outside_finish("PT-999"), "step": "orphan-step"})
        content = self.write(history)
        if restarted:
            self.f.restart()
        self.f.engine.box_proxy = mock.Mock(listen="", port=0)
        with mock.patch.object(boxstep, "release_ports") as ports, \
                mock.patch.object(boxstep, "_release_resources") as resources, \
                mock.patch("tide_daemon.box.refguard.finish", return_value={}) as guard:
            boxstep.recover_network(self.f.engine)
        for path in self.paths:
            self.assertEqual(path.read_bytes(), content)
        return ports, resources, guard

    def test_unknown_history_releases_nothing_across_restart(self):
        for restarted in (False, True):
            ports, resources, guard = self.recover(True, restarted)
            ports.assert_not_called()
            resources.assert_not_called()
            guard.assert_not_called()

    def test_valid_history_releases_own_ended_step_and_orphan_reservation_across_restart(self):
        for restarted in (False, True):
            ports, resources, _ = self.recover(False, restarted)
            ports.assert_called_once()
            self.assertEqual(ports.call_args.args[0]["boxPortOwner"], "K-38/never-started")
            self.assertEqual([c.args[1]["step"] for c in resources.call_args_list], ["box-step"])


class BarrierMatrixPositive(Harness):
    """Prawidłowa historia kontrolna każdej bariery; skutek obserwowany na atrapie przed/po restarcie."""

    def each(self):
        for (name, history), restarted in itertools.product(gen.legal_histories(), (False, True)):
            host = [{k: v for k, v in e.items() if k not in ("runner", "box", "boxSlug", "card")} for e in history]
            host += [{"type": "step.finished", "step": gen.STEP, "ticket": "PT-999", "result": "done"}]
            self.append(host)
            if restarted:
                self.f.restart()
            # Bieżąca historia i wariant restartu są w self.case (opis porażki w tracebacku).
            self.case = (name, restarted)
            yield name

    def test_model_and_strict_read(self):
        for _ in self.each():
            events = trialhold.read_process_history(self.f.journal, "K-38")
            st = fold(events)
            self.assertFalse(trialhold.preserves_project(st, "proj"))
            self.assertIs(st.steps[gen.STEP].get("_identityHeld"), False)

    def test_inventory_and_public_inventory(self):
        for _ in self.each():
            self.assertTrue(self.f.inventory())
            self.assertTrue(trialhold.public_inventory(self.f.engine))

    def test_independent_outside_starts_one_process(self):
        for _ in self.each():
            step = self.f.outside_history()
            with self.f.outside_environment() as spawn:
                self.f.engine._start_outside("K-38", step, "echo ordinary-test")
                spawn.assert_called_once()
                self.assertEqual(spawn.call_args.args[0][-1], "echo ordinary-test")

    def test_advance_runs_consented_outside_once(self):
        for _ in self.each():
            self.f.outside_history()
            with self.f.outside_environment() as spawn:
                self.f.engine._advance("K-38", fold(self.f.journal.read("K-38")))
                spawn.assert_called_once()

    def test_process_check_returns_current_state_for_the_same_identity(self):
        for _ in self.each():
            st = self.f.engine._process_check("K-38", {"step": gen.STEP, "ticket": "PT-999"})
            self.assertEqual(st.process_identities[gen.STEP].state, "ok")
            with self.assertRaises(FlowError):
                self.f.engine._process_check("K-38", {"step": gen.STEP, "ticket": "PT-998"})

    def test_lifecycle_stop_stops_the_project_box(self):
        for _ in self.each():
            incus = _running_until_stopped()
            builder, _ = _builder(self.f, incus)
            with mock.patch("tide_daemon.box.admission.snapshot"):
                fixtures.lifecycle.Lifecycle(builder).stop("proj")
            incus.stop.assert_called_once_with("proj")

    def test_idle_tick_stops_only_the_idle_project_box(self):
        for _ in self.each():
            incus = _running_until_stopped()
            builder, settings = _builder(self.f, incus)
            with mock.patch.object(boxstep, "settings_of", return_value=settings), \
                    mock.patch.object(boxstep, "builder_of", return_value=builder), \
                    mock.patch("tide_daemon.box.admission.snapshot", return_value={"queue": []}), \
                    mock.patch("tide_daemon.box.admission.tick"), \
                    mock.patch("tide_daemon.box.packs.cleanup") as cleanup:
                fixtures.lifecycle.tick(self.f.engine)
            incus.stop.assert_called_once_with("proj")
            cleanup.assert_not_called()

    def test_reset_restores_the_stopped_project_box(self):
        for _ in self.each():
            incus = _incus()
            incus.instance.return_value = {"status": "Stopped"}
            builder, _ = _builder(self.f, incus)
            with mock.patch.object(builder, "ensure_home"):
                builder.reset("proj")
            incus.snapshot_restore.assert_called_once_with("proj", fixtures.bs.SNAPSHOT)
            incus.stop.assert_not_called()

    def test_box_start_reaches_admission_for_the_project_box(self):
        for _ in self.each():
            incus = _incus()
            incus.exec.return_value = 0
            builder, _ = _builder(self.f, incus)
            status = SimpleNamespace(usable=True, running=False, recipe_hash="h", warning=False, error="", state="")
            raw = fixtures.lifecycle.Lifecycle.start.__wrapped__.__wrapped__
            with mock.patch.object(builder, "status", return_value=status), \
                    mock.patch.object(builder, "load_profile", return_value={}), \
                    mock.patch.object(fixtures.lifecycle, "limits", return_value={}), \
                    mock.patch.object(fixtures.lifecycle.admission, "start") as start:
                raw(fixtures.lifecycle.Lifecycle(builder), "proj")
            start.assert_called_once_with(incus, "proj")

    def test_packs_cleanup_removes_only_own_devices(self):
        for _ in self.each():
            incus = _incus()
            incus.device_show.return_value = ["tide-pack-k-38-logs", "tide-pack-k-39-logs", "eth0"]
            packs.cleanup("proj", "K-38", incus, self.f.journal)
            self.assertEqual(sorted(c.args for c in incus.device_remove.call_args_list),
                             [("proj", "tide-pack-k-38-logs"),
                              (fixtures.bs.card_box_name("proj", "K-38"), "tide-pack-k-38-logs")])

    def test_close_card_deletes_only_the_card_copy(self):
        for _ in self.each():
            copy = fixtures.bs.card_box_name("proj", "K-38")
            incus = _incus()
            config = {"status": "Stopped", "config": {fixtures.bs.COPY_OF_KEY: "proj", fixtures.bs.CARD_KEY: "K-38"}}
            deleted = []
            incus.delete.side_effect = lambda name, force: deleted.append(name)
            incus.instance.side_effect = lambda name: (None if name in deleted else config) if name == copy \
                else {"status": "Stopped"}
            builder, _ = _builder(self.f, incus)
            with mock.patch.object(builder, "ensure_home"), mock.patch("tide_daemon.box.admission.snapshot"):
                fixtures.lifecycle.Lifecycle(builder).close_card("proj", "K-38")
            incus.delete.assert_called_once_with(copy, force=True)

    def test_diagnostic_registration_and_consumption_start_procedure_once(self):
        for _ in self.each():
            _, procedure = self.f.procedure()
            self.f.engine.trial_gate = gate.TrialGate(procedure=procedure)
            name = self.f.engine.register_trial_diagnostic()["step"]
            self.f.engine.resolve_step("K-38", {"step": name, "choice": "run"})
            procedure.start.assert_called_once()
            self.assertEqual(self.f.journal.read_complete("K-38")[-1]["type"], "k38.diagnostic-consumed")

    def test_trial_gate_procedure_starts_with_clean_inventory(self):
        for _ in self.each():
            trial_gate, procedure = self.f.procedure()
            trial_gate.start_registered("new", consent=True, inventory=self.f.inventory)
            procedure.start.assert_called_once()

    def test_outside_ready_accepts_completion_of_the_inherited_unit(self):
        for name in self.each():
            if not name.startswith("outside-finish"):
                continue
            st = fold(trialhold.read_process_history(self.f.journal, "K-38"))
            with self.f.outside_environment() as spawn:
                decision = self.f.engine._outside_ready("K-38", st.steps[gen.STEP], st.finished[gen.STEP],
                                                        st.step_decisions[gen.STEP])
                spawn.assert_not_called()
            self.assertIn("Kod wyjścia: 0", decision["answer"])
