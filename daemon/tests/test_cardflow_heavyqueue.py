"""Kolejka ciężkich poleceń na maszynę (K-39 #3, PT-373).

10.11 sześć zgód „uruchamiaj zawsze” dało sześć `make test-changed` naraz i wszystkie przekroczyły 60 minut.
Zasada jest w `cardflow/heavyqueue.py`; testy silnika idą wzorem `test_cardflow_outside_queue.py` (silnik bez
konstruktora, dziennik w pamięci, start polecenia podmieniony na zapis `outside.started`)."""
import os
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta

from tide_daemon.cardflow import engine as flow_engine
from tide_daemon.cardflow import fullcard, heavyqueue, mergecheck
from tide_daemon.cardflow.state import fold

PLASMA = {"changed": "make test-changed BASE={base}", "full": "make test"}
SHA = "2906ad3bbb3635048fd006f1114e34aa643e03ed"
HEAVY = f"make test-changed BASE={SHA}"


class ClassifyTest(unittest.TestCase):
    def heavy(self, command, contract=PLASMA, **kw):
        return heavyqueue.classify(command, ("/w",), contract, (), **kw)

    def test_contract_commands_and_merge_check_are_heavy(self):
        self.assertEqual(self.heavy(HEAVY), heavyqueue.Verdict(True, heavyqueue.TESTS_CHANGED))
        self.assertEqual(self.heavy("make test"), heavyqueue.Verdict(True, heavyqueue.TESTS_FULL))
        self.assertTrue(self.heavy(f"make test-changed BASE={SHA} JOBS=4").heavy)
        self.assertTrue(self.heavy("LC_ALL=pl_PL.UTF-8 make test").heavy)
        self.assertEqual(self.heavy("echo cokolwiek", merge_check=True), heavyqueue.Verdict(True, heavyqueue.MERGE_CHECK))

    def test_other_commands_are_light(self):
        for command in ("ss -ltnp", "cat /proc/loadavg", "make lint", "make test-cardflow", "make", "",
                        "make test-changed BASE=x && make test"):
            self.assertFalse(self.heavy(command).heavy, command)

    def test_project_with_other_contract_has_its_own_heavy_commands(self):
        dotnet = {"changed": "dotnet test --filter {base}", "full": "dotnet test"}
        self.assertTrue(self.heavy("dotnet test", dotnet).heavy)
        self.assertTrue(self.heavy("dotnet test --filter Unit", dotnet).heavy)
        self.assertFalse(self.heavy("make test", dotnet).heavy)
        self.assertFalse(self.heavy("dotnet build", dotnet).heavy)
        self.assertFalse(self.heavy("make test", None).heavy)

    def test_test_policy_is_accepted_as_contract(self):
        from tide_daemon.cardflow import testpolicy
        policy = testpolicy.TestPolicy(changed="make test-changed BASE={base}", full="make test")
        self.assertTrue(heavyqueue.classify(HEAVY, (), policy).heavy)


class AdmitTest(unittest.TestCase):
    def test_admits_up_to_free_slots_in_consent_order(self):
        waiting = [heavyqueue.Waiting("c", 30.0), heavyqueue.Waiting("a", 10.0), heavyqueue.Waiting("b", 20.0)]
        result = heavyqueue.admit(waiting, {"r"}, 3)
        self.assertEqual(result.admitted, ("a", "b"))
        self.assertEqual(result.held, {"c": heavyqueue.HELD_LIMIT})
        self.assertEqual((result.busy, result.limit), (1, 3))

    def test_lower_limit_never_admits_until_running_drops_below_it(self):
        waiting = [heavyqueue.Waiting("w", 1.0)]
        self.assertEqual(heavyqueue.admit(waiting, {"a", "b"}, 1).admitted, ())
        self.assertEqual(heavyqueue.admit(waiting, {"b"}, 1).admitted, ())
        self.assertEqual(heavyqueue.admit(waiting, set(), 1).admitted, ("w",))

    def test_order_key_is_pluggable(self):
        waiting = [heavyqueue.Waiting("old", 1.0), heavyqueue.Waiting("new", 2.0)]
        result = heavyqueue.admit(waiting, (), 1, order=lambda w: (-w.queued, w.name))
        self.assertEqual(result.admitted, ("new",))

    def test_held_heavy_consent_does_not_hold_others(self):
        self.assertTrue(heavyqueue.holds_queue(False, 5, 2))
        self.assertTrue(heavyqueue.holds_queue(True, 1, 2))
        self.assertFalse(heavyqueue.holds_queue(True, 2, 2))

    def test_limit_values(self):
        self.assertEqual(heavyqueue.limit_of(None), 2)
        self.assertEqual(heavyqueue.limit_of(7), 2)
        self.assertEqual(heavyqueue.limit_of(True), 2)
        self.assertEqual(heavyqueue.limit_of(6), 6)
        for bad in (0, 7, 2.0, "2", True, None):
            self.assertTrue(heavyqueue.limit_problem(bad), bad)
        self.assertEqual(heavyqueue.limit_problem(1), "")


class MemoryJournal:
    def __init__(self):
        self.events = {}
        self.lock = threading.RLock()

    def cards(self):
        return list(self.events)

    def read(self, card):
        return list(self.events.get(card, []))

    read_complete = read

    def state(self, card):
        return fold(self.read(card))

    def append(self, card, event_type, **fields):
        events = self.events.setdefault(card, [])
        event = {"v": 1, "seq": len(events) + 1, "card": card, "type": event_type,
                 "ts": fields.pop("ts", None) or datetime.now().astimezone().isoformat(timespec="seconds"), **fields}
        events.append(event)
        return event


def ago(minutes):
    return (datetime.now().astimezone() - timedelta(minutes=minutes)).isoformat(timespec="seconds")


class HeavyQueueEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.worktree = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.worktree)
        with open(os.path.join(self.worktree, "Makefile"), "w", encoding="utf-8") as fh:
            fh.write("test-changed:\n\t@true\ntest:\n\t@true\nlint:\n\t@true\n")
        self.settings = {"heavyLimit": 2}
        self.projects = {}
        self.started = []
        self.journal = MemoryJournal()
        eng = flow_engine.FlowEngine.__new__(flow_engine.FlowEngine)
        eng.journal = self.journal
        eng._outside_waiting = {}
        eng._outside_seeded = True
        eng._outside_active = {}
        eng._outside_heavy = {}
        eng._heavy_held = {}
        eng.store = types.SimpleNamespace(root=self.tmp.name, approvals_path=None)
        eng._project = lambda card: (None, types.SimpleNamespace(
            slug=self.projects[card], repo=self.worktree, denied_commands=[], tests=None, tests_error=""))
        eng._settings_data = lambda: dict(self.settings)
        eng._start_outside = self.fake_start
        self.eng = eng

    def card(self, card, project="plasma-tide"):
        self.projects[card] = project
        self.journal.append(card, "card.started", project=project, entry="now", repo=self.worktree, baseVersion=1)
        return card

    def request(self, card, ticket, command=HEAVY, *, choice="run", minutes=5, auto=False):
        name = f"{card}-{ticket}-1"
        folder = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.join(folder, "out"), exist_ok=True)
        step = self.journal.append(card, "step.started", step=name, ticket=ticket, kind="ticket", stepDir=folder,
                                   worktree=self.worktree, baseVersion=1)
        self.journal.append(card, "step.finished", step=name, ticket=ticket, result="denied", outside=True,
                            command=command, canAllow=True)
        self.journal.append(card, "step.decision", step=name, ticket=ticket, kind="ticket", choice=choice,
                            command=command, executor="", ts=ago(minutes), **({"auto": True} if auto else {}))
        return step

    def fake_start(self, card, step, command):
        name = step["step"]
        exit_file = os.path.join(step["stepDir"], "outside", "in", "exit-code")
        os.makedirs(os.path.dirname(exit_file), exist_ok=True)
        self.journal.append(card, "outside.started", step=name, ticket=step["ticket"], command=command,
                            unit=f"tide-outside-{name}", log=os.path.join(step["stepDir"], "outside", "outside.log"),
                            exitFile=exit_file, worktree=step["worktree"], head="", rerunOf="")
        self.eng._outside_active[name] = (card, f"tide-outside-{name}")
        self.started.append(name)

    def finish(self, step):
        with open(os.path.join(step["stepDir"], "outside", "in", "exit-code"), "w", encoding="ascii") as fh:
            fh.write("0\n")

    def ready(self, card, step):
        st = self.journal.state(card)
        name = step["step"]
        return self.eng._outside_ready(card, step, st.finished[name], st.step_decisions[name])

    def tick(self, *pairs):
        for card, step in pairs:
            self.ready(card, step)

    def status(self, card):
        out = {"tickets": [{"id": t, "state": "w-toku"} for t in
                           dict.fromkeys(s.get("ticket") for s in self.journal.state(card).steps.values())],
               "note": ""}
        st = self.journal.state(card)
        for ticket in out["tickets"]:
            ticket.update(fullcard.ticket_status(st, ticket))
        fullcard.heavy_queued(st, out, self.eng._heavy_view(card))
        return {t["id"]: t["statusLabel"] for t in out["tickets"]}

    def test_two_cards_in_one_tick_with_limit_one_start_exactly_one(self):
        self.settings["heavyLimit"] = 1
        a, b = self.card("K-1"), self.card("K-2")
        first, second = self.request(a, "PT-1", minutes=10), self.request(b, "PT-2", minutes=5)
        self.tick((b, second), (a, first))     # młodsza sprawdzana pierwsza, w tym samym ticku
        self.assertEqual(len(self.started), 1)
        waiting = second if self.started == [first["step"]] else first
        card = b if waiting is second else a
        ticket = waiting["ticket"]
        self.assertEqual(self.status(card)[ticket],
                         "w kolejce: make test-changed BASE=2906ad3bbb363504… (ciężkie polecenia: 1 z 1 zajęte)")

    def test_same_tick_start_counts_for_the_next_card(self):
        # Zbiór trwających z żywego `_outside_active`: start w tym ticku od razu zajmuje miejsce.
        self.settings["heavyLimit"] = 1
        a, b = self.card("K-1"), self.card("K-2", "streamer")
        first, second = self.request(a, "PT-1", minutes=10), self.request(b, "KAMP-1", minutes=5)
        self.tick((a, first), (b, second))
        self.assertEqual(self.started, [first["step"]])

    def test_three_consents_in_two_projects_with_limit_two(self):
        a, b, c = self.card("K-1"), self.card("K-2"), self.card("K-3", "streamer")
        steps = [(a, self.request(a, "PT-1", minutes=30)), (b, self.request(b, "PT-2", minutes=20)),
                 (c, self.request(c, "KAMP-1", minutes=10))]
        self.tick(*steps)
        self.assertEqual(self.started, [steps[0][1]["step"], steps[1][1]["step"]])
        self.assertEqual(self.status(c)["KAMP-1"], "w kolejce: make test-changed BASE=2906ad3bbb363504… "
                                                   "(ciężkie polecenia: 2 z 2 zajęte)")
        self.tick(steps[2])
        self.assertEqual(len(self.started), 2)
        self.finish(steps[0][1])
        self.tick(steps[2])
        self.assertEqual(self.started[-1], steps[2][1]["step"])
        self.assertEqual(self.status(c)["KAMP-1"], "pracuje: make test-changed BASE=2906ad3bbb363504…")

    def test_lowering_the_limit_keeps_running_and_waits_until_both_finish(self):
        a, b, c = self.card("K-1"), self.card("K-2"), self.card("K-3")
        one, two = self.request(a, "PT-1", minutes=30), self.request(b, "PT-2", minutes=20)
        waiting = self.request(c, "PT-3", minutes=10)
        self.tick((a, one), (b, two), (c, waiting))
        self.assertEqual(self.started, [one["step"], two["step"]])
        self.settings["heavyLimit"] = 1
        self.tick((c, waiting))
        self.assertEqual(set(self.eng._outside_active), {one["step"], two["step"]})     # nic nie przerwane
        self.finish(one)
        self.tick((c, waiting))
        self.assertNotIn(waiting["step"], self.started)
        self.assertEqual(self.status(c)["PT-3"], "w kolejce: make test-changed BASE=2906ad3bbb363504… "
                                                 "(ciężkie polecenia: 1 z 1 zajęte)")
        self.finish(two)
        self.tick((c, waiting))
        self.assertEqual(self.started[-1], waiting["step"])

    def test_automatic_consents_obey_the_same_limit(self):
        self.settings["heavyLimit"] = 1
        a, b = self.card("K-1"), self.card("K-2")
        always = self.request(a, "PT-1", choice="run-always", minutes=10, auto=True)
        ticket = self.request(b, "PT-2", choice="run-ticket", minutes=5, auto=True)
        from unittest import mock
        from tide_daemon.cardflow import consent
        with mock.patch.object(consent, "automatic", return_value=True):
            self.tick((a, always), (b, ticket))
            self.assertEqual(self.started, [always["step"]])
            self.finish(always)
            self.tick((b, ticket))
        self.assertEqual(self.started, [always["step"], ticket["step"]])

    def test_light_command_starts_despite_two_running_heavy(self):
        a, b, c = self.card("K-1"), self.card("K-2"), self.card("K-3")
        one, two = self.request(a, "PT-1", minutes=30), self.request(b, "PT-2", minutes=20)
        heavy_waiting = self.request(c, "PT-3", minutes=15)
        light = self.request(c, "PT-4", "ss -ltnp", minutes=10)
        self.tick((a, one), (b, two), (c, heavy_waiting), (c, light))
        self.assertEqual(self.started, [one["step"], two["step"], light["step"]])
        self.assertEqual(self.status(c)["PT-4"], "pracuje: ss -ltnp")

    def test_full_card_status_has_the_exact_heavy_label(self):
        a, b, c = self.card("K-1"), self.card("K-2"), self.card("K-3")
        self.tick((a, self.request(a, "PT-1", minutes=30)), (b, self.request(b, "PT-2", minutes=20)))
        waiting = self.request(c, "PT-3", "make test-changed", minutes=10)
        self.tick((c, waiting))
        self.assertEqual(self.status(c)["PT-3"], "w kolejce: make test-changed (ciężkie polecenia: 2 z 2 zajęte)")
        # Kafelek: ten sam dopisek po dotychczasowym napisie kolejki.
        st = self.journal.state(c)
        out = {"tickets": [], "note": "PT-3 · w kolejce poza piaskownicą: make test-changed"}
        fullcard.heavy_queued(st, out, self.eng._heavy_view(c))
        self.assertEqual(out["note"], "PT-3 · w kolejce poza piaskownicą: make test-changed "
                                      "(ciężkie polecenia: 2 z 2 zajęte)")
        # Inna karta nie dostaje cudzego wstrzymania.
        self.assertEqual(self.eng._heavy_view(a), {})

    def test_light_command_in_queue_keeps_plain_label(self):
        a = self.card("K-1")
        light = self.request(a, "PT-1", "make lint")
        self.eng._outside_busy = lambda card: True
        self.ready(a, light)
        self.assertEqual(self.status(a)["PT-1"], "w kolejce: make lint")

    def test_running_merge_check_holds_a_slot_and_its_waiting_check_waits(self):
        self.settings["heavyLimit"] = 1
        a, b = self.card("K-1"), self.card("K-2")
        running = mergecheck.step_name("PT-9:1")
        folder = os.path.join(self.tmp.name, running)
        os.makedirs(os.path.join(folder, "outside", "in"))
        self.journal.append(a, "outside.started", step=running, ticket="PT-9", command="make test-changed BASE=x",
                            unit="u", log="", exitFile=os.path.join(folder, "outside", "in", "exit-code"))
        self.eng._outside_active[running] = (a, "u")
        step = self.request(b, "PT-2", minutes=5)
        self.tick((b, step))
        self.assertEqual(self.started, [])
        self.assertEqual(self.eng._heavy_running(), {running})
        # Czekająca próba po scaleniu też czeka na miejsce (zawsze ciężka).
        check = mergecheck.step_name("PT-3:1")
        self.eng._outside_behind(b, check, {"ts": ago(1)}, heavy=True)
        self.assertFalse(self.eng._heavy_admitted(b, check))
        self.assertEqual(self.eng._heavy_view(b)[check], (1, 1))

    def test_merge_check_run_waits_for_a_heavy_slot(self):
        self.settings["heavyLimit"] = 1
        a, b = self.card("K-1"), self.card("K-2")
        busy = self.request(a, "PT-1", "make test", minutes=30)
        self.tick((a, busy))
        name = mergecheck.step_name("PT-2:1")
        folder = os.path.join(self.tmp.name, name)
        fields = dict(step=name, ticket="PT-2", kind=mergecheck.KIND, check="PT-2:1", command="make lint",
                      after="", branch="card/k-2", worktree=self.worktree, stepDir=folder, baseVersion=1)
        self.journal.append(b, mergecheck.STEP, **fields, canAllow=True)
        decision = self.journal.append(b, "step.decision", step=name, ticket="PT-2", kind=mergecheck.KIND,
                                       check="PT-2:1", choice="run", command="make lint", executor="")
        launched = []
        self.eng._merge_check_start = lambda card, step, entry: launched.append(step["step"])
        step = {key: fields[key] for key in ("step", "ticket", "kind", "check", "command", "after", "branch",
                                              "worktree", "stepDir", "baseVersion")}
        self.eng._merge_check_run(b, step, {"check": "PT-2:1"}, decision)
        self.assertEqual(launched, [])      # próba ciężka nawet z lekkim poleceniem; miejsce zajęte
        self.assertEqual(self.eng._heavy_view(b), {name: (1, 1)})
        self.finish(busy)
        self.eng._merge_check_run(b, step, {"check": "PT-2:1"}, decision)
        self.assertEqual(launched, [name])

    def test_restart_restores_running_heavy_and_waiting_order(self):
        a, b, c, d = (self.card(k) for k in ("K-1", "K-2", "K-3", "K-4"))
        one, two = self.request(a, "PT-1", minutes=60), self.request(b, "PT-2", minutes=50)
        older, younger = self.request(c, "PT-3", minutes=40), self.request(d, "PT-4", minutes=30)
        self.fake_start(a, one, HEAVY)
        self.fake_start(b, two, HEAVY)
        self.started.clear()
        # Restart usługi: pusta pamięć, zbiory z dzienników.
        self.eng._outside_active = None
        self.eng._outside_heavy = {}
        self.eng._outside_waiting = {}
        self.eng._outside_seeded = False
        self.tick((d, younger), (c, older))
        self.assertEqual(self.started, [])
        self.assertEqual(self.eng._heavy_running(), {one["step"], two["step"]})
        self.assertTrue(all(len(v) == 5 and v[4] for v in self.eng._outside_waiting.values()))
        self.finish(one)
        self.tick((d, younger), (c, older))
        self.assertEqual(self.started, [older["step"]])

    def test_paused_card_does_not_hold_a_heavy_slot(self):
        self.settings["heavyLimit"] = 1
        a, b = self.card("K-1"), self.card("K-2")
        first, second = self.request(a, "PT-1", minutes=10), self.request(b, "PT-2", minutes=5)
        self.journal.append(a, "card.paused")
        self.tick((a, first), (b, second))
        self.assertEqual(self.started, [second["step"]])

    def test_queue_entry_has_heavy_field(self):
        a = self.card("K-1")
        step = self.request(a, "PT-1")
        light = self.request(a, "PT-2", "cat /proc/loadavg")
        self.eng._outside_busy = lambda card: True
        self.tick((a, step), (a, light))
        self.assertTrue(self.eng._outside_waiting[step["step"]][4])
        self.assertFalse(self.eng._outside_waiting[light["step"]][4])

    # --- wzorce `heavyCommands` (K-39 #12, PT-382) ---

    def test_always_consent_outside_the_contract_waits_in_the_same_queue(self):
        a, b, c = self.card("K-1"), self.card("K-2"), self.card("K-3")
        self.tick((a, self.request(a, "PT-1", minutes=30)), (b, self.request(b, "PT-2", minutes=20)))
        waiting = self.request(c, "PT-3", "make test-cardflow", choice="run-always", minutes=10)
        light = self.request(c, "PT-4", "docker ps", minutes=5)
        self.tick((c, waiting), (c, light))
        self.assertNotIn(waiting["step"], self.started)
        self.assertIn(light["step"], self.started)
        self.assertEqual(self.status(c)["PT-3"], "w kolejce: make test-cardflow (ciężkie polecenia: 2 z 2 zajęte)")

    def test_owner_pattern_for_a_script_queues_and_the_command_is_not_rewritten(self):
        self.settings["heavyLimit"] = 1
        self.settings["heavyCommands"] = ["bash daemon/tests/perf_gate_k98.sh *"]
        a, b = self.card("K-1"), self.card("K-2")
        command = "bash ./daemon/tests/perf_gate_k98.sh build/x"
        first, second = self.request(a, "PT-1", command, minutes=10), self.request(b, "PT-2", command, minutes=5)
        self.tick((a, first), (b, second))
        self.assertEqual(self.started, [first["step"]])
        started = [e for e in self.journal.events["K-1"] if e["type"] == "outside.started"]
        self.assertEqual(started[0]["command"], command)      # normalizacja tylko do klasyfikacji

    def test_emptied_list_makes_only_contract_commands_heavy(self):
        self.settings["heavyCommands"] = []
        a, b, c = self.card("K-1"), self.card("K-2"), self.card("K-3")
        self.tick((a, self.request(a, "PT-1", minutes=30)), (b, self.request(b, "PT-2", minutes=20)))
        other = self.request(c, "PT-3", "make test-cardflow", minutes=10)
        self.tick((c, other))
        self.assertIn(other["step"], self.started)


class HeavySettingsTest(unittest.TestCase):
    def engine(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        eng = flow_engine.FlowEngine.__new__(flow_engine.FlowEngine)
        eng.settings = os.path.join(tmp.name, "cardflow.json")
        with open(eng.settings, "w", encoding="utf-8") as fh:
            fh.write('{"nieznany": {"a": 1}}\n')
        return eng

    def test_default_validation_and_unknown_keys(self):
        eng = self.engine()
        self.assertEqual(eng.get_flow_settings()["heavyLimit"], 2)
        for bad in (0, 7, 1.5, "2", True, None):
            with self.assertRaises(flow_engine.FlowError) as raised:
                eng.set_flow_settings({"heavyLimit": bad})
            self.assertEqual(raised.exception.code, "bad-settings")
        self.assertEqual(eng.set_flow_settings({"heavyLimit": 1})["heavyLimit"], 1)
        self.assertEqual(eng._settings_data()["nieznany"], {"a": 1})
        self.assertEqual(eng.set_flow_settings({"reviewRoundsBeforeQuestion": 4})["heavyLimit"], 1)

    def test_heavy_commands_default_validation_and_unknown_keys(self):
        eng = self.engine()
        self.assertEqual(eng.get_flow_settings()["heavyCommands"], heavyqueue.default_patterns())
        for bad in ("make test", [1], ["make test", ""], ["  "], ["*"], {"a": 1}, None, True):
            with self.assertRaises(flow_engine.FlowError, msg=repr(bad)) as raised:
                eng.set_flow_settings({"heavyCommands": bad})
            self.assertEqual(raised.exception.code, "bad-settings")
        self.assertNotIn("heavyCommands", eng._settings_data())      # odmowa przed zapisem
        saved = eng.set_flow_settings({"heavyCommands": [" bash daemon/tests/perf_gate_k98.sh * "]})
        self.assertEqual(saved["heavyCommands"], ["bash daemon/tests/perf_gate_k98.sh *"])
        self.assertEqual(eng._settings_data()["nieznany"], {"a": 1})
        self.assertEqual(eng.set_flow_settings({"heavyLimit": 3})["heavyCommands"], saved["heavyCommands"])
        self.assertEqual(eng.set_flow_settings({"heavyCommands": []})["heavyCommands"], [])


DEFAULTS = heavyqueue.default_patterns()
WORKTREE, REPO = "/w/pt-1", "/r/proj"
ROOTS = (WORKTREE, REPO, "/w/pt-1.step")
PERF = "bash daemon/tests/perf_gate_k98.sh *"


class PatternClassifyTest(unittest.TestCase):
    def heavy(self, command, patterns=DEFAULTS, contract=PLASMA, roots=ROOTS):
        return heavyqueue.classify(command, roots, contract, patterns).heavy

    def test_default_list_heavy_and_light(self):
        for command in ("make test-cardflow", "make test-daemon -j2", "make test-board", "npm run e2e -- x",
                        "npx playwright test x", "docker compose up -d", "docker build .", "make mist-golden",
                        "dotnet build", "go test ./...", "cargo test", "pytest -q", "npm test"):
            self.assertEqual(self.heavy(command, contract=None), True, command)
        for command in ("docker ps", "ss -ltnp", "cat /proc/loadavg", "make lint", "npm install", "docker compose ps"):
            self.assertFalse(self.heavy(command, contract=None), command)

    def test_make_test_family_does_not_leak_to_other_targets(self):
        self.assertFalse(self.heavy("make testy", ["make test *"], None))
        self.assertTrue(self.heavy("make test", ["make test *"], None))
        self.assertTrue(self.heavy("make test X=1", ["make test *"], None))

    def test_normalization_of_interpreters_and_assignments(self):
        for command in ("/usr/bin/python3 -m unittest discover -s daemon/tests",
                        f"env PYTHONPATH={WORKTREE}/src .venv/bin/python -m pytest -q tests",
                        f"JOBS=4 make test-changed BASE={SHA}",
                        "/usr/bin/python3 tools/test_runner.py --modules a/b.py",
                        "python3.12 -m pytest", "PYTHONUNBUFFERED=1 python -m unittest"):
            self.assertTrue(self.heavy(command, contract=None), command)
        self.assertFalse(self.heavy("/usr/bin/python3 -m http.server", contract=None))

    def test_contract_family_is_normalized_too(self):
        self.assertTrue(self.heavy(f"JOBS=4 make test-changed BASE={SHA}", [], PLASMA))
        python = {"changed": "/usr/bin/python3 -m pytest {base}", "full": "python3 -m pytest"}
        self.assertTrue(self.heavy(f"{REPO}/.venv/bin/python -m pytest -q", [], python))

    def test_script_patterns_match_every_spelling_inside_roots(self):
        for command in ("bash daemon/tests/perf_gate_k98.sh build/x", "bash ./daemon/tests/perf_gate_k98.sh build/x",
                        "sh daemon/tests/perf_gate_k98.sh build/x", f"bash {WORKTREE}/daemon/tests/perf_gate_k98.sh build/x",
                        f"bash {REPO}/daemon/tests/perf_gate_k98.sh build/x", "bash daemon/../daemon/tests/perf_gate_k98.sh x"):
            self.assertFalse(self.heavy(command, [], None), command)
            self.assertEqual(self.heavy(command, [PERF], None), "../" not in command, command)

    def test_script_pattern_written_with_dot_or_absolute_path_matches_too(self):
        for pattern in ("bash ./daemon/tests/perf_gate_k98.sh *", f"sh {WORKTREE}/daemon/tests/perf_gate_k98.sh *"):
            self.assertTrue(self.heavy("bash daemon/tests/perf_gate_k98.sh x", [pattern], None), pattern)

    def test_script_outside_roots_is_never_heavy_for_a_pattern(self):
        for command in ("bash /etc/daemon/tests/perf_gate_k98.sh x", "bash ../daemon/tests/perf_gate_k98.sh x",
                        "bash /tmp/daemon/tests/perf_gate_k98.sh x"):
            self.assertFalse(self.heavy(command, [PERF, "bash *"], None), command)
        self.assertFalse(self.heavy("bash daemon/tests/perf_gate_k98.sh x", [PERF], None, roots=()))

    def test_exact_rule_needs_the_whole_command(self):
        self.assertTrue(self.heavy("make lint", ["make lint"], None))
        self.assertFalse(self.heavy("make lint V=1", ["make lint"], None))
        self.assertTrue(self.heavy("make lint V=1", ["make lint *"], None))

    def test_compound_commands_and_bad_patterns_do_not_match(self):
        self.assertFalse(self.heavy("make test && rm -rf x", ["make test *"], None))
        self.assertFalse(self.heavy("make lint", ["", "   ", "*", "make lint; rm x"], None))

    def test_pattern_matches_is_one_shared_function(self):
        argv = heavyqueue.command_argv("sh ./daemon/tests/perf_gate_k98.sh a", ROOTS)
        self.assertEqual(argv, ["bash", "daemon/tests/perf_gate_k98.sh", "a"])
        self.assertTrue(heavyqueue.pattern_matches(argv, [PERF], ROOTS))
        self.assertFalse(heavyqueue.pattern_matches([], [PERF], ROOTS))


if __name__ == "__main__":
    unittest.main()
