"""PT-288: kompletność modelu tożsamości względem kodu zapisującego dziennik i konsumentów.

Oczekiwany katalog zapisów, konsumentów i skutków wyznacza AST (`cardflow_identity_catalog`),
niezależnie od tabel modelu i generatora. Porównanie z modelem i macierzą barier zawodzi przy
nowym miejscu zapisu, rodzaju zdarzenia, dyskryminatorze, polu, konsumencie lub skutku bez
klasyfikacji/testu. Kontrole mutacyjne usuwają z kopii modelu całe zdarzenie lub pojedyncze
pole i potwierdzają porażkę porównania.
"""
import ast
import copy
import importlib
from pathlib import Path
from types import MappingProxyType
import unittest

from . import cardflow_identity_catalog as cat
from tide_daemon.box import state as bs
from tide_daemon.cardflow import processidentity as pi

REPORT = Path(__file__).resolve().parents[2] / "docs" / "agents" / "pt-288" / "catalog.md"


# ------------------------------------------------------------------ porównanie katalogu z modelem

def model_view(events=pi.EVENTS, actions=pi.ACTIONS, box=pi.BOX_EVENTS):
    return {"events": events, "actions": actions, "box": box}


def mismatches(catalog, model):
    """Lista rozbieżności katalogu zapisów i klasyfikacji modelu (pusta = zgodność)."""
    problems = []
    events, actions, box = model["events"], model["actions"], model["box"]
    seen_events, seen_fields, seen_actions = set(), set(), set()
    for write in catalog.writes:
        where = f"{write.path}:{write.line} {write.function}"
        if write.store == "box":
            spec = box.get(write.kind)
            if spec is None:
                problems.append(f"{where}: zdarzenie pudełka {write.kind} bez klasyfikacji")
                continue
            seen_events.add(("box", write.kind))
            allowed = set(spec.fields or {}) | set(pi.BOX_ENVELOPE) | {"box"}
            for name in sorted(set(write.fields) - allowed):
                problems.append(f"{where}: pole {write.kind}.{name} bez klasyfikacji")
            seen_fields.update(("box", write.kind, name) for name in write.fields)
            continue
        spec = events.get(write.kind)
        if spec is None:
            problems.append(f"{where}: zdarzenie {write.kind} bez klasyfikacji")
            continue
        seen_events.add(("card", write.kind))
        fields = spec.fields
        label = write.kind
        if spec.role == "action":
            if write.discriminator == "<replay>":
                # Replay przepisuje akcję istniejącego zamiaru; dozwolony tylko bez nowych pól procesu.
                if write.fields != cat.ANY and write.fields - {"action", "key", "ok", "error", "payload",
                                                               "youtrack", "permanent", "retryWriteSafe"}:
                    problems.append(f"{where}: replay z polami {sorted(write.fields)}")
                continue
            action = actions.get(write.discriminator)
            if action is None:
                problems.append(f"{where}: dyskryminator {write.kind}.action={write.discriminator!r} bez klasyfikacji")
                continue
            seen_actions.add(write.discriminator)
            if action.role == "metadata":
                continue   # ładunek akcji metadanych nie jest czytany (osobny test)
            fields = action.intent if write.kind == "intent" else action.result
            label = f"{write.kind}:{write.discriminator}"
        if write.fields == cat.ANY:
            if fields is not None:
                problems.append(f"{where}: dynamiczny ładunek {label} dla zdarzenia z rolą {spec.role}")
            continue
        if fields is None:
            continue
        for name in sorted(set(write.fields) - set(fields) - set(pi.CARD_ENVELOPE)):
            problems.append(f"{where}: pole {label}.{name} bez klasyfikacji")
        seen_fields.update(("card", label, name) for name in write.fields)
    # Odwrotnie: klasyfikacja „writer” musi mieć zapis w kodzie, inaczej jest historyczna.
    for kind, spec in events.items():
        if spec.source == "writer" and ("card", kind) not in seen_events:
            problems.append(f"model: {kind} oznaczone jako zapisywane, brak zapisu w kodzie")
        if spec.source == "historical" and ("card", kind) in seen_events:
            problems.append(f"model: {kind} oznaczone jako historyczne, a kod je zapisuje")
        for label, fields in _field_tables(kind, spec, actions):
            for name, field in fields.items():
                written = ("card", label, name) in seen_fields
                if field.source == "writer" and not written:
                    problems.append(f"model: pole {label}.{name} oznaczone jako zapisywane, brak w kodzie")
                if field.source == "historical" and written:
                    problems.append(f"model: pole {label}.{name} oznaczone jako historyczne, a kod je zapisuje")
    for name, action in actions.items():
        if action.source == "writer" and name not in seen_actions:
            problems.append(f"model: akcja {name} oznaczona jako zapisywana, brak zapisu w kodzie")
        if action.source == "historical" and name in seen_actions:
            problems.append(f"model: akcja {name} historyczna, a kod ją zapisuje")
    for kind, spec in box.items():
        if ("box", kind) not in seen_events:
            problems.append(f"model: zdarzenie pudełka {kind} bez zapisu w kodzie")
        for name, field in (spec.fields or {}).items():
            if name not in ("box", "ts") and ("box", kind, name) not in seen_fields and field.source == "writer":
                problems.append(f"model: pole pudełka {kind}.{name} bez zapisu w kodzie")
    return problems


def _field_tables(kind, spec, actions):
    if spec.role == "action":
        for name, action in actions.items():
            if action.role == "step-start":
                yield f"{kind}:{name}", (action.intent if kind == "intent" else action.result)
    elif spec.fields is not None:
        yield kind, spec.fields


class WriterCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = cat.writes()

    def test_every_journal_write_is_resolved(self):
        self.assertEqual(self.catalog.unresolved, [])
        self.assertGreater(len(self.catalog.writes), 150)
        # Każde miejsce zapisu ma lokalizację w kodzie.
        for write in self.catalog.writes:
            self.assertTrue((cat.PACKAGE / write.path).is_file())
            self.assertGreater(write.line, 0)

    def test_every_manual_resolution_is_used(self):
        used = self.catalog.used_scopes
        self.assertEqual(set(cat.DYNAMIC) - used, set())
        self.assertEqual(set(cat.REPLAY_ACTIONS) - used, set())
        self.assertEqual(set(cat.CALLBACKS) - used, set())
        self.assertEqual({a for a in cat.WORK_BY_ACTION} - {k[1] for k in used if k[0] == "work"}, set())

    def test_catalog_matches_model_classification(self):
        self.assertEqual(mismatches(self.catalog, model_view()), [])

    def test_catalog_covers_writers_outside_engine(self):
        paths = {w.path for w in self.catalog.writes}
        for expected in ("cardflow/engine.py", "cardflow/trialhold.py", "cardflow/consent.py",
                         "cardflow/caretakerchat.py", "cardflow/stepnotes.py", "cardflow/boxstep.py",
                         "cardflow/tracker.py", "box/state.py", "box/builder.py", "box/lifecycle.py",
                         "box/packs.py", "box/runner.py"):
            self.assertIn(expected, paths)
        kinds = {w.kind for w in self.catalog.writes}
        self.assertTrue({"outside.started", "outside.finished", "step.entered", "box.use"} <= kinds)

    def test_removing_any_event_or_field_from_a_model_copy_fails(self):
        """Kontrola mutacyjna: kompletność nie zależy od generatora ani od tabeli autora."""
        written = {(w.store, w.kind) for w in self.catalog.writes}
        for store, kind in sorted(written):
            with self.subTest(store=store, kind=kind):
                if store == "card":
                    events = dict(pi.EVENTS)
                    del events[kind]
                    model = model_view(events=MappingProxyType(events))
                else:
                    box = dict(pi.BOX_EVENTS)
                    del box[kind]
                    model = model_view(box=MappingProxyType(box))
                self.assertTrue(mismatches(self.catalog, model))
        checked = 0
        for kind, spec in pi.EVENTS.items():
            for label, fields in _field_tables(kind, spec, pi.ACTIONS):
                for name, field in fields.items():
                    if field.source != "writer":
                        continue
                    reduced = MappingProxyType({k: v for k, v in fields.items() if k != name})
                    with self.subTest(event=label, field=name):
                        if ":" in label:
                            action = pi.ACTIONS[label.split(":")[1]]
                            replaced = copy.copy(action)
                            object.__setattr__(replaced, "intent" if kind == "intent" else "result", reduced)
                            model = model_view(actions=MappingProxyType({**pi.ACTIONS, label.split(":")[1]: replaced}))
                        else:
                            events = dict(pi.EVENTS)
                            events[kind] = pi.Event(spec.role, reduced, spec.reason, spec.source)
                            model = model_view(events=MappingProxyType(events))
                        self.assertTrue(mismatches(self.catalog, model))
                        checked += 1
        self.assertGreater(checked, 300)
        for action in sorted({w.discriminator for w in self.catalog.writes if w.kind in ("intent", "result")}
                             - {"<replay>"}):
            with self.subTest(action=action):
                actions = {k: v for k, v in pi.ACTIONS.items() if k != action}
                self.assertTrue(mismatches(self.catalog, model_view(actions=MappingProxyType(actions))))

    def test_new_write_site_kind_discriminator_or_field_fails(self):
        base = self.catalog.writes[0]
        for extra in (cat.Write("card", "cardflow/engine.py", "X.new", 1, "step.brand-new", "", frozenset()),
                      cat.Write("card", "cardflow/engine.py", "X.new", 1, "intent", "future-action",
                                frozenset({"action", "key"})),
                      cat.Write("card", "cardflow/engine.py", "X.new", 1, "outside.finished", "",
                                frozenset({"step", "pid"})),
                      cat.Write("card", "cardflow/engine.py", "X.new", 1, "step.decision", "", cat.ANY),
                      cat.Write("box", "box/state.py", "X.new", 1, "box.use", "", frozenset({"step", "pid"}))):
            with self.subTest(write=extra):
                catalog = cat.Catalog(writes=[*self.catalog.writes, extra])
                self.assertTrue(mismatches(catalog, model_view()))
        self.assertTrue(base)

    def test_box_model_matches_box_journal_vocabulary(self):
        self.assertEqual(set(pi.BOX_EVENTS), set(bs.EVENTS))


class DynamicScopes(unittest.TestCase):
    """Osobne kontrole każdego ręcznie rozstrzygniętego zakresu dynamicznego."""

    def function(self, path, qualname):
        for node in ast.walk(cat.module(path)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and cat.qualname(node) == qualname:
                return node
        self.fail(f"brak funkcji {path}:{qualname}")

    def test_placement_entry_scope_matches_its_source(self):
        function = self.function("cardflow/boxstep.py", "Placement.entry")
        keys = set()
        for node in ast.walk(function):
            if isinstance(node, ast.Dict):
                keys |= {k.value for k in node.keys if isinstance(k, ast.Constant)}
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
                keys.add(node.slice.value)
        for key, (rule, fields) in cat.DYNAMIC.items():
            if rule == "placement-entry":
                self.assertEqual(fields, keys, key)

    def test_step_base_context_scope_matches_its_source(self):
        function = self.function("cardflow/engine.py", "FlowEngine._step_base_context")
        keys = set()
        for ret in (n for n in ast.walk(function) if isinstance(n, ast.Return)):
            self.assertIsInstance(ret.value, ast.Tuple)
            dict_node = ret.value.elts[1]
            self.assertIsInstance(dict_node, ast.Dict)
            keys |= {k.value for k in dict_node.keys}
        for key, (rule, fields) in cat.DYNAMIC.items():
            if rule == "step-base-context":
                self.assertEqual(fields, keys, key)

    def test_metadata_payload_scopes_belong_to_unread_events(self):
        catalog = cat.writes()
        for write in catalog.writes:
            if write.fields != cat.ANY:
                continue
            with self.subTest(write=write):
                spec = pi.EVENTS[write.kind]
                if spec.role == "action":
                    self.assertTrue(write.discriminator == "<replay>"
                                    or pi.ACTIONS[write.discriminator].role == "metadata")
                else:
                    self.assertEqual((spec.role, spec.fields), ("metadata", None))

    def test_reducer_ignores_any_payload_of_metadata_events_and_actions(self):
        payload = {"step": "legacy", "key": "legacy", "ticket": "PT-176", "unit": "x", "branch": "ticket/pt-176",
                   "consoleUnit": "", "processFile": [], "runner": "box", "boxSlug": "", "outside": True,
                   "choice": "run"}
        card = {"type": "card.started", "project": "proj"}
        for kind, spec in pi.EVENTS.items():
            if spec.role != "metadata":
                continue
            with self.subTest(kind=kind):
                self.assertEqual(pi.process_identities([card, {**payload, "type": kind}]), {})
        for action, spec in pi.ACTIONS.items():
            if spec.role != "metadata":
                continue
            for kind in ("intent", "result"):
                with self.subTest(action=action, kind=kind):
                    self.assertEqual(pi.process_identities([card, {**payload, "type": kind, "action": action}]), {})

    def test_replay_discriminator_copies_a_recorded_intent(self):
        function = self.function("cardflow/engine.py", "FlowEngine._reconcile")
        assigned = [n for n in ast.walk(function) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Tuple) and "action" in [e.id for e in t.elts if isinstance(e, ast.Name)]
                            for t in n.targets)]
        self.assertEqual(len(assigned), 1)
        self.assertIn("intent.get('action')", ast.unparse(assigned[0].value))
        # Replay YouTracka (ładunek dynamiczny) dotyczy wyłącznie zamiarów oznaczonych youtrack.
        tracker = [n for n in ast.walk(function) if isinstance(n, ast.Call) and ast.unparse(n.func) == "self.tracker.run"]
        self.assertEqual(len(tracker), 1)
        parent = tracker[0]
        while not isinstance(parent, ast.If):
            parent = parent._parent
        self.assertEqual(ast.unparse(parent.test), "intent.get('youtrack')")

    def test_work_results_without_literal_scope_are_metadata_actions(self):
        for action in cat.WORK_BY_ACTION:
            with self.subTest(action=action):
                self.assertEqual(pi.ACTIONS[action].role, "metadata")
        self.assertNotIn("step-start", cat.WORK_BY_ACTION)


# ------------------------------------------------------------------ konsumenci, skutki i macierz barier

M = "daemon.tests.test_cardflow_identity_matrix"
INV = "daemon.tests.test_cardflow_trial_inventory"
PID = "daemon.tests.test_cardflow_process_identity"
FLOW = "daemon.tests.test_cardflow"
NEG = [f"{M}:BarrierMatrixNegative.test_every_generated_bad_history_of_ordinary_ticket_denies_all_barriers",
       f"{M}:BarrierMatrixNegative.test_every_generated_bad_history_of_protected_ticket_denies_all_barriers",
       f"{PID}:GeneratedHistoryBarriers.test_every_generated_unknown_history_denies_process_and_lifecycle_after_restart"]

# Bariera → konsumenci (z AST), punkt odmowy, obserwowany skutek oraz testy ujemne i dodatnie.
BARRIERS = {
    "model": dict(
        consumers=[("cardflow/state.py", "fold"), ("cardflow/trialhold.py", "preserves_project"),
                   ("cardflow/trialhold.py", "read_process_history"),
                   ("cardflow/journal.py", "_validate_process_identity"),
                   ("cardflow/journal.py", "Journal.read_complete")],
        denial="read_process_history: ValueError; fold: _identityHeld", effect="wspólny wynik redukcji",
        negative=NEG, positive=[f"{M}:BarrierMatrixPositive.test_model_and_strict_read"]),
    "inventory": dict(
        consumers=[("cardflow/trialhold.py", "inventory"), ("cardflow/trialhold.py", "public_inventory"),
                   ("cardflow/trialhold.py", "public_processes"), ("cardflow/trialhold.py", "_public_process_inventory"),
                   ("cardflow/trialhold.py", "_validate_box_event"), ("cardflow/engine.py", "FlowEngine._tick_card")],
        denial="inventory() False", effect="zgoda inwentaryzacji kart i dawnych publicznych prób",
        negative=NEG + [f"{INV}:InventoryTest.test_box_history_validates_envelope_and_process_identity_before_filtering"],
        positive=[f"{M}:BarrierMatrixPositive.test_inventory_and_public_inventory"]),
    "process-check": dict(
        consumers=[("cardflow/engine.py", "FlowEngine._trial_check"), ("cardflow/engine.py", "FlowEngine._process_state"),
                   ("cardflow/engine.py", "FlowEngine._process_check")],
        denial="FlowError process-history / k38-trial-held", effect="końcowa kontrola operacji przed skutkiem",
        negative=NEG, positive=[f"{M}:BarrierMatrixPositive.test_process_check_returns_current_state_for_the_same_identity"]),
    "outside": dict(
        consumers=[("cardflow/engine.py", "FlowEngine._outside_history"),
                   ("cardflow/engine.py", "FlowEngine._outside_recheck"),
                   ("cardflow/engine.py", "FlowEngine._consent_recheck")],
        denial="_outside_history pod journal.lock przed Popen; _outside_ready FlowError",
        effect="Popen niezależnego outside; zaakceptowanie zakończenia",
        negative=NEG + [f"{M}:Review10OutsideCompletionUnit.test_explicit_wrong_completion_unit_denies_every_barrier_across_restart",
                        f"{M}:Review9And8Probes.test_round9_completion_without_separate_start_denies_across_restart"],
        positive=[f"{M}:BarrierMatrixPositive.test_independent_outside_starts_one_process",
                  f"{M}:BarrierMatrixPositive.test_outside_ready_accepts_completion_of_the_inherited_unit",
                  f"{M}:Review10OutsideCompletionUnit.test_omitted_or_matching_completion_unit_accepts_completion_across_restart",
                  f"{INV}:InventoryTest.test_valid_outside_with_omitted_ticket_runs_after_restart"]),
    "advance-resume": dict(
        consumers=[("cardflow/engine.py", "FlowEngine._advance"), ("cardflow/engine.py", "FlowEngine._resume_step"),
                   ("cardflow/engine.py", "FlowEngine._complete_manual_finish")],
        denial="_process_state/_process_check przed wznowieniem", effect="wznowienie kroku, outside po zgodzie",
        negative=NEG, positive=[f"{M}:BarrierMatrixPositive.test_advance_runs_consented_outside_once",
                                f"{FLOW}:RestartInProcessTest.test_restart_during_a_step_neither_loses_it_nor_duplicates_events"]),
    "step-launch": dict(
        consumers=[("cardflow/engine.py", "FlowEngine._launch"), ("cardflow/engine.py", "FlowEngine._launch.launch"),
                   ("cardflow/engine.py", "FlowEngine._launch_full_planning")],
        denial="_process_check pod journal.lock przed runner.start/terminal.open", effect="start kroku, Konsola planowania",
        negative=NEG, positive=[f"{FLOW}:RestartInProcessTest.test_restart_during_a_step_neither_loses_it_nor_duplicates_events",
                                "daemon.tests.test_cardflow_full_planning:FullPlanningTest.test_restart_does_not_open_second_console"]),
    "console": dict(
        consumers=[("cardflow/engine.py", "FlowEngine.enter"), ("cardflow/engine.py", "FlowEngine._stop_for_enter"),
                   ("cardflow/engine.py", "FlowEngine._recover_console_openings"),
                   ("cardflow/engine.py", "FlowEngine._stop_interactive_transfer"),
                   ("cardflow/engine.py", "FlowEngine._resolve_transfer")],
        denial="_process_check pod journal.lock przed terminal.open", effect="otwarcie Konsoli, przekazanie",
        negative=NEG + [f"{M}:Review9And8Probes.test_round8_incomplete_console_denies_across_restart"],
        positive=[f"{M}:Review9And8Probes.test_round8_complete_console_with_empty_step_unit_allows_effects_across_restart",
                  "daemon.tests.test_cardflow_limits:InteractiveTransferTest.test_restart_after_interactive_transfer_decision_opens_one_console",
                  "daemon.tests.test_cardflow_limits:InteractiveTransferTest.test_restart_opens_persisted_console_task_once"]),
    "recovery": dict(
        consumers=[("cardflow/engine.py", "FlowEngine._reconcile"), ("cardflow/engine.py", "FlowEngine._recover_started_steps"),
                   ("cardflow/engine.py", "FlowEngine.tick")],
        denial="read_process_history/_process_check przed odtworzeniem", effect="odzyskanie kroku po restarcie",
        negative=NEG, positive=[f"{FLOW}:RestartInProcessTest.test_restart_after_launch_result_recovers_the_running_step"]),
    "poll-stop": dict(
        consumers=[("cardflow/engine.py", "FlowEngine._poll"), ("cardflow/engine.py", "FlowEngine._guard"),
                   ("cardflow/engine.py", "FlowEngine._stop")],
        denial="_process_check przed zatrzymaniem/zakończeniem", effect="zatrzymanie jednostki, zakończenie kroku",
        negative=NEG, positive=[f"{FLOW}:RestartInProcessTest.test_restart_after_store_move_before_flow_event_finishes_the_card"]),
    "diagnostic": dict(
        consumers=[("cardflow/engine.py", "FlowEngine.register_trial_diagnostic"),
                   ("cardflow/engine.py", "FlowEngine._resolve_trial_diagnostic"),
                   ("cardflow/engine.py", "FlowEngine._start_trial_diagnostic.safe")],
        denial="read_process_history pod journal.lock; FlowError", effect="procedura PT-273 (atrapa)",
        negative=NEG, positive=[f"{M}:BarrierMatrixPositive.test_diagnostic_registration_and_consumption_start_procedure_once",
                                f"{M}:BarrierMatrixPositive.test_trial_gate_procedure_starts_with_clean_inventory"]),
    "box-lifecycle": dict(
        consumers=[("box/lifecycle.py", "active_steps"), ("box/builder.py", "Builder.running_steps"),
                   ("cardflow/boxstep.py", "builder_of"), ("box/lifecycle.py", "Lifecycle.close_card")],
        denial="require_idle → BoxBusy", effect="incus.stop/start/delete/snapshot_restore",
        negative=NEG, positive=[f"{M}:BarrierMatrixPositive.test_lifecycle_stop_stops_the_project_box",
                                f"{M}:BarrierMatrixPositive.test_reset_restores_the_stopped_project_box",
                                f"{M}:BarrierMatrixPositive.test_box_start_reaches_admission_for_the_project_box",
                                f"{M}:BarrierMatrixPositive.test_close_card_deletes_only_the_card_copy"]),
    "idle-tick": dict(
        consumers=[("box/lifecycle.py", "tick")],
        denial="read_process_history/public_inventory/preserves_project → bez stop/cleanup",
        effect="incus.stop bezczynnego pudełka, packs.cleanup",
        negative=NEG, positive=[f"{M}:BarrierMatrixPositive.test_idle_tick_stops_only_the_idle_project_box"]),
    "packs": dict(
        consumers=[("box/packs.py", "cleanup")],
        denial="PackError przed device_remove", effect="incus.device_remove własnych paczek",
        negative=NEG, positive=[f"{M}:BarrierMatrixPositive.test_packs_cleanup_removes_only_own_devices"]),
    "box-network": dict(
        consumers=[("cardflow/boxstep.py", "recover_network")],
        denial="held → bez odtworzenia polityki sieci", effect="odtworzenie polityki proxy kroku",
        negative=[f"{M}:BoxNetworkRecovery.test_unknown_history_releases_nothing_across_restart"],
        positive=[f"{M}:BoxNetworkRecovery.test_valid_history_releases_own_ended_step_and_orphan_reservation_across_restart",
                  "daemon.tests.test_cardflow_box_ports:PortPlacementTest.test_recovered_start_preserves_owner_and_releases_ports_when_finished"]),
}

# Skutki z AST → bariera i strażnik. Strażnik bezpośredni: funkcja skutku wywołuje jedną z nazw
# przed skutkiem. Strażnik pośredni: wskazane funkcje wywołujące mają kontrolę przed wywołaniem.
_LOCK = True
EFFECT_SCOPE = {
    ("cardflow/engine.py", "FlowEngine._start_outside", "Popen"): ("outside", "direct", {"_outside_history"}, _LOCK),
    ("cardflow/engine.py", "FlowEngine._launch_full_planning", "Konsola"): ("step-launch", "direct", {"_process_check"}, _LOCK),
    ("cardflow/engine.py", "FlowEngine.enter", "Konsola"): ("console", "direct", {"_process_check"}, _LOCK),
    ("cardflow/engine.py", "FlowEngine._launch.launch", "start kroku"): ("step-launch", "direct", {"_process_check"}, _LOCK),
    ("cardflow/engine.py", "FlowEngine._recover_console_openings", "Konsola"): ("console", "direct", {"_process_check"}, _LOCK),
    ("box/lifecycle.py", "Lifecycle.stop", "incus.stop"): ("box-lifecycle", "direct", {"require_idle"}, False),
    ("box/lifecycle.py", "Lifecycle.close_card", "incus.delete"): ("box-lifecycle", "direct", {"require_idle"}, False),
    ("box/lifecycle.py", "tick", "packs.cleanup"): ("idle-tick", "direct", {"active_steps"}, False),
    ("box/packs.py", "cleanup", "incus.device_remove"): ("packs", "direct", {"read_process_history"}, False),
    ("box/builder.py", "Builder.reset", "incus.stop"): ("box-lifecycle", "direct", {"require_idle"}, False),
    ("box/builder.py", "Builder.destroy", "incus.delete"): ("box-lifecycle", "direct", {"running_steps"}, False),
    ("box/builder.py", "Builder.copy_for_card", "incus.delete"): ("box-lifecycle", "direct", {"require_idle"}, False),
    ("box/trialgate.py", "TrialGate.start_registered", "procedura próby"): ("diagnostic", "direct", {"inventory"}, False),
    ("box/builder.py", "Builder._build", "incus.delete"):
        ("box-lifecycle", [("box/builder.py", "Builder._execute_build", {"require_idle"})], None, False),
    ("box/admission.py", "start", "incus.start"):
        ("box-lifecycle", [("box/builder.py", "Builder._execute_build", {"require_idle"}),
                           ("box/builder.py", "Builder.reset", {"require_idle"}),
                           ("box/lifecycle.py", "Lifecycle.start", {"require_idle"}),
                           ("cardflow/engine.py", "FlowEngine._launch", {"_process_check"})], None, False),
    ("box/runner.py", "BoxRunner._ensure_locked", "incus.device_remove"):
        ("step-launch", [("cardflow/engine.py", "FlowEngine._launch", {"_process_check"})], None, False),
    ("box/runner.py", "BoxRunner._ensure_locked", "incus.stop"):
        ("step-launch", [("cardflow/engine.py", "FlowEngine._launch", {"_process_check"})], None, False),
    ("tasks/agentcmd.py", "Background.start", "Popen"):
        ("step-launch", [("cardflow/engine.py", "FlowEngine._launch.launch", {"_process_check"})], None, False),
    ("box/home.py", "_attach", "incus.stop"):
        ("box-lifecycle", [("box/builder.py", "Builder.reset", {"require_idle"}),
                           ("box/lifecycle.py", "Lifecycle.close_card", {"require_idle"})], None, False),
    ("box/command.py", "_execute", "start kroku"):
        ("box-lifecycle", [("box/command.py", "run_command", {"require_public", "require_idle"})], None, False),
    ("box/command.py", "_execute", "incus.device_remove"):
        ("box-lifecycle", [("box/command.py", "run_command", {"require_public", "require_idle"})], None, False),
    ("box/command.py", "_finish", "incus.device_remove"):
        ("box-lifecycle", [("box/command.py", "run_command", {"require_public", "require_idle"})], None, False),
    ("box/runner.py", "_demo_guarded", "start kroku"):
        ("box-lifecycle", [("box/runner.py", "_demo", {"require_public"})], None, False),
}
# Skutki bez związku z tożsamością procesów kart (uzasadnienie zamiast bariery).
OUT_OF_SCOPE = {
    ("box/base.py", "BaseBuilder._provision", "incus.stop"): "obraz bazowy Tide, bez kroków kart",
    ("box/base.py", "BaseBuilder._build", "incus.delete"): "obraz bazowy Tide, bez kroków kart",
    ("box/base.py", "BaseBuilder._stop_quietly", "incus.stop"): "obraz bazowy Tide, bez kroków kart",
    ("box/test_artifacts.py", "run", "Popen"): "skrypt wewnątrz pudełka uruchomionego kroku",
    ("cardflow/hostingcli.py", "read_json", "Popen"): "odczyt gh/glab dla podpowiedzi linków",
    ("docker_monitor.py", "DockerMonitor._open_tab", "Konsola"): "karta Konsoli kontenera Dockera",
    ("reports/summarize.py", "call_claude", "Popen"): "podsumowanie raportu porannego",
    ("task_monitor.py", "TaskMonitor._launch", "Konsola"):
        "zadania użytkownika; zadania kart otwiera silnik po _process_check, a console_allowed (PT-261) je wstrzymuje",
    ("tasks/launch.py", "run_flow", "Popen"): "opakowanie programu wewnątrz już uruchomionej jednostki kroku",
    ("worktrees/cancellation.py", "Cancellation.run", "Popen"): "anulowanie sesji worktree",
    ("worktrees/launchers.py", "Launchers.spawn", "Popen"): "aplikacje uruchamiane przez użytkownika",
}


def _function(path, qualname):
    for node in ast.walk(cat.module(path)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and cat.qualname(node) == qualname:
            return node
    return None


def _calls(function, names):
    return [n for n in cat._function_body_nodes(function) if isinstance(n, ast.Call) and (
        (isinstance(n.func, ast.Attribute) and n.func.attr in names)
        or (isinstance(n.func, ast.Name) and n.func.id in names))]


def _under_journal_lock(node):
    current = getattr(node, "_parent", None)
    while current is not None and not isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if isinstance(current, ast.With) and any(ast.unparse(i.context_expr).endswith("journal.lock")
                                                 for i in current.items):
            return current
        current = getattr(current, "_parent", None)
    return None


class ConsumerCatalog(unittest.TestCase):
    def test_every_consumer_from_code_has_a_barrier(self):
        found = {(c.path, c.function) for c in cat.consumers()}
        assigned = [entry for barrier in BARRIERS.values() for entry in barrier["consumers"]]
        self.assertEqual(len(assigned), len(set(assigned)), "konsument przypisany dwa razy")
        self.assertEqual(found - set(assigned), set(), "konsument bez bariery")
        self.assertEqual(set(assigned) - found, set(), "bariera wskazuje nieistniejącego konsumenta")

    def test_every_barrier_names_existing_negative_and_positive_tests(self):
        for name, barrier in BARRIERS.items():
            for kind in ("negative", "positive"):
                self.assertTrue(barrier[kind], f"{name}: brak testów {kind}")
                for ref in barrier[kind]:
                    module, _, attr = ref.partition(":")
                    with self.subTest(barrier=name, test=ref):
                        cls, _, method = attr.partition(".")
                        owner = getattr(importlib.import_module(module), cls)
                        self.assertTrue(callable(getattr(owner, method, None)), ref)
            self.assertTrue(barrier["denial"] and barrier["effect"])

    def test_every_effect_from_code_is_guarded_or_out_of_scope(self):
        found = {(e.path, e.function, e.effect) for e in cat.effects()}
        self.assertEqual(found - set(EFFECT_SCOPE) - set(OUT_OF_SCOPE), set(), "skutek bez bariery")
        self.assertEqual((set(EFFECT_SCOPE) | set(OUT_OF_SCOPE)) - found, set(), "nieaktualny wpis skutku")
        effects = {(e.path, e.function, e.effect): e for e in cat.effects()}
        for key, (barrier, guard, names, locked) in EFFECT_SCOPE.items():
            with self.subTest(effect=key):
                self.assertIn(barrier, BARRIERS)
                effect = effects[key]
                if guard == "direct":
                    function = _function(effect.path, effect.function)
                    calls = _calls(function, names)
                    self.assertTrue(any(c.lineno <= effect.line for c in calls), "brak kontroli przed skutkiem")
                    if locked:
                        # Końcowa kontrola i skutek w tej samej sekcji pod blokadą dziennika.
                        node = next(n for n in ast.walk(function) if isinstance(n, ast.Call)
                                    and n.lineno == effect.line)
                        block = _under_journal_lock(node)
                        self.assertIsNotNone(block, "skutek poza blokadą dziennika")
                        inside = [c for c in calls if _under_journal_lock(c) is block and c.lineno <= effect.line]
                        self.assertTrue(inside, "brak świeżej kontroli pod tą samą blokadą")
                else:
                    for path, qualname, guard_names in guard:
                        function = _function(path, qualname)
                        self.assertIsNotNone(function, (path, qualname))
                        self.assertTrue(_calls(function, guard_names), (path, qualname, guard_names))

    def test_delegated_box_operations_reach_the_shared_consumer(self):
        # builder.steps_source → lifecycle.active_steps; require_idle → running_steps.
        builder_of = _function("cardflow/boxstep.py", "builder_of")
        self.assertIn("active_steps", ast.unparse(builder_of))
        running = _function("box/builder.py", "Builder.running_steps")
        self.assertIn("active_steps", ast.unparse(running))
        require = _function("box/builder.py", "Builder.require_idle")
        self.assertTrue(_calls(require, {"running_steps"}))

    def test_report_matches_catalog(self):
        """Raport pokrycia (docs/agents/pt-288/catalog.md) jest aktualny względem kodu i modelu."""
        self.assertEqual(REPORT.read_text(encoding="utf-8"), render_report())


def render_report():
    catalog = cat.writes()
    lines = ["# PT-288: katalog zapisów, konsumentów i macierz barier", "",
             "Plik generowany przez `daemon/tests/test_cardflow_identity_catalog.py` (`render_report`);",
             "test kompletności porównuje go z kodem. Katalog zapisów pochodzi z AST, nie z modelu.", "",
             "## Zapisy dziennika (rodzaj → rola w modelu → miejsca)", "",
             "| Dziennik | Zdarzenie | Dyskryminator | Rola | Pola | Miejsca |", "| --- | --- | --- | --- | --- | --- |"]
    rows = {}
    for w in catalog.writes:
        key = (w.store, w.kind, w.discriminator)
        entry = rows.setdefault(key, [set(), set()])
        entry[0] |= {"*"} if w.fields == cat.ANY else set(w.fields)
        entry[1].add(f"{w.path}:{w.function}")
    for (store, kind, disc), (fields, places) in sorted(rows.items()):
        if store == "box":
            role = pi.BOX_EVENTS[kind].role
        elif kind in ("intent", "result"):
            role = "replay" if disc == "<replay>" else pi.ACTIONS[disc].role
        else:
            role = pi.EVENTS[kind].role
        lines.append(f"| {store} | `{kind}` | {disc or '—'} | {role} | {', '.join(sorted(fields)) or '—'} | "
                     f"{'<br>'.join(sorted(places))} |")
    lines += ["", "## Role pól zdarzeń procesu", "",
              "| Zdarzenie | Pole | Rola | Typ | Niepuste | Poprzednik | Domena | Reguła | Źródło |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for kind, spec in pi.EVENTS.items():
        for label, fields in _field_tables(kind, spec, pi.ACTIONS):
            if spec.role in ("metadata",):
                continue
            for name, f in fields.items():
                if f.role == "neutral":
                    continue
                lines.append(f"| `{label}` | {name} | {f.role} | {f.type} | {'tak' if f.nonempty else '—'} | "
                             f"{f.predecessor or '—'} | {f.domain or '—'} | {f.rule or '—'} | {f.source} |")
    lines += ["", "Pozostałe pola zdarzeń procesu mają rolę `neutral` (dane operacyjne); pełna lista w modelu.", "",
              "## Konsumenci i bariery", "",
              "| Bariera | Konsumenci | Punkt odmowy | Skutek | Testy ujemne | Testy dodatnie |",
              "| --- | --- | --- | --- | --- | --- |"]
    for name, b in BARRIERS.items():
        lines.append(f"| {name} | {'<br>'.join(f'{p}:{q}' for p, q in b['consumers'])} | {b['denial']} | "
                     f"{b['effect']} | {'<br>'.join(r.split(':')[1] for r in b['negative'])} | "
                     f"{'<br>'.join(r.split(':')[1] for r in b['positive'])} |")
    lines += ["", "## Skutki", "", "| Skutek | Funkcja | Bariera / uzasadnienie |", "| --- | --- | --- |"]
    for (path, function, effect), (barrier, guard, names, locked) in sorted(EFFECT_SCOPE.items()):
        how = ("pod journal.lock " if locked else "") + (
            ", ".join(sorted(names)) if guard == "direct" else "przez " + ", ".join(q for _, q, _ in guard))
        lines.append(f"| {effect} | {path}:{function} | {barrier}: {how} |")
    for (path, function, effect), reason in sorted(OUT_OF_SCOPE.items()):
        lines.append(f"| {effect} | {path}:{function} | poza zakresem: {reason} |")
    return "\n".join(lines) + "\n"
