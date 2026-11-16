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
import json, os, subprocess, sys, time
LAUNCHES, GATE, NO_EXIT, LATE = {launches!r}, {gate!r}, {no_exit!r}, {late!r}
args = sys.argv[1:]
unit = next((a.split("=", 1)[1] for a in args if a.startswith("--unit=")), "")
with open(LAUNCHES, "a", encoding="utf-8") as fh:
    fh.write(unit + "\n")
journal = os.environ.get("PT485_JOURNAL", "")
if journal and unit.startswith("tide-cmd-"):
    # K-109 PT-485: stan dziennika karty w chwili wywołania klienta — zamiar tej próby musi już tam być.
    attempt = unit[len("tide-cmd-"):]
    with open(journal, encoding="utf-8") as fh:
        kinds = [e.get("type") for e in map(json.loads, filter(str.strip, fh)) if e.get("attempt") == attempt]
    with open(LAUNCHES + ".journal", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(unit=unit, kinds=kinds)) + "\n")
if unit.startswith("tide-cmd-"):
    if os.path.exists(LATE):
        # Wyścig z odzyskiwaniem (PT-485): żądanie wysłane, a jednostka powstaje dopiero po pliku bramki.
        while not os.path.exists(GATE):
            time.sleep(0.05)
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


INTENT_KEYS = START_KEYS - {"loadavg", "cores"} | {"heavy", "exclusive", "project"}
COMMAND = "printenv TIDE_PT479"
OUTPUT = "z-srodowiska\n"       # jeden wiersz w pliku wyniku próby = jedno wykonanie polecenia


class Crash(BaseException):
    """Awaria usługi w środku `_start_outside` (nie `Exception`: nic w silniku jej nie łapie)."""


def body(command: str, exclusive: bool = False) -> str:
    asked = {"command": command, **({"exclusive": True} if exclusive else {})}
    return f"ATRAPA: poza-zadane\nATRAPA-POZA: {json.dumps(asked)}\n"


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
        self.late = os.path.join(self.base, "late")
        real = self.fakes["systemd-run"]
        os.rename(real, real + ".real")
        with open(real, "w", encoding="utf-8") as fh:
            fh.write(COMMAND_SYSTEMD_RUN.replace("{launches!r}", repr(self.launch_log))
                     .replace("{gate!r}", repr(self.gate)).replace("{no_exit!r}", repr(self.no_exit))
                     .replace("{late!r}", repr(self.late)))
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

    def ask(self, command: str, exclusive: bool = False) -> str:
        card = self.card(description="Polecenie poza piaskownicą.\n\n" + body(command, exclusive))
        self.assertTrue(self.start(card)["ok"])
        return card

    def request_of(self, card: str) -> str:
        def found():
            st = self.flow.engine.journal.state(card)
            return next((name for name, done in st.finished.items() if done.get("outside")), None)
        name = wait_until(found, timeout=40)
        self.assertTrue(name, json.dumps(self.events(card), ensure_ascii=False)[-4000:])
        return name

    def asked(self, command: str) -> tuple:
        card = self.ask(command)
        name = self.request_of(card)
        self.flow.stop()
        os.environ["PT485_JOURNAL"] = self.flow.engine.journal.path(card)
        return card, name

    def journal_at_launch(self) -> list:
        """Rodzaje zdarzeń próby w dzienniku karty w chwili każdego wywołania atrapy `systemd-run`."""
        try:
            with open(self.launch_log + ".journal", encoding="utf-8") as fh:
                return [json.loads(line) for line in fh]
        except FileNotFoundError:
            return []

    def executions(self, engine, card, name) -> int:
        """Licznik wykonań polecenia (nie wywołań `systemd-run`): wiersze wyniku we wszystkich próbach kroku."""
        folder = os.path.join(engine.journal.state(card).steps[name]["stepDir"], "outside")
        count = 0
        for attempt in (os.listdir(folder) if os.path.isdir(folder) else ()):
            try:
                with open(os.path.join(folder, attempt, runner_mod.LOG_FILE), encoding="utf-8") as fh:
                    count += fh.read().count(OUTPUT)
            except FileNotFoundError:
                pass
        return count

    def crash_in_start(self, engine, *, after_popen: bool):
        """Awaria usługi w `_start_outside` po zapisie zamiaru: przed wywołaniem `systemd-run` albo zaraz po nim
        (przed zapisem `command.started`). Zwraca zamiar z dziennika."""
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
        self.assertTrue(wait_until(crashed, timeout=30), "start polecenia nie nastąpił")
        engine.runner.run_command = real

    def intent_of(self, engine, card) -> dict:
        [intent] = self.runs(engine, card, commandruns.INTENT)
        return intent

    def engine_now(self):
        engine = self.flow.engine
        engine._read_load = lambda: heavyqueue.Load(1.5, 8)
        # Pola `loadavg`/`cores` zdarzeń przebiegu: odczyt w kształcie K-100 PT-423 (`FlowEngine.machine_load`).
        engine.machine_load = lambda: ([1.5, 1.25, 1.0], 8)
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
             "isolation": "none", "class": "", "consentVersion": "", "loadavg": [1.5, 1.25, 1.0], "cores": 8,
             "source": "step", "head": "", "rerunOf": ""})
        self.assertEqual(set(finished), FINISH_KEYS)
        # Klient-obserwator skończył się kodem 42; wynik to kod polecenia z pliku `exit-code`.
        self.assertEqual((finished["exit"], finished["tail"]), (0, "z-srodowiska\n"))
        self.assertEqual({k: finished[k] for k in ("attempt", "unit", "exitFile", "executor", "isolation", "source")},
                         {k: started[k] for k in ("attempt", "unit", "exitFile", "executor", "isolation", "source")})
        self.assertEqual(self.launches(), [unit])
        self.assert_no_legacy_runs(engine, card)
        # K-109 PT-485: jeden zapis zamiaru z tą samą próbą, jednostką i plikami przed wywołaniem `systemd-run`.
        [intent] = self.runs(engine, card, commandruns.INTENT)
        self.assertEqual(set(intent), INTENT_KEYS)
        self.assertLess(intent["seq"], started["seq"])
        same = START_KEYS - {"v", "seq", "ts", "type", "loadavg", "cores"}
        self.assertEqual({k: intent[k] for k in same}, {k: started[k] for k in same})
        self.assertEqual((intent["heavy"], intent["exclusive"], intent["project"]), (False, False, "proj"))
        # Atrapa czyta dziennik z dysku w chwili wywołania: zamiar już tam jest (start dopisuje usługa po Popen, więc
        # może zdążyć przed odczytem — kolejność i tak rozstrzyga `seq`).
        [seen] = self.journal_at_launch()
        self.assertEqual((seen["unit"], seen["kinds"][:1]), (unit, [commandruns.INTENT]),
                         "systemd-run wywołany bez wcześniejszego zamiaru w dzienniku")
        self.assertEqual(runner_mod.read_marker(runner_mod.marker_of(started["exitFile"])), runner_mod.MARK_RUN)
        self.assertEqual(self.executions(engine, card, name), 1)
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
        self.assertEqual(self.executions(engine, card, name), 1)      # PT-485 (3): po starcie, przed wynikiem
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
        self.assertEqual(self.executions(engine, card, name), 1)      # PT-485 (4): po wyniku, przed przetworzeniem
        self.assert_no_legacy_runs(engine, card)

    # --- K-109 PT-485: restart między zamiarem a startem ------------------------------------------------

    def assert_settled_once(self, engine, card, name, *, executions: int):
        """Kolejne ticki nie dokładają zamiaru, startu ani wykonania; krok wznawia się z wynikiem."""
        for _ in range(6):
            engine.tick()
            time.sleep(0.05)
        of_step = lambda kind: [e for e in self.runs(engine, card, kind) if e.get("step") == name]  # noqa: E731
        self.assertEqual(len(of_step(commandruns.INTENT)), 1)
        self.assertEqual(len(of_step(commandruns.FINISHED)), 1)
        self.assertEqual(self.executions(engine, card, name), executions)
        self.assertNotIn(name, engine.journal.state(card).outside_started)
        with self.assertRaises(Exception):      # ta sama decyzja nie dostaje drugiego startu
            engine._outside_history(card, engine.journal.state(card).steps[name], command=COMMAND)

    def test_1_restart_after_intent_before_unit_start_settles_not_started(self):
        open(self.gate, "a").close()
        card, name = self.asked(COMMAND)
        engine = self.engine_now()
        self.allow(engine, card, name)
        self.crash_in_start(engine, after_popen=False)
        intent = self.intent_of(engine, card)
        self.assertEqual(self.runs(engine, card, commandruns.STARTED), [])
        self.assertIn(name, engine.journal.state(card).outside_started, "zamiar nie zajmuje kroku")
        engine = self.restarted()
        [finished] = self.tick_until(engine, card, commandruns.FINISHED)
        self.assertEqual((finished["exit"], finished["reason"], finished["attempt"], finished["unit"]),
                         ("zatrzymane", "not-started", intent["attempt"], intent["unit"]))
        self.assertEqual(self.runs(engine, card, commandruns.STARTED), [])
        self.assertEqual(self.launches(), [])
        self.assertEqual(runner_mod.read_marker(runner_mod.marker_of(intent["exitFile"])), runner_mod.MARK_ABANDONED)
        self.assert_settled_once(engine, card, name, executions=0)
        self.assertEqual([r for r in commandruns.runs(engine.journal.read(card)) if r.step == name], [],
                         "zamiar bez startu nie jest przebiegiem")
        self.assertTrue(commandruns.attempt_of(engine.journal.read(card), name).not_started)
        self.assert_no_legacy_runs(engine, card)

    def test_2_unit_created_after_recovery_never_runs_the_command(self):
        # Wyścig z systemd: żądanie `systemd-run` wysłane przed awarią, jednostka powstaje dopiero po odzyskaniu.
        open(self.late, "a").close()
        card, name = self.asked(COMMAND)
        engine = self.engine_now()
        self.allow(engine, card, name)
        self.crash_in_start(engine, after_popen=True)
        intent = self.intent_of(engine, card)
        self.assertTrue(wait_until(lambda: self.launches() == [intent["unit"]], timeout=10), self.launches())
        self.assertEqual(engine.runner.command_state(intent["unit"]), "gone", "jednostka jeszcze nie powstała")
        engine = self.restarted()
        [finished] = self.tick_until(engine, card, commandruns.FINISHED)
        self.assertEqual((finished["exit"], finished["reason"]), ("zatrzymane", "not-started"))
        open(self.gate, "a").close()            # jednostka powstaje teraz i uruchamia opakowanie
        self.assertTrue(wait_until(lambda: runner_mod.read_command_exit(intent["exitFile"]) == "zatrzymane",
                                   timeout=20))
        self.assertEqual(runner_mod.read_marker(runner_mod.marker_of(intent["exitFile"])), runner_mod.MARK_ABANDONED)
        self.assertEqual(self.launches(), [intent["unit"]])
        self.assert_settled_once(engine, card, name, executions=0)

    def test_2b_wrapper_already_running_is_recovered_as_started_not_abandoned(self):
        # Znacznik `run` założony przez opakowanie przed awarią: odzyskiwanie nie zakłada `abandoned`, zapisuje
        # odzyskany start i czeka na wynik z `exit-code` tej samej jednostki.
        card, name = self.asked(COMMAND)
        engine = self.engine_now()
        self.allow(engine, card, name)
        self.crash_in_start(engine, after_popen=True)
        intent = self.intent_of(engine, card)
        marker = runner_mod.marker_of(intent["exitFile"])
        open(self.gate, "a").close()            # usługa leży, a opakowanie w jednostce zakłada `run` i kończy
        self.assertTrue(wait_until(lambda: runner_mod.read_command_exit(intent["exitFile"]) == 0, timeout=20))
        self.assertEqual(runner_mod.read_marker(marker), runner_mod.MARK_RUN)
        engine = self.restarted()
        [started] = self.tick_until(engine, card, commandruns.STARTED)
        self.assertIs(started["recovered"], True)
        self.assertEqual({k: started[k] for k in ("attempt", "unit", "exitFile", "log", "command")},
                         {k: intent[k] for k in ("attempt", "unit", "exitFile", "log", "command")})
        [finished] = self.tick_until(engine, card, commandruns.FINISHED)
        self.assertEqual((finished["exit"], finished["tail"], finished.get("reason")), (0, OUTPUT, None))
        self.assertEqual(runner_mod.read_marker(marker), runner_mod.MARK_RUN)
        self.assertEqual(self.launches(), [intent["unit"]])
        self.assert_settled_once(engine, card, name, executions=1)

    def test_heavy_intent_without_start_holds_heavy_queue_until_settled(self):
        open(self.gate, "a").close()
        first, second = self.ask(COMMAND, exclusive=True), self.ask(COMMAND, exclusive=True)
        first_name, second_name = self.request_of(first), self.request_of(second)
        self.flow.stop()
        engine = self.engine_now()
        self.allow(engine, first, first_name)
        self.crash_in_start(engine, after_popen=False)
        intent = self.intent_of(engine, first)
        self.assertEqual((intent["heavy"], intent["exclusive"]), (True, True))
        # Znacznik próby chwilowo niedostępny (od pierwszego ticku nowego silnika): zamiar zostaje
        # nierozstrzygnięty, a jego rezerwacja trwa.
        unavailable = mock.patch.object(runner_mod, "claim_abandoned", side_effect=OSError("brak dostępu"))
        unavailable.start()
        self.addCleanup(unavailable.stop)
        engine = self.restarted()
        self.allow(engine, second, second_name)
        for _ in range(8):
            engine.tick()
            time.sleep(0.05)
        self.assertEqual(self.runs(engine, second, commandruns.INTENT), [], "drugie ciężkie obok zamiaru")
        self.assertIn(first_name, engine._heavy_running())
        self.assertIn(first_name, engine._outside_active)
        held = engine._heavy_view(second).get(second_name)
        self.assertIsInstance(held, str, engine._heavy_held_state())
        self.assertEqual(held, f"czeka na ciszę: biegnie {COMMAND} ({first})")
        self.assertEqual(self.runs(engine, first, commandruns.FINISHED), [])
        unavailable.stop()                      # znacznik znów dostępny: rozstrzygnięcie zwalnia rezerwację
        [finished] = self.tick_until(engine, first, commandruns.FINISHED)
        self.assertEqual(finished["reason"], "not-started")
        self.assertNotIn(first_name, engine._heavy_running())
        [done] = self.tick_until(engine, second, commandruns.FINISHED)
        self.assertEqual(done["exit"], 0)
        self.assertEqual(self.launches(), [self.intent_of(engine, second)["unit"]])


if __name__ == "__main__":
    import unittest
    unittest.main()
