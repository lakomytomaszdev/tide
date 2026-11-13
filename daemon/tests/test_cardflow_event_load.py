"""K-100, PT-423: obciążenie maszyny w `outside.started`/`outside.finished` i padnięte moduły w `merge.check-finished`.

Pola są wyłącznie diagnostyczne (miernik K-100 #6): błąd odczytu loadavg albo `summary.json` nigdy nie przerywa
polecenia, próby ani zapisu zdarzenia, a dzienniki sprzed zmiany (bez tych pól) czyta się jak dotąd — stan karty,
model tożsamości procesów i katalog zdarzeń. Przebieg próby testów po scaleniu z nowymi polami sprawdza
`test_cardflow_merge_check_run`; prośbę `outside` kroku — `StepOutsideLoadTest` niżej.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tide_daemon.cardflow import engine as engine_mod
from tide_daemon.cardflow import mergecheck
from tide_daemon.cardflow import processidentity as pi
from tide_daemon.cardflow.state import derived, fold

from .privatebus import wait_until
from .test_cardflow import FlowInProcess

NAME = "merge-check-PT-2-1"


def runner_report(folder, results, *, header=True, summary=True):
    """Dziennik próby z nagłówkiem uruchamiacza (`tools/test_runner.py`) i jego `summary.json`."""
    report = os.path.join(folder, "run-1")
    os.makedirs(report, exist_ok=True)
    log = os.path.join(folder, "outside.log")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("make test-changed\n")
        if header:
            fh.write(f"Moduły: {len(results)}, JOBS=4, logi: {report}\n")
        fh.write("Moduły: 9, JOBS=1, logi: /nie/ten\n")   # późniejsze wyjście testu nie wskazuje raportu
    if summary:
        with open(os.path.join(report, "summary.json"), "w", encoding="utf-8") as fh:
            json.dump({"seconds": 1.0, "jobs": 4, "results": results}, fh)
    return log, report


def module(name, code=0, failures=(), errors=(), unexpected=()):
    return {"module": name, "returncode": code, "failures": list(failures), "errors": list(errors),
            "unexpected_successes": list(unexpected), "tests": 3}


class FailedFromSummaryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pt423-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_failed_modules_and_tests_come_from_the_runner_summary(self):
        log, _ = runner_report(self.dir, [
            module("daemon/tests/test_a.py"),
            module("daemon/tests/test_b.py", 1, failures=["test_b.B.test_x"], errors=["test_b.B.test_y"]),
            module("apps/board/tests/test_c.py", -9),     # proces zabity: bez nazw testów, sam moduł
            module("daemon/tests/test_d.py", 1, unexpected=["test_d.D.test_z"])])
        self.assertEqual(mergecheck.failed_from_summary(log), (
            ["daemon/tests/test_b.py", "apps/board/tests/test_c.py", "daemon/tests/test_d.py"],
            ["test_b.B.test_x", "test_b.B.test_y", "test_d.D.test_z"]))

    def test_passing_run_gives_empty_lists(self):
        log, _ = runner_report(self.dir, [module("daemon/tests/test_a.py")])
        self.assertEqual(mergecheck.failed_from_summary(log), ([], []))

    def test_missing_or_broken_report_gives_empty_lists_without_error(self):
        cases = {
            "pusty log": lambda: "",
            "brak pliku logu": lambda: os.path.join(self.dir, "brak.log"),
            "bez nagłówka": lambda: runner_report(self.dir, [module("t.py", 1)], header=False)[0],
            "bez summary.json": lambda: runner_report(self.dir, [module("t.py", 1)], summary=False)[0],
        }
        for label, make in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.dir, True)
                os.makedirs(self.dir)
                self.assertEqual(mergecheck.failed_from_summary(make()), ([], []))

    def test_unexpected_schema_gives_empty_or_skips_bad_entries(self):
        log, report = runner_report(self.dir, [])
        path = os.path.join(report, "summary.json")
        for label, content, expected in (
                ("niepoprawny JSON", "{", ([], [])),
                ("bez results", json.dumps({"seconds": 1}), ([], [])),
                ("results nie lista", json.dumps({"results": {"a": 1}}), ([], [])),
                ("lista nie słownik", json.dumps([1, 2]), ([], [])),
                ("złe wpisy pominięte", json.dumps({"results": [
                    "napis", {"module": 5, "returncode": 1}, {"returncode": 1},
                    {"module": "ok.py", "returncode": 2, "failures": "nie-lista", "errors": [3, "e.E.t"]}]}),
                 (["ok.py"], ["e.E.t"]))):
            with self.subTest(label):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)
                self.assertEqual(mergecheck.failed_from_summary(log), expected)

    def test_failed_test_names_are_capped(self):
        names = [f"t.T.test_{n}" for n in range(mergecheck.FAILED_TESTS_MAX + 50)]
        log, _ = runner_report(self.dir, [module("t.py", 1, failures=names)])
        modules, tests = mergecheck.failed_from_summary(log)
        self.assertEqual(modules, ["t.py"])
        self.assertEqual(tests, names[:mergecheck.FAILED_TESTS_MAX])


class MachineLoadTest(unittest.TestCase):
    def test_reads_three_load_averages_and_the_cores_available_to_the_service(self):
        loadavg, cores = engine_mod.machine_load()
        self.assertEqual(len(loadavg), 3)
        self.assertTrue(all(isinstance(v, float) and v >= 0 for v in loadavg))
        self.assertEqual(cores, len(os.sched_getaffinity(0)))

    def test_read_error_gives_none_in_that_field_only(self):
        with mock.patch.object(engine_mod.os, "getloadavg", side_effect=OSError("brak /proc")):
            loadavg, cores = engine_mod.machine_load()
        self.assertIsNone(loadavg)
        self.assertGreaterEqual(cores, 1)
        with mock.patch.object(engine_mod.os, "sched_getaffinity", side_effect=OSError("brak")), \
                mock.patch.object(engine_mod.os, "cpu_count", return_value=None):
            loadavg, cores = engine_mod.machine_load()
        self.assertEqual(len(loadavg), 3)
        self.assertIsNone(cores)


def history(new_fields: bool):
    """Dziennik próby testów po scaleniu ze startem i końcem outside — w starym formacie albo z polami PT-423."""
    load = {"loadavg": [7.1, 6.5, 5.0], "cores": 12} if new_fields else {}
    failed = {"failedModules": ["daemon/tests/test_b.py"], "failedTests": ["test_b.B.test_x"]} if new_fields else {}
    rows = [
        {"type": "card.started", "project": "proj", "entry": "now", "baseVersion": 1},
        {"type": mergecheck.ARMED},
        {"type": mergecheck.REQUESTED, "ticket": "PT-2", "check": "PT-2:1", "start": "a" * 40, "before": "b" * 40,
         "after": "c" * 40, "command": "make test-changed BASE=" + "a" * 40, "source": "makefile"},
        {"type": mergecheck.STEP, "step": NAME, "ticket": "PT-2", "kind": "merge-check", "check": "PT-2:1",
         "command": "make test-changed BASE=" + "a" * 40, "after": "c" * 40, "branch": "card/k-1",
         "worktree": "/w", "stepDir": "/w.step", "baseVersion": 1},
        {"type": "step.decision", "step": NAME, "ticket": "PT-2", "kind": "merge-check", "check": "PT-2:1",
         "choice": "run", "command": "make test-changed BASE=" + "a" * 40},
        {"type": "outside.started", "step": NAME, "ticket": "PT-2", "command": "make test-changed",
         "unit": "tide-outside-" + NAME, "log": "/w.step/outside/outside.log", "exitFile": "/w.step/outside/in/exit-code",
         "worktree": "/w", "head": "c" * 40, **load},
        {"type": "outside.finished", "step": NAME, "ticket": "PT-2", "command": "make test-changed", "exit": 2,
         "tail": "BŁĄD", **load},
        {"type": mergecheck.FINISHED, "ticket": "PT-2", "check": "PT-2:1", "exit": 2,
         "log": "/w.step/outside/outside.log", **failed},
    ]
    return [{**row, "seq": n, "ts": f"2026-11-12T10:00:{n:02d}+00:00"} for n, row in enumerate(rows, 1)]


class StepOutsideLoadTest(FlowInProcess):
    """Prośba `outside` zwykłego kroku: start (`_start_outside`) i koniec (`_outside_ready`) z obciążeniem maszyny
    z wstrzykniętego odczytu; zepsuty odczyt nie zatrzymuje polecenia ani zapisu (wzór: PT-311, deny-wins)."""

    def step(self):
        card = self.card()
        self.flow.stop()
        eng = self.flow.engine
        eng.journal.append(card, "card.started", project="proj", entry="plan", repo=self.repo, baseVersion=1)
        eng.journal.append(card, "plan.approved", rows=[{"id": "PT-1"}])
        folder = os.path.join(self.base, "s1")
        os.makedirs(os.path.join(folder, "out"))
        step = eng.journal.append(card, "step.started", step="s1", ticket="PT-1", kind="ticket", stepDir=folder,
                                  worktree=self.repo, baseVersion=1)
        eng.journal.append(card, "step.finished", step="s1", ticket="PT-1", result="denied", outside=True,
                           command="echo zewnetrzne-ok", canAllow=True)
        eng.journal.append(card, "step.decision", step="s1", ticket="PT-1", kind="ticket", choice="run",
                           answer="", command="echo zewnetrzne-ok", executor="")
        return card, eng, step

    def run_outside(self, card, eng, step):
        st = fold(eng.journal.read(card))
        ready = lambda: eng._outside_ready(card, step, st.finished["s1"], st.step_decisions["s1"])  # noqa: E731
        self.assertIsNone(ready())
        self.assertTrue(wait_until(lambda: ready() is not None, timeout=10))
        events = eng.journal.read(card)
        return ([e for e in events if e["type"] == "outside.started"],
                [e for e in events if e["type"] == "outside.finished"])

    def test_start_and_finish_of_a_step_command_record_the_machine_load(self):
        card, eng, step = self.step()
        eng.machine_load = lambda: ([7.1, 6.5, 5.0], 12)
        started, finished = self.run_outside(card, eng, step)
        self.assertEqual([(e["loadavg"], e["cores"]) for e in started], [([7.1, 6.5, 5.0], 12)])
        self.assertEqual([(e["exit"], e["loadavg"], e["cores"]) for e in finished], [(0, [7.1, 6.5, 5.0], 12)])
        self.assertEqual(pi.outside_run(eng.journal.read(card), "s1")[0], "ok")

    def test_broken_load_probe_does_not_stop_the_command_or_its_events(self):
        card, eng, step = self.step()

        def broken():
            raise RuntimeError("odczyt obciążenia padł")
        eng.machine_load = broken
        started, finished = self.run_outside(card, eng, step)
        self.assertEqual([(e["loadavg"], e["cores"]) for e in started], [(None, None)])
        self.assertEqual([(e["exit"], e["loadavg"], e["cores"]) for e in finished], [(0, None, None)])


NEW_FIELDS = ("loadavg", "cores", "failedModules", "failedTests")


def without_new_fields(value):
    if isinstance(value, dict):
        return {k: without_new_fields(v) for k, v in value.items() if k not in NEW_FIELDS}
    if isinstance(value, list):
        return [without_new_fields(v) for v in value]
    return value


class HistoricalEventsTest(unittest.TestCase):
    """Wpisy bez nowych pól (dzienniki sprzed PT-423) i z nimi dają ten sam stan karty, bramkę i tożsamość."""

    def test_card_state_gate_and_view_are_the_same_with_and_without_the_new_fields(self):
        old, new = fold(history(False)), fold(history(True))
        self.assertEqual(without_new_fields(mergecheck.gate(old)), without_new_fields(mergecheck.gate(new)))
        self.assertEqual([(e["check"], e["status"]) for e in mergecheck.gate(old)], [("PT-2:1", mergecheck.FAILED)])
        # Wynik próby niesie listę padniętych modułów tylko, gdy ją zapisano; stary wpis jej nie ma.
        self.assertEqual(new.merge_checks["PT-2"]["finished"]["failedModules"], ["daemon/tests/test_b.py"])
        self.assertNotIn("failedModules", old.merge_checks["PT-2"]["finished"])
        self.assertEqual(without_new_fields(old.outside_started), without_new_fields(new.outside_started))
        self.assertEqual(old.outside_launched, new.outside_launched)
        view_old, view_new = derived(old, {"column": "progress"}), derived(new, {"column": "progress"})
        self.assertEqual((view_old["column"], view_old["note"]), (view_new["column"], view_new["note"]))
        self.assertEqual(view_old["flow"]["mergeChecks"], view_new["flow"]["mergeChecks"])

    def test_process_identity_is_ok_with_and_without_the_new_fields(self):
        for new_fields in (False, True):
            with self.subTest(new_fields=new_fields):
                state, started, finished = pi.outside_run(history(new_fields), NAME)
                self.assertEqual(state, "ok")
                self.assertEqual((started["unit"], finished["exit"]), ("tide-outside-" + NAME, 2))
                self.assertEqual(pi.outside_runs(history(new_fields)), {})

    def test_catalog_describes_the_new_fields_as_neutral_and_optional(self):
        for kind, names in (("outside.started", ("loadavg", "cores")), ("outside.finished", ("loadavg", "cores")),
                            (mergecheck.FINISHED, ("failedModules", "failedTests"))):
            fields = pi.EVENTS[kind].fields
            for name in names:
                with self.subTest(kind=kind, field=name):
                    self.assertEqual(fields[name].role, "neutral")
                    self.assertFalse(fields[name].nonempty)


if __name__ == "__main__":
    unittest.main()
