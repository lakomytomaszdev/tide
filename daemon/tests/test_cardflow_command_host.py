"""K-109 PT-479: prośba `outside` kroku przez `Runner.run_command` — przebieg `command.started` → `command.finished`
z wynikiem wyłącznie z pliku `exit-code`, bez zapisu `outside.*`, i odzyskiwanie po restarcie bez ponownego startu.

Cała droga usługi przez `FlowEngine.tick`: prawdziwy krok (atrapa agenta prosi o polecenie `outside`), zgoda przez
`resolve_step`, start przez atrapę klienta `systemd-run`, która liczy uruchomienia, czeka na plik bramki, uruchamia
argv usługi (opakowanie `tide-step --command`) i sama kończy się kodem 42 — innym niż polecenie. Prawdziwy menedżer
systemd: test_runner_command_systemd.py.
"""
import json
import os
import stat
import time
from unittest import mock

from tide_daemon.cardflow import commandruns, heavyqueue
from tide_daemon.tasks import runner as runner_mod

from .privatebus import wait_until
from .test_cardflow import FlowInProcess

# Ścieżki wpisane przy instalacji: krok agenta biegnie z oczyszczonym środowiskiem (bez zmiennych testu).
COMMAND_SYSTEMD_RUN = r'''#!/usr/bin/python3
import os, subprocess, sys, time
LAUNCHES, GATE, NO_EXIT = {launches!r}, {gate!r}, {no_exit!r}
args = sys.argv[1:]
unit = next((a.split("=", 1)[1] for a in args if a.startswith("--unit=")), "")
with open(LAUNCHES, "a", encoding="utf-8") as fh:
    fh.write(unit + "\n")
if unit.startswith("tide-cmd-"):
    # „Jednostka” żyje (plik pid jak w atrapie flowfakes) do pliku bramki; potem argv usługi i kod klienta 42.
    folder = os.path.join(os.environ["XDG_RUNTIME_DIR"], "fake-units")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, unit + ".pid"), "w") as fh:
        fh.write(str(os.getpid()))
    while not os.path.exists(GATE):
        time.sleep(0.05)
    if os.path.exists(NO_EXIT):
        sys.exit(0)             # jednostka skończyła się bez zapisu opakowania
    subprocess.call(args[args.index("--") + 1:])
    sys.exit(42)
os.execv(sys.argv[0] + ".real", [sys.argv[0] + ".real", *args])
'''

START_KEYS = {"v", "seq", "ts", "card", "type", "step", "ticket", "command", "unit", "log", "exitFile", "worktree",
              "head", "rerunOf", "attempt", "executor", "isolation", "class", "consentVersion", "loadavg", "cores",
              "source"}
FINISH_KEYS = {"v", "seq", "ts", "card", "type", "step", "ticket", "command", "exit", "tail", "attempt", "executor",
               "isolation", "class", "consentVersion", "unit", "exitFile", "loadavg", "cores", "source"}


def body(command: str) -> str:
    return f"ATRAPA: poza-zadane\nATRAPA-POZA: {json.dumps({'command': command})}\n"


class CommandRunTickTest(FlowInProcess):
    def setUp(self):
        super().setUp()
        from tide_daemon.cardflow import gitops
        from tide_daemon.tasks.stepstop import sandbox_available
        if not sandbox_available():
            # Piaskownica kroku agenta nie ma zagnieżdżonego bwrap (jak test_cardflow_exclusive_tick.py).
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
        real = self.fakes["systemd-run"]
        os.rename(real, real + ".real")
        with open(real, "w", encoding="utf-8") as fh:
            fh.write(COMMAND_SYSTEMD_RUN.replace("{launches!r}", repr(self.launch_log))
                     .replace("{gate!r}", repr(self.gate)).replace("{no_exit!r}", repr(self.no_exit)))
        os.chmod(real, os.stat(real + ".real").st_mode | stat.S_IXUSR)
        self.addCleanup(lambda: open(self.gate, "a").close())
        os.environ["TIDE_PT479"] = "z-srodowiska"     # przywraca patcher środowiska z FlowInProcess

    # --- pomocnicze ------------------------------------------------------------------------------

    def launches(self) -> list:
        try:
            with open(self.launch_log, encoding="utf-8") as fh:
                return [line.strip() for line in fh if line.startswith("tide-cmd-")]
        except FileNotFoundError:
            return []

    def asked(self, command: str) -> tuple:
        card = self.card(description="Polecenie poza piaskownicą.\n\n" + body(command))
        self.assertTrue(self.start(card)["ok"])

        def found():
            st = self.flow.engine.journal.state(card)
            return next((name for name, done in st.finished.items() if done.get("outside")), None)
        name = wait_until(found, timeout=40)
        self.assertTrue(name, json.dumps(self.events(card), ensure_ascii=False)[-4000:])
        self.flow.stop()
        return card, name

    def engine_now(self):
        engine = self.flow.engine
        engine._read_load = lambda: heavyqueue.Load(1.5, 8)
        return engine

    def restarted(self):
        """Nowy silnik bez pamięci przebiegów (awaria usługi): czyta tylko dziennik."""
        self.flow = self.make_flow()
        self.flow.stop()
        return self.engine_now()

    def allow(self, engine, card, name):
        reply = engine.resolve_step(card, {"choice": "run", "step": name})
        self.assertEqual(reply.get("choice"), "run", reply)

    def runs(self, engine, card, kind):
        return [e for e in engine.journal.read(card) if e.get("type") == kind]

    def tick_until(self, engine, card, kind, timeout=30):
        def done():
            engine.tick()
            return self.runs(engine, card, kind)
        found = wait_until(done, timeout=timeout)
        self.assertTrue(found, json.dumps(engine.journal.read(card), ensure_ascii=False)[-5000:])
        return found

    def assert_no_legacy_runs(self, engine, card):
        kinds = [e.get("type") for e in engine.journal.read(card)]
        self.assertNotIn(commandruns.LEGACY_STARTED, kinds)
        self.assertNotIn(commandruns.LEGACY_FINISHED, kinds)
        self.assertEqual({commandruns.family(e["type"]) for e in engine.journal.read(card)
                          if commandruns.phase(e)}, {"command"})

    # --- przypadki -------------------------------------------------------------------------------

    def test_step_request_writes_command_run_with_result_from_exit_file(self):
        open(self.gate, "a").close()
        card, name = self.asked("printenv TIDE_PT479")
        engine = self.engine_now()
        self.allow(engine, card, name)
        [finished] = self.tick_until(engine, card, commandruns.FINISHED)
        [started] = self.runs(engine, card, commandruns.STARTED)
        self.assertEqual(set(started), START_KEYS)
        attempt = started["attempt"]
        unit = "tide-cmd-" + attempt
        step_dir = engine.journal.state(card).steps[name]["stepDir"]
        self.assertEqual(
            {k: started[k] for k in ("step", "command", "unit", "exitFile", "log", "executor", "isolation", "class",
                                     "consentVersion", "loadavg", "cores", "source", "head", "rerunOf")},
            {"step": name, "command": "printenv TIDE_PT479", "unit": unit,
             "exitFile": os.path.join(step_dir, "outside", unit, "exit-code"),
             "log": os.path.join(step_dir, "outside", unit, "outside.log"), "executor": "host-isolated",
             "isolation": "none", "class": "", "consentVersion": "", "loadavg": 1.5, "cores": 8, "source": "step",
             "head": "", "rerunOf": ""})
        self.assertEqual(set(finished), FINISH_KEYS)
        # Klient-obserwator skończył się kodem 42; wynik to kod polecenia z pliku `exit-code`.
        self.assertEqual((finished["exit"], finished["tail"]), (0, "z-srodowiska\n"))
        self.assertEqual({k: finished[k] for k in ("attempt", "unit", "exitFile", "executor", "isolation", "source")},
                         {k: started[k] for k in ("attempt", "unit", "exitFile", "executor", "isolation", "source")})
        self.assertEqual(self.launches(), [unit])
        self.assert_no_legacy_runs(engine, card)
        # Odczyty przez widok przebiegów: jeden zakończony przebieg nowej rodziny.
        [run] = commandruns.runs(engine.journal.read(card))
        self.assertEqual((run.family, run.attempt, run.unit, run.finished, run.exit), ("command", attempt, unit, True, 0))
        self.assertNotIn(name, engine.journal.state(card).outside_started)

    def test_missing_exit_file_after_unit_end_is_stopped(self):
        open(self.gate, "a").close()
        open(self.no_exit, "a").close()
        card, name = self.asked("printenv TIDE_PT479")
        engine = self.engine_now()
        from tide_daemon.cardflow import engine as engine_mod
        with mock.patch.object(engine_mod, "OUTSIDE_STOP_GRACE", 0.3):
            self.allow(engine, card, name)
            [finished] = self.tick_until(engine, card, commandruns.FINISHED)
        self.assertEqual(finished["exit"], "zatrzymane")
        self.assertEqual(engine_mod.OUTSIDE_STOPPED, runner_mod.STOPPED)
        self.assertEqual(len(self.launches()), 1)
        self.assert_no_legacy_runs(engine, card)

    def test_restart_while_unit_lives_waits_and_takes_result_without_second_start(self):
        card, name = self.asked("printenv TIDE_PT479")
        engine = self.engine_now()
        self.allow(engine, card, name)
        [started] = self.tick_until(engine, card, commandruns.STARTED)
        self.assertTrue(wait_until(lambda: self.launches() == [started["unit"]], timeout=10), self.launches())
        engine = self.restarted()
        for _ in range(8):                      # ticki nowego silnika przy żyjącej jednostce
            engine.tick()
            time.sleep(0.1)
        self.assertEqual(self.runs(engine, card, commandruns.FINISHED), [])
        self.assertIn(name, engine.journal.state(card).outside_started)
        open(self.gate, "a").close()            # polecenie kończy się i zapisuje kod wyjścia
        [finished] = self.tick_until(engine, card, commandruns.FINISHED)
        self.assertEqual((finished["exit"], finished["tail"], finished["attempt"]),
                         (0, "z-srodowiska\n", started["attempt"]))
        self.assertEqual(self.launches(), [started["unit"]], "ponowne uruchomienie polecenia po restarcie")
        self.assertEqual(len(self.runs(engine, card, commandruns.STARTED)), 1)
        self.assert_no_legacy_runs(engine, card)

    def test_restart_after_unit_ended_takes_result_from_exit_file_without_second_start(self):
        card, name = self.asked("printenv TIDE_PT479")
        engine = self.engine_now()
        self.allow(engine, card, name)
        [started] = self.tick_until(engine, card, commandruns.STARTED)
        self.assertTrue(wait_until(lambda: self.launches() == [started["unit"]], timeout=10), self.launches())
        # Usługa leży, a polecenie kończy się w tym czasie (jednostka zebrana przez --collect).
        open(self.gate, "a").close()
        self.assertTrue(wait_until(lambda: runner_mod.read_command_exit(started["exitFile"]) == 0, timeout=20))
        engine = self.restarted()
        [finished] = self.tick_until(engine, card, commandruns.FINISHED)
        self.assertEqual((finished["exit"], finished["attempt"]), (0, started["attempt"]))
        self.assertEqual(self.launches(), [started["unit"]], "ponowne uruchomienie polecenia po restarcie")
        self.assert_no_legacy_runs(engine, card)


if __name__ == "__main__":
    import unittest
    unittest.main()
