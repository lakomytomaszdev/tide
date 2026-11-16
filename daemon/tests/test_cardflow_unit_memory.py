"""K-125 #3 (PT-488): inwentaryzacja K-38 bez odczytu stanu jednostek co tick.

W obserwacji (bez `fresh`) stan jednostki trzymanego procesu pochodzi z pamięci (`unitstate.UnitStates`) najwyżej
`unitstate.TTL` = 30 s; pamiętane są tylko `active` i `inactive`. `unknown` (zwrócone albo z wyjątku) nigdy nie jest
pamięcią. `fresh=True` (zapis `k38.inventory`/`k38.handoff`, diagnostyka K-38) czyta każdą jednostkę świeżo.
Granice liczenia: `stepstop.unit_state` (proces `systemctl` kroku hostowego) i `step_state` runnera pudełka."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tide_daemon.box import state as bs, trialgate as gate
from tide_daemon.cardflow import boxstep, trialhold, unitstate
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.journal import Journal
from tide_daemon.tasks import stepstop

from .test_cardflow_trial_gate import HostStore


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class UnitStatesTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.memory = unitstate.UnitStates(clock=self.clock)
        self.calls = []

    def reader(self, *states):
        values = list(states)

        def read():
            self.calls.append(1)
            value = values.pop(0) if len(values) > 1 else values[0]
            if isinstance(value, BaseException):
                raise value
            return value
        return read

    def test_active_and_inactive_are_remembered_for_less_than_30_seconds(self):
        for state in ("active", "inactive"):
            with self.subTest(state=state):
                self.calls.clear()
                read = self.reader(state)
                key = ("step", state)
                self.assertEqual(self.memory.read(key, read), state)
                self.clock.now += unitstate.TTL - 0.001
                self.assertEqual(self.memory.read(key, read), state)
                self.assertEqual(len(self.calls), 1)
                self.clock.now += 0.001                                 # dokładnie 30 s po odczycie: znów odczyt
                self.memory.read(key, read)
                self.assertEqual(len(self.calls), 2)

    def test_returned_unknown_is_never_remembered_and_drops_an_older_entry(self):
        read = self.reader("active", "unknown", "unknown", "active")
        self.assertEqual(self.memory.read("u", read, fresh=True), "active")
        self.assertEqual(self.memory.read("u", read, fresh=True), "unknown")
        self.assertEqual(self.memory.read("u", read), "unknown")      # wcześniejsze `active` nie wraca z pamięci
        self.assertEqual(self.memory.read("u", read), "active")
        self.assertEqual(len(self.calls), 4)

    def test_a_raising_read_is_never_remembered_and_drops_an_older_entry(self):
        read = self.reader("active", OSError("brak magistrali"), "active")
        self.memory.read("u", read)
        with self.assertRaises(OSError):
            self.memory.read("u", read, fresh=True)
        self.assertEqual(self.memory.read("u", read), "active")
        self.assertEqual(len(self.calls), 3)

    def test_fresh_reads_every_time_and_refreshes_the_memory(self):
        read = self.reader("active", "inactive")
        self.memory.read("u", read)
        self.clock.now += 20
        self.assertEqual(self.memory.read("u", read, fresh=True), "inactive")
        self.clock.now += 20                                            # 40 s po pierwszym, 20 s po świeżym odczycie
        self.assertEqual(self.memory.read("u", read), "inactive")
        self.assertEqual(len(self.calls), 2)

    def test_engine_without_a_memory_object_reads_every_time(self):
        """Silnik-atrapa bez pamięci (brak atrybutu albo `Mock`): każdy odczyt świeży."""
        read = self.reader("active")
        for engine in (SimpleNamespace(), SimpleNamespace(unit_states=mock.Mock())):
            unit = trialhold._unit_reader(engine, False)
            for _ in range(2):
                self.assertEqual(unit("u", read), "active")
        self.assertEqual(len(self.calls), 4)


HELD = "PT-176"                     # ticket chronionej próby K-38 (`trialpolicy.LEGACY`): tożsamość `held`
BOX = bs.box_name("proj")           # pudełko projektu: zasób kroku `runner=box` ze spójną tożsamością


class InventoryMemoryTest(unittest.TestCase):
    """Karta K-38 z trzymanym procesem (krok chronionej próby z jednostką); dziennik bez prawdziwych procesów."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.board = self.root / "board"
        (self.board / "projects").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root / "state"),
                                           "TIDE_BOARD_DIR": str(self.board)})
        env.start()
        self.addCleanup(env.stop)
        for method in ("run", "Popen", "call"):
            patcher = mock.patch("subprocess." + method, side_effect=AssertionError("zakaz procesu w regresji"))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.journal = Journal(str(self.board))
        self.engine = FlowEngine(SimpleNamespace(root=str(self.board)), journal=self.journal,
                                 codex_roots=[], settings=str(self.root / "settings.json"))
        self.clock = Clock()
        self.engine.unit_states = unitstate.UnitStates(clock=self.clock)
        self.card = "K-7"
        self.journal.append(self.card, "card.started", project="plasma-tide")
        self.states: dict[str, list] = {}
        self.reads: list[str] = []

    def held_step(self, name, **extra):
        self.journal.append(self.card, "step.started", step=name, kind="caretaker", ticket=HELD,
                            unit=f"u-{name}", **extra)

    def host_states(self, unit):
        """Atrapa `stepstop.unit_state`: kolejne wartości z `self.states` (ostatnia powtarzana), wyjątek — rzuca."""
        self.reads.append(unit)
        values = self.states[unit]
        value = values.pop(0) if len(values) > 1 else values[0]
        if isinstance(value, BaseException):
            raise value
        return value

    def inventory(self, **kw):
        with mock.patch.object(stepstop, "unit_state", side_effect=self.host_states):
            return trialhold.inventory(self.engine, self.card, None, **kw)

    def written(self):
        return [e["items"] for e in self.journal.read_complete(self.card) if e["type"] == "k38.inventory"]

    def test_two_observations_within_30_seconds_read_the_host_unit_once_and_again_after_30_seconds(self):
        self.held_step("care")
        self.states["u-care"] = ["active"]
        self.inventory()                     # pierwszy przebieg: zmiana → zapis po świeżym odczycie
        self.assertEqual([[i["state"] for i in items] for items in self.written()], [["active"]])
        self.reads.clear()
        self.inventory()
        self.clock.now += 29
        self.inventory()
        self.assertEqual(self.reads, [], "ticki w ciągu 30 s: stan `active` z pamięci")
        self.clock.now += 1
        self.inventory()
        self.assertEqual(self.reads, ["u-care"], "po 30 s kolejny odczyt")
        self.assertEqual(len(self.written()), 1, "stan bez zmian — bez nowego zapisu")

    def test_box_step_is_read_once_within_30_seconds_at_the_runner_boundary(self):
        self.held_step("boxed", runner=boxstep.RUNNER, box=BOX, boxSlug="proj")
        runner = mock.Mock()
        runner.step_state.return_value = "active"
        with mock.patch.object(self.engine, "_runner_for", return_value=runner):
            self.inventory()
            runner.step_state.reset_mock()
            self.inventory()
            self.clock.now += 10
            self.inventory()
            self.assertEqual(runner.step_state.call_count, 0)
            self.clock.now += unitstate.TTL
            self.inventory()
            self.assertEqual(runner.step_state.call_count, 1)
        self.assertEqual([i["state"] for i in self.written()[-1]], ["active"])

    def test_returned_unknown_is_read_again_in_every_tick(self):
        self.held_step("care")
        self.states["u-care"] = ["unknown"]
        self.inventory()
        self.reads.clear()
        for _ in range(3):
            self.inventory()
        self.assertEqual(self.reads, ["u-care"] * 3, "`unknown` nie jest pamięcią")

    def test_box_runner_error_is_read_again_in_every_tick(self):
        from tide_daemon.tasks import agentcmd
        self.held_step("boxed", runner=boxstep.RUNNER, box=BOX, boxSlug="proj")
        runner = mock.Mock()
        runner.step_state.side_effect = agentcmd.StepError("incus exec: przekroczony czas")
        with mock.patch.object(self.engine, "_runner_for", return_value=runner):
            for _ in range(3):
                self.inventory()
        self.assertEqual(runner.step_state.call_count, 4, "pierwszy przebieg z zapisem czyta dwa razy, potem co tick")
        self.assertEqual([i["state"] for i in self.written()[-1]], ["unknown"])

    def test_a_raising_read_is_read_again_in_every_tick(self):
        self.held_step("care")
        self.states["u-care"] = [OSError("brak magistrali")]
        self.inventory()
        self.reads.clear()
        for _ in range(3):
            self.inventory()
        self.assertEqual(self.reads, ["u-care"] * 3, "wyjątek z odczytu nie jest pamięcią")
        self.assertEqual([i["state"] for i in self.written()[-1]], ["unknown"])

    def test_state_change_written_to_the_inventory_comes_from_a_fresh_read_not_from_the_memory(self):
        self.held_step("care")
        self.states["u-care"] = ["active"]
        self.inventory()
        self.reads.clear()
        # Nowy trzymany proces wymaga zapisu (przekazanie, nowa pozycja). W pamięci `care` jest `active`, a świeży
        # odczyt mówi już `inactive` (→ `unknown`: brak chronionego dowodu hosta, reguła bez zmian).
        self.held_step("care-2")
        self.states["u-care"] = ["inactive"]
        self.states["u-care-2"] = ["active"]
        self.inventory()
        self.assertEqual(sorted(self.reads), ["u-care", "u-care-2", "u-care-2"],
                         "obserwacja: `care` z pamięci; zapis: obie jednostki świeżo")
        items = {i["step"]: i["state"] for i in self.written()[-1]}
        self.assertEqual(items, {"care": "unknown", "care-2": "active"})
        self.reads.clear()
        self.inventory()                                    # świeży odczyt odświeżył pamięć
        self.assertEqual(self.reads, [])
        self.assertEqual(len(self.written()), 2)

    def test_fresh_inventory_reads_every_unit_every_time(self):
        self.held_step("care")
        self.states["u-care"] = ["active"]
        self.inventory()
        self.reads.clear()
        for _ in range(3):
            self.assertFalse(self.inventory(fresh=True))
        self.assertEqual(self.reads, ["u-care"] * 3)

    def test_k38_diagnostic_reads_the_units_fresh_despite_a_warm_memory(self):
        self.card = "K-38"
        self.journal.append("K-38", "card.started", project="plasma-tide")
        self.held_step("care")
        self.states["u-care"] = ["active"]
        self.inventory()                                   # rozgrzana pamięć (obserwacja ticku)
        self.inventory()
        binding = gate.Binding("diagnostic", "1", str(self.root / "disposable"), "attempt", "frozen", "manifest",
                               "sha", tuple((ticket, "sha") for ticket in sorted(gate.DEPENDENCIES)), "review-274")
        procedure = SimpleNamespace(snapshot=lambda: binding, bootstrap_reviewed=lambda: True,
                                    start=mock.Mock(return_value={"started": True}))
        store = HostStore()
        store.record = gate.HostRecord("PT-274", "review-274", binding)
        self.engine.trial_gate = gate.TrialGate(store=store, procedure=procedure)
        name = self.engine.register_trial_diagnostic()["step"]
        self.reads.clear()
        with mock.patch.object(stepstop, "unit_state", side_effect=self.host_states), self.assertRaises(FlowError):
            self.engine.resolve_step("K-38", {"step": name, "choice": "run"})
        self.assertEqual(self.reads, ["u-care"], "bramka diagnostyki czyta stan jednostki świeżo")
        procedure.start.assert_not_called()

    def test_box_lifecycle_check_reads_the_unit_without_the_memory(self):
        """Ochrona zasobów pudełka (`box/lifecycle.active_steps`) pyta `engine._unit_state` za każdym razem."""
        from tide_daemon.box import lifecycle
        self.journal.append(self.card, "step.started", step="plain", kind="ticket", ticket="PT-1", unit="u-plain",
                            runner=boxstep.RUNNER, box=BOX, boxSlug="proj")
        builder = SimpleNamespace(incus=SimpleNamespace(instance=lambda name: {"status": "Running"}),
                                  settings=None)
        with mock.patch.object(self.engine, "_unit_state", return_value="inactive") as state, \
                mock.patch.object(trialhold, "_public_process_inventory", return_value=[]), \
                mock.patch.object(trialhold, "public_processes", return_value=[]), \
                mock.patch.object(lifecycle.bs, "running_steps", return_value=[]):
            for _ in range(2):
                self.assertEqual(lifecycle.active_steps(builder, "proj", BOX, self.engine), [])
        self.assertEqual(state.call_count, 2)


if __name__ == "__main__":
    unittest.main()
