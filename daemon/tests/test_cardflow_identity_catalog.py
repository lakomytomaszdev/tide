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
        allows_dynamic = spec.role == "metadata" and spec.dynamic
        explicit = cat.explicit(write.fields)
        if spec.role == "action":
            if write.discriminator == "<replay>":
                # Replay przepisuje akcję istniejącego zamiaru; dozwolony tylko bez nowych pól procesu.
                # map/ticket/title: znane klucze wyniku YouTracka (TrackerOutbox.run, M5 review 2).
                if explicit - {"action", "key", "ok", "error", "payload", "youtrack", "permanent", "retryWriteSafe",
                               "map", "ticket", "title"}:
                    problems.append(f"{where}: replay z polami {sorted(explicit)}")
                continue
            action = actions.get(write.discriminator)
            if action is None:
                problems.append(f"{where}: dyskryminator {write.kind}.action={write.discriminator!r} bez klasyfikacji")
                continue
            seen_actions.add(write.discriminator)
            fields = action.intent if write.kind == "intent" else action.result
            label = f"{write.kind}:{write.discriminator}"
            allows_dynamic = action.role == "metadata" and write.kind in action.dynamic
        # Zakres dynamiczny nie zwalnia z klasyfikacji pól jawnych (M4 review 1).
        if cat.dynamic(write.fields) and not allows_dynamic:
            problems.append(f"{where}: dynamiczny ładunek {label} bez jawnej zgody modelu (rola {spec.role})")
        if fields is None:
            problems.append(f"{where}: zdarzenie {label} bez tabeli pól")
            continue
        for name in sorted(explicit - set(fields) - set(pi.CARD_ENVELOPE)):
            problems.append(f"{where}: pole {label}.{name} bez klasyfikacji")
        seen_fields.update(("card", label, name) for name in explicit)
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
            fields = action.intent if kind == "intent" else action.result
            if fields is not None:
                yield f"{kind}:{name}", fields
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
                      cat.Write("card", "cardflow/engine.py", "X.new", 1, "step.decision", "", cat.DYN),
                      cat.Write("card", "cardflow/engine.py", "X.new", 1, "refs.guard", "",
                                frozenset({cat.ANY, "step", "pid"})),
                      cat.Write("card", "cardflow/engine.py", "X.new", 1, "result", "merge",
                                frozenset({cat.ANY, "action", "key", "pid"})),
                      cat.Write("card", "cardflow/engine.py", "X.new", 1, "intent", "cleanup",
                                frozenset({cat.ANY, "action", "key"})),
                      cat.Write("box", "box/state.py", "X.new", 1, "box.use", "", frozenset({"step", "pid"}))):
            with self.subTest(write=extra):
                catalog = cat.Catalog(writes=[*self.catalog.writes, extra])
                self.assertTrue(mismatches(catalog, model_view()))
        self.assertTrue(base)

    def test_new_explicit_field_beside_dynamic_payload_fails_from_code(self):
        """Sonda M4: `pid=123` obok `**base` w FlowEngine._guard_baseline musi trafić do katalogu
        i dać porażkę porównania bez zmiany modelu, generatora ani raportu."""
        tree = ast.parse((cat.PACKAGE / "cardflow/engine.py").read_text(encoding="utf-8"))
        owner = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_guard_baseline")
        calls = [n for n in ast.walk(owner) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "append" and ast.unparse(n.func.value).endswith("journal")
                 and any(k.arg is None and ast.unparse(k.value) == "base" for k in n.keywords)]
        self.assertEqual(len(calls), 1)
        calls[0].keywords.append(ast.keyword(arg="pid", value=ast.Constant(123)))
        with cat.overridden({"cardflow/engine.py": tree}):
            catalog = cat.writes()
        self.assertEqual(catalog.unresolved, [])
        probed = [w for w in catalog.writes if w.function == "FlowEngine._guard_baseline"]
        self.assertTrue(probed and all("pid" in w.fields and cat.dynamic(w.fields) for w in probed))
        problems = mismatches(catalog, model_view())
        self.assertTrue(any(".pid bez klasyfikacji" in p for p in problems), problems)
        # Bez sondy katalog jest zgodny (kontrola, że porażka pochodzi z nowego pola).
        self.assertEqual(mismatches(cat.writes(), model_view()), [])

    def test_new_literal_payload_field_is_not_hidden_by_dynamic_scope(self):
        """Sonda M5 review 2: `base["pid"] = 123` (oraz update/setdefault) przed `journal.append(..., **base)`
        w FlowEngine._guard_baseline trafia do katalogu obok zakresu dynamicznego i daje porażkę porównania."""
        statements = ('base["pid"] = 123', 'base.update(pid=123)', 'base.update({"pid": 123})',
                      'base.setdefault("pid", 123)', 'base["pid"] += 1')
        for statement in statements:
            with self.subTest(statement=statement):
                tree = ast.parse((cat.PACKAGE / "cardflow/engine.py").read_text(encoding="utf-8"))
                owner = next(n for n in ast.walk(tree)
                             if isinstance(n, ast.FunctionDef) and n.name == "_guard_baseline")
                owner.body.insert(len(owner.body) - 1, ast.parse(statement).body[0])
                with cat.overridden({"cardflow/engine.py": tree}):
                    catalog = cat.writes()
                self.assertEqual(catalog.unresolved, [])
                probed = [w for w in catalog.writes if w.function == "FlowEngine._guard_baseline"]
                self.assertTrue(probed and all("pid" in w.fields and cat.dynamic(w.fields) for w in probed))
                problems = mismatches(catalog, model_view())
                self.assertTrue(any(".pid bez klasyfikacji" in p for p in problems), problems)
        self.assertEqual(mismatches(cat.writes(), model_view()), [])

    def test_alias_of_record_is_catalogued(self):
        """Sonda M6 review 2: zapis przez alias/import po nazwie `box/state.record` jest w katalogu;
        odniesienie bez wywołania jest nierozstrzygnięte (zachowawczo), nie pomijane."""
        sources = ("from .state import record as save\ndef foo():\n    save('proj', 'box.use', 'proj', step='x', pid=123)\n",
                   "from .state import record\ndef foo():\n    record('proj', 'box.use', 'proj', step='x', pid=123)\n",
                   "from tide_daemon.box.state import record as save\n"
                   "def foo():\n    save('proj', 'box.use', 'proj', step='x', pid=123)\n",
                   "from tide_daemon.box import state as st\n"
                   "def foo():\n    st.record('proj', 'box.use', 'proj', step='x', pid=123)\n")
        for source in sources:
            with self.subTest(source=source):
                with cat.overridden({"box/new_probe.py": ast.parse(source)}):
                    catalog = cat.writes()
                found = [w for w in catalog.writes if w.path == "box/new_probe.py"]
                self.assertEqual([(w.store, w.kind, w.fields) for w in found],
                                 [("box", "box.use", frozenset({"step", "pid"}))])
                problems = mismatches(catalog, model_view())
                self.assertTrue(any("box.use.pid bez klasyfikacji" in p for p in problems), problems)
        for source in ("from . import state as bs\nsave = bs.record\n",
                       "from .state import record\ndef foo(run):\n    run(record)\n"):
            with self.subTest(source=source):
                with cat.overridden({"box/new_probe.py": ast.parse(source)}):
                    catalog = cat.writes()
                self.assertTrue([u for u in catalog.unresolved if u[0] == "box/new_probe.py"])

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
            if not cat.dynamic(write.fields):
                continue
            with self.subTest(write=write):
                spec = pi.EVENTS[write.kind]
                if spec.role == "action":
                    action = pi.ACTIONS.get(write.discriminator)
                    self.assertTrue(write.discriminator == "<replay>"
                                    or (action.role == "metadata" and write.kind in action.dynamic))
                else:
                    self.assertEqual((spec.role, spec.dynamic), ("metadata", True))
        # Zgoda modelu na zakres dynamiczny dotyczy wyłącznie metadanych (reduktor ich nie czyta).
        for kind, spec in pi.EVENTS.items():
            self.assertTrue(not spec.dynamic or spec.role == "metadata", kind)
        for name, action in pi.ACTIONS.items():
            self.assertTrue(not action.dynamic or action.role == "metadata", name)

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
                   # K-105 PT-415: kontrola tożsamości i stan procesu karty (pamięć według wersji dziennika).
                   ("cardflow/trialhold.py", "_checked_state"), ("cardflow/trialhold.py", "_remembered"),
                   ("cardflow/journal.py", "_validate_process_identity"),
                   # K-105 #2: ścisła reguła `read_complete` (z pamięcią) i `read_fresh` (bramki) — jeden parser.
                   ("cardflow/journal.py", "_parse_strict"),
                   # PT-295: audyt historii (osobny proces, tylko odczyt); odmowa modelu to błąd read-rejected.
                   ("cardflow/historyaudit_driver.py", "read_cards"),
                   # K-105 PT-412: czynnik spoczynku `held` (`_identityHeld`, `preserves_project`) — tożsamość
                   # chroniona albo nieznana wyklucza spoczynek karty (pełny przebieg z barierami jak dotąd).
                   ("cardflow/rest.py", "_held")],
        denial="read_process_history: ValueError; fold: _identityHeld", effect="wspólny wynik redukcji",
        negative=NEG, positive=[f"{M}:BarrierMatrixPositive.test_model_and_strict_read"]),
    "inventory": dict(
        consumers=[("cardflow/trialhold.py", "inventory"), ("cardflow/trialhold.py", "_inventory_items"),
                   ("cardflow/trialhold.py", "public_inventory"),
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
                   ("cardflow/engine.py", "FlowEngine._consent_recheck"),
                   # Bieżące uruchomienie outside ze wspólnego wyniku (M4/M7 review 2).
                   ("cardflow/engine.py", "FlowEngine._outside_ready"),
                   # K-105 PT-415: ponowna redukcja na świeżym odczycie przed skutkiem (wynik, zapis zakończenia).
                   ("cardflow/engine.py", "FlowEngine._outside_ready.fresh_run"),
                   ("cardflow/engine.py", "FlowEngine._outside_busy"),
                   ("cardflow/engine.py", "FlowEngine._outside_exit"),
                   ("cardflow/engine.py", "FlowEngine._seed_outside_waiting"),
                   # PT-373: klasa trwającego polecenia po restarcie z tego samego wspólnego wyniku (outside_runs).
                   ("cardflow/engine.py", "FlowEngine._started_heavy"),
                   # PT-329: przebieg próby testów po scaleniu (pseudokrok `merge-check-…`) — ta sama historia
                   # i ten sam wspólny wynik redukcji co prośba outside kroku.
                   ("cardflow/engine.py", "FlowEngine._merge_check_history"),
                   ("cardflow/engine.py", "FlowEngine._merge_check_run"),
                   # K-105 PT-415 (review 1, MAJOR 2): jak `_outside_ready.fresh_run` — ponowna redukcja na świeżym
                   # odczycie przed skutkiem próby (wynik z zakończenia, zatrzymanie jednostki, zapis wyniku).
                   ("cardflow/engine.py", "FlowEngine._merge_check_run.fresh_run")],
        denial="_outside_history pod journal.lock przed Popen; _outside_ready: stan outside_run ≠ ok → FlowError",
        effect="Popen niezależnego outside; zaakceptowanie zakończenia bieżącej próby; zajętość i kolejka miejsc",
        negative=NEG + [f"{M}:Review10OutsideCompletionUnit.test_explicit_wrong_completion_unit_denies_every_barrier_across_restart",
                        f"{M}:Review9And8Probes.test_round9_completion_without_separate_start_denies_across_restart",
                        f"{M}:Review2DomainProbes.test_m2_step_start_of_another_project_is_not_a_predecessor_across_restart",
                        f"{M}:Review2DomainProbes.test_m3_return_to_an_older_attempt_does_not_restore_its_outside_across_restart"],
        positive=[f"{M}:BarrierMatrixPositive.test_independent_outside_starts_one_process",
                  f"{M}:BarrierMatrixPositive.test_outside_ready_accepts_completion_of_the_inherited_unit",
                  f"{M}:Review2DomainProbes.test_m4_outside_ready_consumes_only_the_current_attempt_across_restart",
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
        consumers=[("cardflow/engine.py", "FlowEngine._enter_step"), ("cardflow/engine.py", "FlowEngine._stop_for_enter"),
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
                   ("cardflow/engine.py", "FlowEngine.tick"),
                   # K-105 PT-459: pętla odtwarzania ticku — stan procesu karty z fazy ticku (pamięć PT-415).
                   ("cardflow/engine.py", "FlowEngine._replay_starts")],
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
                   ("cardflow/boxstep.py", "builder_of"), ("box/lifecycle.py", "Lifecycle.close_card"),
                   # PT-264: zajętość pudełka przed zmianą lub odłączeniem montowań (active_steps).
                   ("box/mounts.py", "ProjectMounts._busy")],
        denial="require_idle → BoxBusy; ProjectMounts._busy → MountsBusy / bez odłączenia",
        effect="incus.stop/start/delete/snapshot_restore, zmiana i odłączenie montowań projektu",
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
        # K-113 PT-466: filtr prób K-38 uzgadniania sieci pamiętany według wersji dziennika; `recover_network` bierze
        # go z `network_state` (delegacja tej bariery, DELEGATIONS).
        consumers=[("cardflow/trialhold.py", "network_state"), ("cardflow/trialhold.py", "_network_filter")],
        denial="held → bez odtworzenia polityki sieci", effect="odtworzenie polityki proxy kroku",
        negative=[f"{M}:BoxNetworkRecovery.test_unknown_history_releases_nothing_across_restart",
                  "daemon.tests.test_cardflow_trial_network_memory:NetworkMemoryTest."
                  "test_unreadable_history_or_unknown_identity_is_not_remembered_and_has_no_effects"],
        positive=[f"{M}:BoxNetworkRecovery.test_valid_history_releases_own_ended_step_and_orphan_reservation_across_restart",
                  "daemon.tests.test_cardflow_box_ports:PortPlacementTest.test_recovered_start_preserves_owner_and_releases_ports_when_finished"]),
}

# Skutki z AST → bariera i strażnik. Strażnik bezpośredni: funkcja skutku wywołuje jedną z nazw
# przed skutkiem. Strażnik pośredni: wskazane funkcje wywołujące mają kontrolę przed wywołaniem.
_LOCK = True
EFFECT_SCOPE = {
    ("cardflow/engine.py", "FlowEngine._start_outside", "Popen"): ("outside", "direct", {"_outside_history"}, _LOCK),
    ("cardflow/engine.py", "FlowEngine._merge_check_start", "Popen"):
        ("outside", "direct", {"_merge_check_history"}, _LOCK),
    ("cardflow/engine.py", "FlowEngine._launch_full_planning", "Konsola"): ("step-launch", "direct", {"_process_check"}, _LOCK),
    ("cardflow/engine.py", "FlowEngine._enter_step", "Konsola"): ("console", "direct", {"_process_check"}, _LOCK),
    ("cardflow/engine.py", "FlowEngine._launch.launch", "start kroku"): ("step-launch", "direct", {"_process_check"}, _LOCK),
    ("cardflow/engine.py", "FlowEngine._recover_console_openings", "Konsola"): ("console", "direct", {"_process_check"}, _LOCK),
    ("box/lifecycle.py", "Lifecycle.stop", "incus.stop"): ("box-lifecycle", "direct", {"require_idle"}, False),
    ("box/lifecycle.py", "Lifecycle.close_card", "incus.delete"): ("box-lifecycle", "direct", {"require_idle"}, False),
    ("box/lifecycle.py", "tick", "packs.cleanup"): ("idle-tick", "direct", {"active_steps"}, False),
    # K-105 PT-415: `process_state` — ta sama kontrola tożsamości (model, kategoria „model”) i stan procesu karty.
    ("box/packs.py", "cleanup", "incus.device_remove"):
        ("packs", "direct", {"read_process_history", "process_state"}, False),
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
    # PT-264: zatrzymanie przed zmianą montowań tylko bez żywego kroku innego niż startowany
    # (_busy → active_steps pod lifecycle_lock); dotyczy kroku silnika, run i --demo.
    ("box/mounts.py", "ProjectMounts._ensure", "incus.stop"): ("box-lifecycle", "direct", {"_busy"}, False),
    # Odłączenie kopii wejścia (callback packs.delete_snapshot) tylko z niezajętego pudełka.
    ("box/mounts.py", "ProjectMounts._detach_snapshot", "incus.device_remove"):
        ("box-lifecycle", "direct", {"_busy"}, False),
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
    ("worktrees/cancellation.py", "Cancellation.run_capped", "Popen"):
        "odczyt gita z limitem wyjścia (PT-355), ta sama anulowana sesja worktree co Cancellation.run",
    ("worktrees/launchers.py", "Launchers.spawn", "Popen"): "aplikacje uruchamiane przez użytkownika",
}


# Delegacje (M3 review 1): funkcje, które wywołują konsumenta lub inną delegację; wyznacza je
# graf wywołań z AST (`cat.delegations`), nie ta tabela. Każda ma przypisaną barierę, przez
# którą przechodzi (punkt odmowy i skutek w BARRIERS); test sprawdza, że bariera leży na ścieżce.
DELEGATIONS = {
    ("app.py", "main"): "step-launch",
    ("board_monitor.py", "BoardMonitor.enter_step"): "console",
    ("board_monitor.py", "BoardMonitor.move_card"): "advance-resume",
    ("board_monitor.py", "BoardMonitor.report_step_fault"): "console",
    ("board_monitor.py", "BoardMonitor.resolve_card_caretaker"): "advance-resume",
    ("board_monitor.py", "BoardMonitor.resolve_card_step"): "console",
    ("board_monitor.py", "BoardMonitor.set_card_paused"): "advance-resume",
    ("board_monitor.py", "BoardMonitor.start_card"): "step-launch",
    ("board_monitor.py", "BoardMonitor.stop_box"): "box-lifecycle",
    ("box/admission.py", "_tick"): "box-lifecycle",
    ("box/builder.py", "Builder._attach_home"): "box-lifecycle",
    ("box/builder.py", "Builder._build"): "box-lifecycle",
    ("box/builder.py", "Builder._execute_build"): "box-lifecycle",
    ("box/builder.py", "Builder._reset_stopped"): "box-lifecycle",
    ("box/builder.py", "Builder.build"): "box-lifecycle",
    ("box/builder.py", "Builder.copy_for_card"): "box-lifecycle",
    ("box/builder.py", "Builder.destroy"): "box-lifecycle",
    ("box/builder.py", "Builder.ensure_home"): "box-lifecycle",
    ("box/builder.py", "Builder.require_idle"): "box-lifecycle",
    ("box/builder.py", "Builder.reset"): "box-lifecycle",
    ("box/command.py", "_execute"): "box-lifecycle",
    ("box/command.py", "_ready"): "box-lifecycle",
    ("box/command.py", "run_command"): "box-lifecycle",
    ("box/home.py", "_attach"): "box-lifecycle",
    ("box/home.py", "attach"): "box-lifecycle",
    ("box/lifecycle.py", "Lifecycle.idle"): "box-lifecycle",
    ("box/lifecycle.py", "Lifecycle.start"): "box-lifecycle",
    ("box/lifecycle.py", "Lifecycle.stop"): "box-lifecycle",
    # PT-264: start kroku (silnik, run, --demo) przygotowuje montowania przez ProjectMounts.ensure → _busy.
    ("box/mounts.py", "ProjectMounts._detach_snapshot"): "box-lifecycle",
    ("box/mounts.py", "ProjectMounts._ensure"): "box-lifecycle",
    ("box/mounts.py", "ProjectMounts.detach_snapshot"): "box-lifecycle",
    ("box/mounts.py", "ProjectMounts.ensure"): "box-lifecycle",
    ("box/runner.py", "BoxRunner.ensure_devices"): "box-lifecycle",
    ("box/runner.py", "BoxRunner.start"): "box-lifecycle",
    ("box/runner.py", "_demo"): "box-lifecycle",
    ("box/runner.py", "_demo_guarded"): "box-lifecycle",
    ("box/runner.py", "_demo_run"): "box-lifecycle",
    ("cardflow/boxcontrol.py", "stop"): "box-lifecycle",
    ("cardflow/boxstep.py", "_ready"): "box-lifecycle",
    ("cardflow/boxstep.py", "_reserve_start"): "box-lifecycle",
    ("cardflow/boxstep.py", "before_start"): "box-lifecycle",
    ("cardflow/boxstep.py", "check_mounts"): "box-lifecycle",
    ("cardflow/boxstep.py", "place"): "box-lifecycle",
    ("cardflow/boxstep.py", "prepare_runner"): "box-lifecycle",
    # PT-311: odmowa po automatycznej zgodzie outside — część ResolveCardStep (_trial_check przed zapisem).
    ("cardflow/consent.py", "deny_queued"): "process-check",
    ("cardflow/caretaker.py", "_answer"): "console",
    ("cardflow/caretaker.py", "_change_executor"): "console",
    ("cardflow/caretaker.py", "_resolve_action"): "console",
    ("cardflow/caretaker.py", "_retry"): "console",
    ("cardflow/caretaker.py", "_retry_stage"): "advance-resume",
    ("cardflow/engine.py", "FlowEngine._advance_card"): "advance-resume",
    ("cardflow/engine.py", "FlowEngine._advance_close"): "step-launch",
    ("cardflow/engine.py", "FlowEngine._advance_limit"): "advance-resume",
    ("cardflow/engine.py", "FlowEngine._advance_planning"): "step-launch",
    ("cardflow/engine.py", "FlowEngine._box_place"): "box-lifecycle",
    ("cardflow/engine.py", "FlowEngine._caretake"): "step-launch",
    ("cardflow/engine.py", "FlowEngine._caretaker_launch"): "step-launch",
    ("cardflow/engine.py", "FlowEngine._caretaker_resume"): "advance-resume",
    ("cardflow/engine.py", "FlowEngine._cleanup"): "process-check",
    ("cardflow/engine.py", "FlowEngine._finish"): "console",
    # PT-330: nowe review w Konsoli po nieukończonej próbie startuje przez _launch_full_planning.
    ("cardflow/engine.py", "FlowEngine._enter_review_console"): "step-launch",
    ("cardflow/engine.py", "FlowEngine._fix"): "step-launch",
    ("cardflow/engine.py", "FlowEngine._outside_behind"): "outside",
    ("cardflow/engine.py", "FlowEngine._plan_next"): "step-launch",
    ("cardflow/engine.py", "FlowEngine._planning_launch"): "step-launch",
    ("cardflow/engine.py", "FlowEngine._planning_result"): "step-launch",
    ("cardflow/engine.py", "FlowEngine._poll_interactive"): "poll-stop",
    ("cardflow/engine.py", "FlowEngine._recover_transfer_requests"): "console",
    ("cardflow/engine.py", "FlowEngine._repair_wip"): "process-check",
    ("cardflow/engine.py", "FlowEngine._repair_wip_due"): "process-check",
    ("cardflow/engine.py", "FlowEngine._retry_stage"): "advance-resume",
    ("cardflow/engine.py", "FlowEngine._review"): "step-launch",
    ("cardflow/engine.py", "FlowEngine._start_conflict"): "step-launch",
    ("cardflow/engine.py", "FlowEngine._start_outside"): "outside",
    # K-105 PT-415: świeża bramka historii outside przed każdym zapisem sprawdzenia reguł (recheck co tick z pamięci).
    ("cardflow/engine.py", "FlowEngine._outside_recheck.gate"): "outside",
    # PT-329: przebieg pod zgodą próby testów po scaleniu (kolejka outside, wynik) i decyzja z panelu.
    ("cardflow/engine.py", "FlowEngine._merge_checks"): "outside",
    ("cardflow/engine.py", "FlowEngine._merge_check_waiting"): "outside",
    ("cardflow/engine.py", "FlowEngine._merge_check_start"): "outside",
    ("cardflow/engine.py", "FlowEngine._outside_collect"): "outside",
    # PT-373: kolejka ciężkich poleceń — zajętość z żywego zbioru i klasa trwających po restarcie.
    ("cardflow/engine.py", "FlowEngine._heavy_admitted"): "outside",
    ("cardflow/engine.py", "FlowEngine._heavy_running"): "outside",
    # PT-376: decyzja kolejki ciężkich z ciszą i wpisy wstrzymane ciszą (`_outside_behind`).
    ("cardflow/engine.py", "FlowEngine._heavy_decision"): "outside",
    ("cardflow/engine.py", "FlowEngine._quiet_held"): "outside",
    ("cardflow/engine.py", "FlowEngine.resolve_merge_check"): "process-check",
    # PT-313: udany start zdejmuje kartę z Backlogu przez moved(), jak wznowienie w set_paused.
    ("cardflow/engine.py", "FlowEngine._lift_from_backlog"): "advance-resume",
    ("cardflow/engine.py", "FlowEngine.moved"): "advance-resume",
    ("cardflow/engine.py", "FlowEngine.report_step_fault"): "console",
    ("cardflow/engine.py", "FlowEngine.resolve_caretaker"): "advance-resume",
    ("cardflow/engine.py", "FlowEngine.resolve_step"): "console",
    ("cardflow/engine.py", "FlowEngine.set_paused"): "advance-resume",
    # Wybór próby z Snapshot przechodzi do _enter_step (konsument bariery console).
    ("cardflow/engine.py", "FlowEngine.enter"): "console",
    ("cardflow/engine.py", "FlowEngine._start"): "step-launch",
    ("cardflow/startorder.py", "start"): "step-launch",
    ("cardflow/engine.py", "FlowEngine.start"): "step-launch",
    ("cardflow/engine.py", "FlowEngine.start_import"): "step-launch",
    ("cardflow/engine.py", "FlowEngine.start_planning"): "step-launch",
    ("cardflow/engine.py", "FlowEngine.stop_box"): "box-lifecycle",
    ("cardflow/engine.py", "_CaretakerPort.resolve_step"): "console",
    ("cardflow/engine.py", "_CaretakerPort.retry"): "console",
    ("cardflow/engine.py", "_CaretakerPort.retry_stage"): "advance-resume",
    ("cardflow/envconsent.py", "_thread_launcher.work"): "box-lifecycle",
    ("cardflow/historyaudit_driver.py", "main"): "model",
    # K-105 PT-415: stan procesu karty — jedno wywołanie zamiast fold(read_process_history(…)).
    ("cardflow/trialhold.py", "process_state"): "model",
    # K-113 PT-466: uzgadnianie sieci bierze filtr prób z `network_state` (pamięć według wersji dziennika).
    ("cardflow/boxstep.py", "recover_network"): "box-network",
    ("cardflow_monitor.py", "CardFlowMonitor.enter_step"): "console",
    ("cardflow_monitor.py", "CardFlowMonitor.moved"): "advance-resume",
    ("cardflow_monitor.py", "CardFlowMonitor.report_step_fault"): "console",
    ("cardflow_monitor.py", "CardFlowMonitor.resolve_caretaker"): "advance-resume",
    ("cardflow_monitor.py", "CardFlowMonitor.resolve_step"): "console",
    ("cardflow_monitor.py", "CardFlowMonitor.set_paused"): "advance-resume",
    ("cardflow_monitor.py", "CardFlowMonitor.start_card"): "step-launch",
    ("cardflow_monitor.py", "CardFlowMonitor.stop_box"): "box-lifecycle",
    ("dbus_service.py", "BoardAdaptor.EnterStep"): "console",
    ("dbus_service.py", "BoardAdaptor.MoveCard"): "advance-resume",
    ("dbus_service.py", "BoardAdaptor.ReportStepFault"): "console",
    ("dbus_service.py", "BoardAdaptor.ResolveCardCaretaker"): "advance-resume",
    ("dbus_service.py", "BoardAdaptor.ResolveCardStep"): "console",
    ("dbus_service.py", "BoardAdaptor.SetCardPaused"): "advance-resume",
    ("dbus_service.py", "BoardAdaptor.StartCard"): "step-launch",
    ("dbus_service.py", "BoardAdaptor.StopBox"): "box-lifecycle",
    # Sprawy (master): przyjęcie i ponowienie startu prowadzą do startorder.start → FlowEngine._start;
    # pozostałe metody adaptera łączy z nim wspólne wywołanie IssueService.call (dyspozytor po nazwie).
    **{("dbus_service.py", f"IssuesAdaptor.{name}"): "step-launch" for name in (
        "AcceptIssue", "Attach", "Create", "Detach", "Info", "List", "PreviewIssueLaunch", "ReadAttachment",
        "Reject", "Reopen", "RetryIssueStart", "Show", "Submit", "Update", "_flow_call")},
    ("cardflow_monitor.py", "CardFlowMonitor.issue_call"): "step-launch",
    ("issues.py", "IssueService.call"): "step-launch",
    **{("issue_start.py", name): "step-launch" for name in (
        "_dispatch", "_prepare", "accept", "recover", "retry")},
}


# Surowe odczyty śladów procesu (M7 review 2) poza konsumentami barier: funkcja porównuje typ
# zdarzenia z rodzajem śladu procesu, ale wynik nie dopuszcza skutku objętego barierą tożsamości.
# Funkcja z surowym odczytem, która nie jest konsumentem bariery ani nie ma tu uzasadnienia, daje porażkę.
RAW_READS = {
    ("cardflow/startorder.py", "projection"):
        "stan zlecenia startu ze sprawy dla odpowiedzi API (queued/started/completed); nie uruchamia procesu, "
        "start idzie przez FlowEngine._start (bariera step-launch)",
    ("cardflow/caretaker.py", "stale"):
        "aktualność problemu opiekuna; wynik może zatrzymać akcję, a start i wznowienie przechodzą "
        "własne bariery _launch/resolve_step w silniku",
    ("cardflow/consent.py", "automatic"):
        "sprawdzenie zgody na polecenie, bez uruchomienia; skutek zawsze przez _start_outside "
        "z _outside_history pod journal.lock (bariera outside)",
    ("cardflow/consent.py", "denied_before"):
        "PT-311: odczyt odmów człowieka i unieważnień decyzji outside; wynik tylko odbiera automatyczną "
        "zgodę (pominięcie albo unieważnienie), nigdy nie uruchamia procesu ani nie zastępuje _outside_history",
    ("cardflow/consent.py", "view"):
        "projekcja zgód ticketu dla fullcard/UI; zwraca dane active/canRevoke bez skutku procesu",
    ("cardflow/engine.py", "FlowEngine._ticket_consent"):
        "odczyt flagi zgody ticketu, obecnie bez wywołań w produkcie; nie uruchamia procesu "
        "ani nie zastępuje _outside_history",
    ("cardflow/history.py", "build"):
        "historia do wyświetlenia: grupowanie kroków i wyników; canResume pochodzi z canEnter "
        "sprawdzonego przez silnik, a wznowienie przechodzi osobną barierę engine",
    ("cardflow/history.py", "_event_entry"):
        "formatowanie pojedynczego wpisu historii dla UI; zwraca opis bez uruchamiania procesu",
    ("reports/plans.py", "_attach_flow"):
        "raport stanu ticketów z dziennika; składa dane do wyświetlenia, nie wykonuje operacji na procesie",
    ("box/state.py", "running_steps"):
        "zajętość pudełka (box.use bez box.release) — zawsze zachowawcza; jedyne wywołanie przez "
        "Builder.running_steps, które sprawdza też active_steps (bariera box-lifecycle)",
    ("box/lifecycle.py", "Lifecycle.idle"):
        "czas bezczynności z ostatniego znacznika ts; zatrzymanie idzie przez Lifecycle.stop → require_idle "
        "(bariera box-lifecycle), a wcześniej Builder.running_steps",
    ("cardflow/engine.py", "FlowEngine._env_question"):
        "czy krok ma już decyzję (odpowiedź Tide na pytanie o środowisko) — zapis step.decision bez skutku procesu",
    ("cardflow/engine.py", "FlowEngine._auto_deny"):
        "licznik automatycznych odmów ticketu — zapis odmowy, która nigdy nie uruchamia procesu",
    ("cardflow/historyaudit.py", "moved_to_trace"):
        "PT-295: przebieg samej kolumny moved_to do wyjaśnienia różnicy pola w raporcie audytu; "
        "proces tylko do odczytu, bez skutku procesu ani zapisu dziennika",
    ("cardflow/testrerun.py", "substitute"):
        "wybór modułów do ponowienia testów z wyniku outside; woła go wyłącznie _start_outside po "
        "_outside_history (pełna historia ok) — zmienia tekst polecenia, nie dopuszcza skutku",
    ("cardflow/guard.py", "runs"):
        "PT-372: przedziały outside.started/outside.finished do wyjątku strażnika opiekuna; wynik tylko pomija "
        "zmiany drzewa worktree z poleceniem Tide w porównaniu migawek, nigdy nie uruchamia ani nie wznawia procesu",
}

# Wywołania o nierozstrzygniętym celu, których możliwym celem jest konsument lub delegacja
# (M7 review 2): reguła tekstu wywołania (odbiorca.metoda) → uzasadnienie, że to nie jest ta funkcja
# albo że ścieżka jest już objęta barierą. Nowe takie wywołanie bez reguły daje porażkę.
AMBIGUOUS_RULES = (
    (r"^self\._(debounce|import_timer|live_timer|login|note_timer|poll|rederive|rescan|retry|retry_timer|soon|start"
     r"|thread|throttle|tick|timer|work_later)\.(start|stop)$", "QTimer/wątek monitora usługi"),
    (r"^(match|mark|cut|section|headers\[index \+ 1\])\.start$", "re.Match.start — pozycja dopasowania"),
    (r"^thread\.start$", "wątek czytający wyjście gita w Cancellation.run_capped (threading.Thread), nie konsument kroków"),
    (r"^self\._procedure\.(start|snapshot)$",
     "procedura bramki prób PT-273; skutek ma własną barierę (EFFECT_SCOPE TrialGate.start_registered)"),
    (r"^bb\.admission\.start$", "admission.start obrazu bazowego Tide (box/base.py, bez kroków kart)"),
    (r"^bg\.start$", "Background.start kroku w HostRunner (skutek: EFFECT_SCOPE tasks/agentcmd.py Background.start)"),
    (r"^factory\(.*\)\.start$", "serwer proxy pudełek (box/proxy.py)"),
    (r"^loop\.stop$", "pętla asyncio proxy pudełek"),
    (r"^tasks\.stop$", "TaskMonitor przy wyjściu usługi (app.main)"),
    (r"^self\.branches\.stop$", "CardDeriver gałęzi Tablicy"),
    (r"^source\.build$", "źródło kontekstu opiekuna (caretakerctx), nie Builder.build"),
    (r"^b\.running_steps$", "b = self.builder → Builder.running_steps; Lifecycle.idle jest delegacją box-lifecycle"),
    (r"^(self\.bus|bus)\.call$", "QDBusConnection.call (sesyjna szyna D-Bus), nie IssueService.call spraw"),
    (r"^subprocess\.call$", "subprocess.call w opakowaniu jednostki kroku, nie IssueService.call spraw"),
    (r"^boxstep\.lifecycle\.tick$",
     "box/lifecycle.tick przez moduł boxstep; FlowEngine.tick (recovery) i lifecycle.tick (idle-tick) są konsumentami"),
)


def ambiguous_rule(call):
    import re
    return next((reason for pattern, reason in AMBIGUOUS_RULES if re.match(pattern, call.call)), None)


def delegation_barriers(delegations):
    """Bariery osiągalne z każdej delegacji przez wywołanych konsumentów (z AST)."""
    owner = {entry: name for name, barrier in BARRIERS.items() for entry in barrier["consumers"]}
    calls = {(d.path, d.function): d.calls for d in delegations}

    def reach(key, seen):
        if key in owner:
            return {owner[key]}
        out = set()
        for call in calls.get(key, ()):
            if call not in seen:
                out |= reach(call, seen | {key})
        return out
    return {key: reach(key, frozenset()) for key in calls}


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

    def test_every_delegation_from_code_has_a_barrier_on_its_path(self):
        """M3 review 1: delegacje (builder/require_idle, polecenia CLI, D-Bus, bramka diagnostyki)
        wyznacza graf wywołań z kodu; każda ma barierę leżącą na jej ścieżce do konsumenta."""
        delegations = cat.delegations()
        found = {(d.path, d.function) for d in delegations}
        self.assertEqual(found - set(DELEGATIONS), set(), "delegacja bez przypisanej bariery")
        self.assertEqual(set(DELEGATIONS) - found, set(), "nieaktualny wpis delegacji")
        reached = delegation_barriers(delegations)
        for key, barrier in DELEGATIONS.items():
            with self.subTest(delegation=key):
                self.assertIn(barrier, BARRIERS)
                self.assertIn(barrier, reached[key])

    def probe_delegations(self, path, source, base=None):
        """Katalog delegacji z dopisaną w pamięci funkcją (bez zmiany pliku)."""
        text = (cat.PACKAGE / path).read_text(encoding="utf-8") if base is None else base
        tree = ast.parse(text + "\n\n" + source)
        with cat.overridden({path: tree}):
            return cat.delegations(), {(c.path, c.function) for c in cat.consumers()}

    def test_new_delegation_without_barrier_fails_completeness(self):
        """Sonda M3: nowa funkcja `new_reset` (require_idle → reset) musi zostać wykryta jako
        delegacja bez przypisanej kontroli — w istniejącym module i w nowym module pakietu."""
        probes = [
            ("box/builder.py", None, "def new_reset(builder, slug):\n"
                                     "    builder.require_idle(slug)\n    builder.reset(slug)\n", "new_reset"),
            ("box/new_probe.py", "", "def new_reset(builder, slug):\n"
                                     "    builder.require_idle(slug)\n    builder.reset(slug)\n", "new_reset"),
            ("box/new_probe.py", "from . import lifecycle\n",
             "def stop_all(builder, slug):\n    lifecycle.Lifecycle(builder).stop(slug)\n", "stop_all"),
            ("cardflow/new_probe.py", "", "def outside_cli(engine, card, step, command):\n"
                                          "    return engine._start_outside(card, step, command)\n", "outside_cli"),
            ("cardflow/new_probe.py", "", "class Bus:\n    def resolve(self, card, request):\n"
                                          "        return self.engine.resolve_step(card, request)\n", "Bus.resolve"),
            ("cardflow/new_probe.py", "from .trialhold import inventory\n",
             "def scan(engine, card, st):\n    return inventory(engine, card, st)\n", "scan")]
        for path, base, source, name in probes:
            with self.subTest(path=path, function=name):
                delegations, consumers = self.probe_delegations(path, source, base)
                found = {(d.path, d.function) for d in delegations} | consumers
                self.assertIn((path, name), found)
                # Kontrola kompletności (konsumenci i delegacje) zawodzi bez nowego wpisu.
                assigned = {e for b in BARRIERS.values() for e in b["consumers"]} | set(DELEGATIONS)
                self.assertIn((path, name), found - assigned)
        # Bez sondy katalog delegacji jest zgodny z tabelą.
        self.assertEqual({(d.path, d.function) for d in cat.delegations()}, set(DELEGATIONS))

    def test_every_raw_evidence_read_is_a_consumer_or_justified(self):
        """M7 review 2: surowe odczyty startów/zakończeń i śladów procesu wyznacza AST."""
        assigned = {e for b in BARRIERS.values() for e in b["consumers"]}
        found = {(r.path, r.function) for r in cat.raw_readers()}
        self.assertEqual(found - assigned - set(RAW_READS), set(), "surowy odczyt tożsamości bez bariery")
        self.assertEqual(set(RAW_READS) - found, set(), "nieaktualny wpis surowego odczytu")
        self.assertEqual(set(RAW_READS) & assigned, set())
        for key in ("_outside_busy", "_seed_outside_waiting", "_outside_exit", "_outside_ready"):
            self.assertIn(("cardflow/engine.py", f"FlowEngine.{key}"), assigned)

    def test_new_raw_reader_fails_completeness(self):
        source = ("def busy(events, name):\n"
                  "    return any(e.get('type') == 'outside.started' and e.get('step') == name for e in events)\n")
        tree = ast.parse(source)
        with cat.overridden({"cardflow/new_probe.py": tree}):
            found = {(r.path, r.function) for r in cat.raw_readers()}
        self.assertIn(("cardflow/new_probe.py", "busy"), found)
        assigned = {e for b in BARRIERS.values() for e in b["consumers"]}
        self.assertNotIn(("cardflow/new_probe.py", "busy"), assigned | set(RAW_READS))

    def test_every_ambiguous_call_to_a_consumer_has_a_receiver_rule(self):
        """M7 review 2: wywołanie bez rozstrzygniętego celu, który może być konsumentem/delegacją,
        nie jest milcząco pomijane — każde ma jawną regułę, a każda reguła jest użyta."""
        calls = cat.ambiguous_calls()
        self.assertEqual([c for c in calls if ambiguous_rule(c) is None], [])
        import re
        for pattern, _ in AMBIGUOUS_RULES:
            with self.subTest(rule=pattern):
                self.assertTrue(any(re.match(pattern, c.call) for c in calls), "nieużyta reguła")

    def test_reviewer_annotated_and_unannotated_delegations_fail_completeness(self):
        """Sonda M7: `lc: lifecycle.Lifecycle` → `lc.stop(slug)` jest delegacją bez bariery;
        bez adnotacji to samo wywołanie jest nierozstrzygnięte i nie ma reguły."""
        assigned = {e for b in BARRIERS.values() for e in b["consumers"]} | set(DELEGATIONS)
        for source, kind in (("def new_stop(lc: lifecycle.Lifecycle, slug):\n    return lc.stop(slug)\n", "typed"),
                             ("def new_stop(lc: 'Lifecycle', slug):\n    return lc.stop(slug)\n", "typed"),
                             ("def new_stop(lc, slug):\n    return lc.stop(slug)\n", "ambiguous")):
            with self.subTest(source=source):
                tree = ast.parse("from . import lifecycle\n\n" + source)
                with cat.overridden({"box/new_probe.py": tree}):
                    delegations = {(d.path, d.function) for d in cat.delegations()}
                    calls = [c for c in cat.ambiguous_calls() if c.path == "box/new_probe.py"]
                if kind == "typed":
                    self.assertIn(("box/new_probe.py", "new_stop"), delegations - assigned)
                else:
                    self.assertTrue(calls)
                    self.assertTrue(all(ambiguous_rule(c) is None for c in calls))
        self.assertEqual({(d.path, d.function) for d in cat.delegations()}, set(DELEGATIONS))

    def test_matrix_executes_every_barrier_and_box_journal_scope(self):
        """M5 review 1: macierz ujemna wykonuje każdą barierę katalogu; bariery spoza zakresu
        dziennika pudełka nie czytają go (nieosiągalność sprawdzona na AST konsumentów)."""
        matrix = importlib.import_module(M)
        self.assertEqual(set(matrix.MATRIX_BARRIERS), set(BARRIERS))
        self.assertLessEqual(set(matrix.BOX_JOURNAL_BARRIERS), set(BARRIERS))
        box_reads = {"public_processes", "public_inventory", "active_steps", "_validate_box_event", "box_event_error",
                     "trialhold.inventory", "inventory"}
        reads = {(c.path, c.function): c.reads for c in cat.consumers()}
        box_journal = {c for c, r in reads.items() if r & box_reads}
        # Konsumenci czytający dziennik pudełka i delegacje do nich.
        calls = {(d.path, d.function): d.calls for d in cat.delegations()}
        changed = True
        while changed:
            changed = False
            for key, called in calls.items():
                if key not in box_journal and called & box_journal:
                    box_journal.add(key)
                    changed = True
        for name, barrier in BARRIERS.items():
            with self.subTest(barrier=name):
                touches = any(entry in box_journal for entry in barrier["consumers"])
                self.assertEqual(touches, name in matrix.BOX_JOURNAL_BARRIERS)

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
        entry[0] |= set(w.fields)
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
    for kind, spec in pi.BOX_EVENTS.items():
        for name, f in {**pi.BOX_ENVELOPE_FIELDS, **(spec.fields or {})}.items():
            if f.role == "neutral":
                continue
            lines.append(f"| `box:{kind}` | {name} | {f.role} | {f.type} | {'tak' if f.nonempty else '—'} | "
                         f"{f.predecessor or '—'} | {f.domain or '—'} | {f.rule or '—'} | {f.source} |")
    lines += ["", "Pozostałe pola zdarzeń procesu mają rolę `neutral` (dane operacyjne); pełna lista w modelu.", "",
              "## Konsumenci i bariery", "",
              "| Bariera | Konsumenci | Punkt odmowy | Skutek | Testy ujemne | Testy dodatnie |",
              "| --- | --- | --- | --- | --- | --- |"]
    for name, b in BARRIERS.items():
        lines.append(f"| {name} | {'<br>'.join(f'{p}:{q}' for p, q in b['consumers'])} | {b['denial']} | "
                     f"{b['effect']} | {'<br>'.join(r.split(':')[1] for r in b['negative'])} | "
                     f"{'<br>'.join(r.split(':')[1] for r in b['positive'])} |")
    lines += ["", "## Delegacje (graf wywołań z AST)", "",
              "| Delegacja | Wywołuje | Bariera |", "| --- | --- | --- |"]
    for d in cat.delegations():
        lines.append(f"| {d.path}:{d.function} | {', '.join(sorted(q for _, q in d.calls))} | "
                     f"{DELEGATIONS.get((d.path, d.function), '—')} |")
    lines += ["", "## Surowe odczyty śladów procesu (AST)", "",
              "| Funkcja | Rodzaje | Bariera / uzasadnienie |", "| --- | --- | --- |"]
    owner = {entry: name for name, barrier in BARRIERS.items() for entry in barrier["consumers"]}
    for r in cat.raw_readers():
        key = (r.path, r.function)
        how = owner[key] if key in owner else "poza barierą: " + RAW_READS.get(key, "—")
        lines.append(f"| {r.path}:{r.function} | {', '.join(sorted(r.reads))} | {how} |")
    lines += ["", "## Nierozstrzygnięte wywołania o nazwie konsumenta/delegacji", "",
              "| Miejsce | Wywołanie | Możliwy cel | Uzasadnienie |", "| --- | --- | --- | --- |"]
    for a in cat.ambiguous_calls():
        lines.append(f"| {a.path}:{a.function} | `{a.call}` | {', '.join(sorted(q for _, q in a.candidates))} | "
                     f"{ambiguous_rule(a) or '—'} |")
    matrix = importlib.import_module(M)
    gen = importlib.import_module("daemon.tests.cardflow_identity_generator")
    lines += ["", "## Macierz ujemna: historie × bariery × restart", "",
              "Każda wadliwa historia dziennika karty przechodzi przez wszystkie bariery "
              f"({', '.join(matrix.MATRIX_BARRIERS)}) przed restartem i po nim; dziennik pudełka — przez "
              f"bariery, które go czytają ({', '.join(matrix.BOX_JOURNAL_BARRIERS)}). "
              "Liczby to wadliwe historie (zwykły / chroniony ticket); każda ma 2 warianty restartu.", "",
              "| Grupa | Rodzaj | Historie PT-999 | Historie PT-176 | Bariery |", "| --- | --- | --- | --- | --- |"]
    counts = {}
    for case in gen.all_cases():
        if case.expected == gen.BAD:
            entry = counts.setdefault((case.group, case.kind), {"PT-999": 0, "PT-176": 0})
            entry[case.ticket] += 1
    for (group, kind), entry in sorted(counts.items()):
        barriers = matrix.BOX_JOURNAL_BARRIERS if group.startswith("box") else matrix.MATRIX_BARRIERS
        lines.append(f"| {group} | `{kind}` | {entry['PT-999']} | {entry['PT-176']} | {len(barriers)} |")
    lines += ["", "## Skutki", "", "| Skutek | Funkcja | Bariera / uzasadnienie |", "| --- | --- | --- |"]
    for (path, function, effect), (barrier, guard, names, locked) in sorted(EFFECT_SCOPE.items()):
        how = ("pod journal.lock " if locked else "") + (
            ", ".join(sorted(names)) if guard == "direct" else "przez " + ", ".join(q for _, q, _ in guard))
        lines.append(f"| {effect} | {path}:{function} | {barrier}: {how} |")
    for (path, function, effect), reason in sorted(OUT_OF_SCOPE.items()):
        lines.append(f"| {effect} | {path}:{function} | poza zakresem: {reason} |")
    return "\n".join(lines) + "\n"
