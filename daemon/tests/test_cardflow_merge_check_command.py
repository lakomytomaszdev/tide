"""K-109 PT-483: próba testów po scaleniu przez `Runner.run_command` z protokołem jednego wykonania (PT-479, PT-485).

Pełny przepływ karty z prawdziwym gitem (dwa tickety bez blokerów i trzeci zablokowany; drugie scalenie nie jest
„fast-forward” i zakłada próbę), bez reguły projektu: próba czeka na zgodę, więc test przejmuje sterowanie (wątek
przepływu zatrzymany) i dalej woła wyłącznie `FlowEngine.tick`. Restart usługi = nowy silnik bez pamięci przebiegów.

Atrapa klienta `systemd-run` (z test_cardflow_command_host.py) zapisuje wywołania, czeka na plik bramki, uruchamia
argv usługi (opakowanie `tide-step --command`) i sama kończy się kodem 42 — innym niż polecenie. Licznik wykonań
polecenia to wiersze, które `make test-changed` dopisuje do pliku poza worktree (nie liczba wywołań `systemd-run`).

Kryterium każdego restartu: 0 podwójnych prób (licznik wykonań ≤ 1, jeden zamiar) i rozstrzygnięty wynik scalenia
(`merge.check-finished`): 0 otwiera bramkę, a zamiar porzucony bez startu (`not-started`) daje `zatrzymane` — porażkę,
nigdy udaną próbę.
"""
import json
import os
import stat
import time
from unittest import mock

from tide_daemon.cardflow import commandruns, heavyqueue, mergecheck
from tide_daemon.tasks import runner as runner_mod

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_command_host import COMMAND_SYSTEMD_RUN, Crash
from . import test_cardflow_merge_check as gate_tests     # klasa PT-318 tylko przez moduł: jej testy biegną raz

COUNTED_MAKEFILE = "test-changed:\n\t@echo przebieg >> \"$$PT483_COUNTER\"\n\t@echo \"testy zmian od $(BASE)\"\n"


class MergeCheckCommandTest(FlowInProcess):
    merge_checks_pass = False
    _GATE = gate_tests.MergeCheckFlowTest
    proposal, submit, approve = _GATE.proposal, _GATE.submit, _GATE.approve
    commit_files, parallel_plan, run_plan = _GATE.commit_files, _GATE.parallel_plan, _GATE.run_plan
    merges, checks, ticket_starts, state, settle = _GATE.merges, _GATE.checks, _GATE.ticket_starts, _GATE.state, \
        _GATE.settle
    flow_view = _GATE.flow_view
    assert_check_for_merge = _GATE.assert_check_for_merge
    assert_first_merge_only_after_tide_plan_commits = _GATE.assert_first_merge_only_after_tide_plan_commits
    assert_one_check_for_second_merge = _GATE.assert_one_check_for_second_merge
    del _GATE

    def setUp(self):
        super().setUp()
        from tide_daemon.cardflow import gitops
        from tide_daemon.tasks.stepstop import sandbox_available
        if not sandbox_available():
            # Piaskownica kroku agenta nie ma zagnieżdżonego bwrap (jak test_cardflow_command_host.py).
            from .test_cardflow_gitops import LocalStepGit

            class ObjectGit(LocalStepGit):
                def __init__(self, *args, objects_only=False):
                    super().__init__(*args)
            for patch in (mock.patch.object(gitops, "sandbox_available", return_value=True),
                          mock.patch.object(gitops, "StepGit", ObjectGit)):
                patch.start()
                self.addCleanup(patch.stop)
        self.launch_log = os.path.join(self.base, "launches.log")
        self.gate = os.path.join(self.base, "gate")
        self.no_exit = os.path.join(self.base, "no-exit")
        self.late = os.path.join(self.base, "late")
        self.counter = os.path.join(self.base, "executions")
        real = self.fakes["systemd-run"]
        os.rename(real, real + ".real")
        with open(real, "w", encoding="utf-8") as fh:
            fh.write(COMMAND_SYSTEMD_RUN.replace("{launches!r}", repr(self.launch_log))
                     .replace("{gate!r}", repr(self.gate)).replace("{no_exit!r}", repr(self.no_exit))
                     .replace("{late!r}", repr(self.late)))
        os.chmod(real, os.stat(real + ".real").st_mode | stat.S_IXUSR)
        self.addCleanup(lambda: open(self.gate, "a").close())
        os.environ["PT483_COUNTER"] = self.counter     # przywraca patcher środowiska z FlowInProcess
        self.argvs = []

    # --- pomocnicze ------------------------------------------------------------------------------

    def asked(self):
        """Karta z próbą testów po scaleniu czekającą na zgodę; wątek przepływu zatrzymany. Zwraca (karta, próba,
        pseudokrok)."""
        self.commit_files(self.repo, {"Makefile": COUNTED_MAKEFILE})
        card = self.card()
        self.mapping = self.run_plan(card, count=2, blocked=True)
        _first, _second, check = self.assert_one_check_for_second_merge(card)
        name = mergecheck.step_name(check["check"])
        self.assertTrue(wait_until(lambda: any(d["step"] == name and d["kind"] == "permission"
                                               for d in self.flow_view(card).get("decisions") or []), timeout=30),
                        self.flow_view(card))
        self.flow.stop()
        os.environ["PT485_JOURNAL"] = self.flow.engine.journal.path(card)
        return card, check, name

    def engine_now(self):
        engine = self.flow.engine
        engine._read_load = lambda: heavyqueue.Load(1.5, 8)
        real = engine.runner.run_command

        def recorded(request):
            handle = real(request)
            self.argvs.append(list(handle.argv))
            return handle
        engine.runner.run_command = recorded
        return engine

    def restarted(self):
        """Nowy silnik bez pamięci przebiegów (awaria usługi): czyta tylko dziennik."""
        self.flow = self.make_flow()
        self.flow.stop()
        return self.engine_now()

    def allow(self, engine, card, name):
        reply = engine.resolve_step(card, {"choice": "run", "step": name})
        self.assertEqual(reply.get("choice"), "run", reply)

    def runs(self, engine, card, kind, name):
        return [e for e in engine.journal.read(card) if e.get("type") == kind and e.get("step") == name]

    def check_results(self, engine, card, check):
        return [e for e in engine.journal.read(card)
                if e.get("type") == mergecheck.FINISHED and e.get("check") == check["check"]]

    def tick_until(self, engine, found, timeout=40):
        def done():
            engine.tick()
            return found()
        result = wait_until(done, timeout=timeout)
        self.assertTrue(result, json.dumps(engine.journal.read(self.card_id), ensure_ascii=False)[-5000:])
        return result

    def launches(self) -> list:
        try:
            with open(self.launch_log, encoding="utf-8") as fh:
                return [line.strip() for line in fh if line.startswith("tide-cmd-")]
        except FileNotFoundError:
            return []

    def executions(self) -> int:
        try:
            with open(self.counter, encoding="utf-8") as fh:
                return fh.read().count("przebieg\n")
        except FileNotFoundError:
            return 0

    def crash_in_start(self, engine, *, after_popen: bool):
        """Awaria usługi w `merge_check_start` po zapisie zamiaru: przed wywołaniem `systemd-run` albo zaraz po nim
        (przed zapisem `command.started`)."""
        real = engine.runner.run_command

        def run(request):
            if after_popen:
                real(request)
            raise Crash("awaria usługi")
        engine.runner.run_command = run

        def crashed():
            try:
                engine.tick()
            except Crash:
                return True
            return False
        self.assertTrue(wait_until(crashed, timeout=30), "start próby nie nastąpił")
        engine.runner.run_command = real

    def held(self, engine, card):
        return [(entry["check"], entry["status"]) for entry in mergecheck.gate(engine.journal.state(card))]

    def assert_settled_once(self, engine, card, check, name, *, executions: int, status: str):
        """Kolejne ticki nie dokładają zamiaru, startu, wyniku ani wykonania; bramka ma rozstrzygnięty stan."""
        for _ in range(6):
            engine.tick()
            time.sleep(0.05)
        self.assertEqual(len(self.runs(engine, card, commandruns.INTENT, name)), 1)
        self.assertLessEqual(len(self.runs(engine, card, commandruns.STARTED, name)), 1)
        self.assertEqual(len(self.runs(engine, card, commandruns.FINISHED, name)), 1)
        self.assertEqual(len(self.check_results(engine, card, check)), 1)
        self.assertEqual(self.executions(), executions)
        self.assertNotIn(name, engine.journal.state(card).outside_started)
        self.assertNotIn(name, engine._outside_active or {})
        self.assertEqual(self.held(engine, card), [(check["check"], status)] if status else [])
        kinds = {e["type"] for e in engine.journal.read(card) if e.get("step") == name and commandruns.phase(e)}
        self.assertNotIn(commandruns.LEGACY_STARTED, kinds)
        self.assertNotIn(commandruns.LEGACY_FINISHED, kinds)
        with self.assertRaises(Exception):      # ta sama próba nie dostaje drugiego startu
            engine._merge_check_history(card, fresh=True, starting=name)

    # --- przypadki -------------------------------------------------------------------------------

    def test_check_writes_intent_before_systemd_run_and_command_run_with_merge_check_source(self):
        open(self.gate, "a").close()
        card, check, name = self.asked()
        self.card_id = card
        engine = self.engine_now()
        self.allow(engine, card, name)
        [result] = self.tick_until(engine, lambda: self.check_results(engine, card, check))
        [intent] = self.runs(engine, card, commandruns.INTENT, name)
        [started] = self.runs(engine, card, commandruns.STARTED, name)
        [finished] = self.runs(engine, card, commandruns.FINISHED, name)
        attempt = intent["attempt"]
        unit = runner_mod.COMMAND_PREFIX + attempt
        step_dir = engine.journal.state(card).merge_check_steps[name]["stepDir"]
        command = f"make test-changed BASE={check['start']}"
        for event in (intent, started, finished):
            self.assertEqual({k: event[k] for k in ("source", "attempt", "unit", "command", "executor", "isolation")},
                             {"source": "merge-check", "attempt": attempt, "unit": unit, "command": command,
                              "executor": "host-isolated", "isolation": "none"})
        self.assertEqual((intent["heavy"], intent["exclusive"], intent["project"]), (True, False, "proj"))
        self.assertEqual((intent["exitFile"], intent["log"]),
                         (os.path.join(step_dir, "outside", unit, "exit-code"),
                          os.path.join(step_dir, "outside", unit, "outside.log")))
        self.assertLess(intent["seq"], started["seq"])
        self.assertLess(started["seq"], finished["seq"])
        self.assertEqual((started["head"], started["worktree"]), (intent["head"], intent["worktree"]))
        self.assertTrue(started["head"])
        # Wynik z pliku `exit-code` (klient-obserwator skończył się kodem 42) i ten sam `exit` w wyniku próby.
        self.assertEqual((finished["exit"], result["exit"], result["log"]), (0, 0, started["log"]))
        # Zamiar zapisany przed wywołaniem klienta `systemd-run` (atrapa czyta dziennik z dysku).
        with open(self.launch_log + ".journal", encoding="utf-8") as fh:
            [seen] = [json.loads(line) for line in fh]
        self.assertEqual((seen["unit"], seen["kinds"][:1]), (unit, [commandruns.INTENT]))
        # argv składa runner: przejściowa usługa z RuntimeMaxSec i opakowaniem z plikiem `exit-code`, bez `--scope`.
        [argv] = self.argvs
        self.assertNotIn("--scope", argv)
        self.assertIn("--wait", argv)
        self.assertIn(f"--unit={unit}", argv)
        self.assertTrue(any(a.startswith("RuntimeMaxSec=") for a in argv), argv)
        self.assertEqual(argv[argv.index("--") + 1:argv.index("--") + 4],
                         ["/bin/sh", runner_mod.TIDE_STEP_SCRIPT, "--command"])
        self.assertIn(intent["exitFile"], argv)
        self.assertEqual(runner_mod.read_marker(runner_mod.marker_of(intent["exitFile"])), runner_mod.MARK_RUN)
        self.assertEqual(self.launches(), [unit])
        self.assert_settled_once(engine, card, check, name, executions=1, status="")

    def test_1_restart_after_intent_before_unit_start_settles_the_check_as_stopped(self):
        open(self.gate, "a").close()
        card, check, name = self.asked()
        self.card_id = card
        engine = self.engine_now()
        self.allow(engine, card, name)
        self.crash_in_start(engine, after_popen=False)
        [intent] = self.runs(engine, card, commandruns.INTENT, name)
        self.assertEqual(self.runs(engine, card, commandruns.STARTED, name), [])
        self.assertEqual(self.held(engine, card), [(check["check"], "pending")], "bramka czeka na wynik")
        engine = self.restarted()
        [result] = self.tick_until(engine, lambda: self.check_results(engine, card, check))
        [finished] = self.runs(engine, card, commandruns.FINISHED, name)
        self.assertEqual((finished["exit"], finished["reason"], finished["attempt"], finished["source"]),
                         ("zatrzymane", "not-started", intent["attempt"], "merge-check"))
        self.assertEqual(result["exit"], "zatrzymane")
        self.assertFalse(mergecheck.is_pass(result["exit"]), "not-started nie jest udaną próbą")
        self.assertEqual(self.launches(), [])
        self.assertEqual(runner_mod.read_marker(runner_mod.marker_of(intent["exitFile"])), runner_mod.MARK_ABANDONED)
        self.assert_settled_once(engine, card, check, name, executions=0, status=mergecheck.FAILED)
        self.assertFalse(self.ticket_starts(card, self.mapping["#3"]), "bramka trzyma zablokowany ticket")

    def test_2_unit_created_after_recovery_never_runs_the_command(self):
        # Wyścig z systemd: żądanie `systemd-run` wysłane przed awarią, jednostka powstaje dopiero po odzyskaniu.
        open(self.late, "a").close()
        card, check, name = self.asked()
        self.card_id = card
        engine = self.engine_now()
        self.allow(engine, card, name)
        self.crash_in_start(engine, after_popen=True)
        [intent] = self.runs(engine, card, commandruns.INTENT, name)
        self.assertTrue(wait_until(lambda: self.launches() == [intent["unit"]], timeout=10), self.launches())
        self.assertEqual(engine.runner.command_state(intent["unit"]), "gone", "jednostka jeszcze nie powstała")
        engine = self.restarted()
        [result] = self.tick_until(engine, lambda: self.check_results(engine, card, check))
        self.assertEqual(result["exit"], "zatrzymane")
        open(self.gate, "a").close()            # jednostka powstaje teraz i uruchamia opakowanie
        self.assertTrue(wait_until(lambda: runner_mod.read_command_exit(intent["exitFile"]) == "zatrzymane",
                                   timeout=20))
        self.assertEqual(runner_mod.read_marker(runner_mod.marker_of(intent["exitFile"])), runner_mod.MARK_ABANDONED)
        self.assertEqual(self.launches(), [intent["unit"]])
        self.assert_settled_once(engine, card, check, name, executions=0, status=mergecheck.FAILED)

    def test_2b_wrapper_already_running_is_recovered_as_started_and_its_result_counts(self):
        card, check, name = self.asked()
        self.card_id = card
        engine = self.engine_now()
        self.allow(engine, card, name)
        self.crash_in_start(engine, after_popen=True)
        [intent] = self.runs(engine, card, commandruns.INTENT, name)
        open(self.gate, "a").close()            # usługa leży, a opakowanie w jednostce zakłada `run` i kończy
        self.assertTrue(wait_until(lambda: runner_mod.read_command_exit(intent["exitFile"]) == 0, timeout=30))
        engine = self.restarted()
        [result] = self.tick_until(engine, lambda: self.check_results(engine, card, check))
        [started] = self.runs(engine, card, commandruns.STARTED, name)
        self.assertIs(started["recovered"], True)
        self.assertEqual((result["exit"], started["attempt"]), (0, intent["attempt"]))
        self.assertEqual(self.launches(), [intent["unit"]])
        self.assert_settled_once(engine, card, check, name, executions=1, status="")

    def test_3_restart_while_unit_lives_waits_and_takes_the_result_without_second_start(self):
        card, check, name = self.asked()
        self.card_id = card
        engine = self.engine_now()
        self.allow(engine, card, name)
        [started] = self.tick_until(engine, lambda: self.runs(engine, card, commandruns.STARTED, name))
        self.assertTrue(wait_until(lambda: self.launches() == [started["unit"]], timeout=10), self.launches())
        engine = self.restarted()
        for _ in range(8):                      # ticki nowego silnika przy żyjącej jednostce
            engine.tick()
            time.sleep(0.1)
        self.assertEqual(self.check_results(engine, card, check), [])
        self.assertEqual(self.held(engine, card), [(check["check"], "pending")], "bramka czeka na wynik")
        self.assertIn(name, engine.journal.state(card).outside_started)
        open(self.gate, "a").close()            # polecenie kończy się i zapisuje kod wyjścia
        [result] = self.tick_until(engine, lambda: self.check_results(engine, card, check))
        self.assertEqual(result["exit"], 0)
        self.assertEqual(self.launches(), [started["unit"]], "ponowne uruchomienie próby po restarcie")
        self.assert_settled_once(engine, card, check, name, executions=1, status="")

    def test_4_restart_after_result_before_processing_takes_it_from_the_exit_file(self):
        card, check, name = self.asked()
        self.card_id = card
        engine = self.engine_now()
        self.allow(engine, card, name)
        [started] = self.tick_until(engine, lambda: self.runs(engine, card, commandruns.STARTED, name))
        # Usługa leży, a polecenie kończy się w tym czasie (jednostka zebrana przez --collect).
        open(self.gate, "a").close()
        self.assertTrue(wait_until(lambda: runner_mod.read_command_exit(started["exitFile"]) == 0, timeout=30))
        engine = self.restarted()
        [result] = self.tick_until(engine, lambda: self.check_results(engine, card, check))
        self.assertEqual(result["exit"], 0)
        self.assertEqual(self.launches(), [started["unit"]], "ponowne uruchomienie próby po restarcie")
        self.assert_settled_once(engine, card, check, name, executions=1, status="")


if __name__ == "__main__":
    import unittest
    unittest.main()
