"""K-109 PT-481: jeden widok przebiegów poleceń dla historii `outside.*` i nowej rodziny `command.*`.

Fixture dziennika historycznej karty (zgoda, przebieg pełnych testów, który padł, ponowienie padniętych modułów,
które trwa, próba testów po scaleniu i zgoda w kolejce) w dwóch zapisach: dzisiejszym `outside.*` i nowym
`command.intent/started/finished` z tymi samymi danymi. Testy charakteryzujące utrwalają wynik konsumentów na zapisie
`outside.*` (wartości odczytane z kodu sprzed adaptera); testy równoważności — że ten sam scenariusz w `command.*`
daje ten sam wynik. Odzyskiwanie niezakończonego `outside.started` po restarcie (jednostka `tide-outside-<krok>`, bez
ponownego uruchomienia) ma osobny test przez `FlowEngine` z licznikiem atrapy `systemd-run`; od PT-479 start dawną
drogą odtwarza w nim pomocnik `legacy_start` (nowa droga zapisuje `command.*` — test_cardflow_command_host.py).
"""
import contextlib
import dataclasses
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tide_daemon.cardflow import commandruns, commands, guard, mergecheck, processidentity, queueview, testrerun
from tide_daemon.cardflow import trialhold
from tide_daemon.cardflow.state import _epoch, derived, fold, outside_queued

CARD = "K-7"
S1, S2, S3 = "K-7-PT-5-1", "K-7-PT-5-2", "K-7-PT-6-1"
MERGE = mergecheck.step_name("PT-4:1")
FULL = "make test-changed BASE=abc"
SHA = "a" * 40
ROOT = Path(__file__).resolve().parents[2]


def _ts(n: int) -> str:
    return f"2026-11-10T10:{n:02d}:00+01:00"


class Fixture:
    """Dziennik karty K-7 w zapisie `family` (`outside` albo `command`) z tymi samymi danymi przebiegów.

    W zapisie `outside` miejsce zamiaru zajmuje zdarzenie bez znaczenia dla przebiegów (`packs.removed`), więc `seq`
    i `ts` każdego startu i końca są w obu zapisach te same."""

    def __init__(self, root: str, family: str = "outside", ticket: str = "PT-5", unfinished: bool = True):
        self.root, self.family, self.ticket = root, family, ticket
        self.worktree = os.path.join(root, "wt")
        self.card_worktree = os.path.join(root, "card-wt")
        self.events: list[dict] = []
        self.log1, self.log2 = self._log(S1, failed=True), self._log(S2, failed=False)
        self.rerun = testrerun.rerun_command(["daemon/tests/test_a.py"], SHA, S1, FULL)
        t = ticket
        self.add("card.started", project="proj", card=CARD)
        self.step(S1, t, os.path.join(root, "s1"))
        self.ask(S1, t, FULL)
        self.start(S1, t, FULL, self.log1, head=SHA)
        self.finish(S1, t, FULL, exit=1, tail="FAILED (failures=1)")
        self.step(S2, t, os.path.join(root, "s2"), parentStep=S1)
        self.ask(S2, t, FULL)
        self.start(S2, t, self.rerun, self.log2, head=SHA, rerunOf=S1)
        if not unfinished:
            self.finish(S2, t, self.rerun, exit=0, tail="OK")
        # Próba testów po scaleniu PT-4 (pseudokrok w worktree gałęzi karty) — trwa.
        self.add("merge.check-requested", ticket="PT-4", check="PT-4:1", command=FULL, after="c" * 40)
        self.add("merge.check-step", step=MERGE, ticket="PT-4", kind="merge-check", branch="card/k-7",
                 worktree=self.card_worktree, stepDir=os.path.join(root, "m1"), command=FULL)
        self.add("step.decision", step=MERGE, ticket="PT-4", kind="merge-check", choice="run")
        self.start(MERGE, "PT-4", FULL, os.path.join(root, "m1", "outside", "outside.log"), worktree=self.card_worktree)
        # Zgoda PT-6 bez startu: czeka w kolejce.
        self.step(S3, "PT-6", os.path.join(root, "s3"))
        self.ask(S3, "PT-6", "make lint")

    def add(self, event_type: str, /, **fields) -> dict:
        seq = len(self.events) + 1
        event = {"v": 1, "seq": seq, "ts": _ts(seq), "card": CARD, "type": event_type, **fields}
        self.events.append(event)
        return event

    def _log(self, name: str, failed: bool) -> str:
        reports = os.path.join(self.root, "reports", name)
        os.makedirs(reports, exist_ok=True)
        with open(os.path.join(reports, "summary.json"), "w", encoding="utf-8") as fh:
            json.dump({"results": [{"module": "daemon/tests/test_a.py", "returncode": 1 if failed else 0},
                                   {"module": "daemon/tests/test_b.py", "returncode": 0}]}, fh)
        log = os.path.join(self.root, name, "outside", "outside.log")
        os.makedirs(os.path.dirname(log), exist_ok=True)
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(f"Moduły: 2, JOBS=4, logi: {reports}\n")
        return log

    def step(self, name, ticket, step_dir, **extra):
        self.add("step.started", step=name, kind="ticket", ticket=ticket, branch=f"ticket/{ticket.lower()}-x",
                 worktree=self.worktree, stepDir=step_dir, unit=f"tide-step-{name}", **extra)

    def ask(self, name, ticket, command):
        self.add("step.finished", step=name, ticket=ticket, result="denied", outside=True, command=command)
        self.add("step.decision", step=name, ticket=ticket, kind="ticket", choice="run")

    def _attempt(self, name):
        return {"attempt": f"a-{name}", "executor": "host-isolated", "isolation": "none", "class": "",
                "consentVersion": "", "source": "merge-check" if mergecheck.is_step(name) else "step"}

    def start(self, name, ticket, command, log, *, head="", rerunOf="", worktree=None):
        common = dict(step=name, ticket=ticket, command=command, unit=f"tide-outside-{name}", log=log,
                      exitFile=os.path.join(os.path.dirname(log), "in", "exit-code"),
                      worktree=self.worktree if worktree is None else worktree, head=head, rerunOf=rerunOf)
        if self.family == "command":
            self.add(commandruns.INTENT, step=name, ticket=ticket, command=command, **self._attempt(name))
            self.add(commandruns.STARTED, **common, **self._attempt(name))
        else:
            self.add("packs.removed", removed=[])
            self.add(commandruns.LEGACY_STARTED, **common)

    def finish(self, name, ticket, command, **result):
        if self.family == "command":
            self.add(commandruns.FINISHED, step=name, ticket=ticket, command=command, **result, **self._attempt(name))
        else:
            self.add(commandruns.LEGACY_FINISHED, step=name, ticket=ticket, command=command, **result)


def _plain(event):
    """Zdarzenie startu/końca bez nazwy rodziny i pól tylko `command.*` — do porównania obu zapisów."""
    if not isinstance(event, dict):
        return event
    return {k: v for k, v in event.items() if k != "type" and k not in commandruns.COMMAND_ONLY}


def _same_view(run):
    return dataclasses.replace(run, family="", attempt="", intent=False)


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pt481-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def fixture(self, family="outside", root=None, **kwargs):
        return Fixture(root or os.path.join(self.dir, family + str(len(os.listdir(self.dir)))), family, **kwargs)

    def both(self, **kwargs):
        """Ten sam scenariusz (te same ścieżki i pliki) w obu zapisach."""
        root = os.path.join(self.dir, "both" + str(len(os.listdir(self.dir))))
        return self.fixture("outside", root, **kwargs), self.fixture("command", root, **kwargs)


class AdapterViewTest(FixtureCase):
    def test_both_families_give_one_view_of_each_run(self):
        legacy, new = self.both()
        old_runs, new_runs = commandruns.runs(legacy.events), commandruns.runs(new.events)
        self.assertEqual([r.step for r in old_runs], [S1, S2, MERGE])
        first = old_runs[0]
        self.assertEqual((first.ticket, first.command, first.unit, first.head, first.rerun_of, first.finished,
                          first.exit, first.tail, first.family),
                         ("PT-5", FULL, f"tide-outside-{S1}", SHA, "", True, 1, "FAILED (failures=1)", "outside"))
        self.assertEqual((old_runs[1].rerun_of, old_runs[1].finished, old_runs[1].finished_seq), (S1, False, None))
        self.assertEqual(old_runs[1].exit_file, os.path.join(os.path.dirname(legacy.log2), "in", "exit-code"))
        # Ten sam widok z zapisu `command.*`; różni się tylko rodziną, próbą i znacznikiem zamiaru.
        self.assertEqual([_same_view(r) for r in old_runs], [_same_view(r) for r in new_runs])
        self.assertEqual({(r.family, r.attempt, r.intent) for r in new_runs},
                         {("command", f"a-{r.step}", True) for r in new_runs})
        self.assertEqual(commandruns.pending(legacy.events), {})
        self.assertEqual(commandruns.pending(new.events), {})

    def test_mixed_families_on_one_card(self):
        legacy = self.fixture("outside")
        # Przebieg zaczęty starym zapisem i zakończony nowym (przełączenie zapisu w trakcie), potem przebieg w nowym.
        legacy.add(commandruns.FINISHED, step=S2, ticket="PT-5", command=legacy.rerun, exit=0, tail="OK",
                   attempt="", source="step")
        legacy.add(commandruns.INTENT, step=S3, ticket="PT-6", command="make lint", attempt="b1")
        before_start = commandruns.pending(legacy.events)
        legacy.add(commandruns.STARTED, step=S3, ticket="PT-6", command="make lint", unit="tide-cmd-b1",
                   log="/x/outside.log", exitFile="/x/in/exit-code", worktree=legacy.worktree, attempt="b1")
        found = {r.step: r for r in commandruns.runs(legacy.events)}
        self.assertEqual((found[S2].family, found[S2].finished, found[S2].exit), ("outside", True, 0))
        self.assertEqual((found[S3].family, found[S3].unit, found[S3].attempt, found[S3].intent, found[S3].finished),
                         ("command", "tide-cmd-b1", "b1", True, False))
        self.assertEqual(before_start, {S3: "b1"})
        self.assertEqual(commandruns.pending(legacy.events), {})
        st = fold(legacy.events)
        self.assertEqual(set(st.outside_started), {MERGE, S3})
        self.assertEqual(set(st.outside_launched), {S1, S2, MERGE, S3})
        state, started, finished = processidentity.outside_run(legacy.events, S2)
        self.assertEqual((state, started["type"], finished["type"]), ("ok", "outside.started", "command.finished"))
        self.assertEqual(set(processidentity.outside_runs(legacy.events)), {MERGE, S3})

    def test_phases_match_the_process_model_roles(self):
        roles = {"started": "outside-start", "finished": "outside-finish", "intent": "outside-trace"}
        for kind, value in commandruns.PHASES.items():
            with self.subTest(kind=kind):
                self.assertEqual(processidentity.EVENTS[kind].role, roles[value])
        self.assertEqual(commandruns.family("command.future"), "command")
        self.assertEqual(commandruns.family("outside.ticket-consent"), "outside")
        self.assertEqual(commandruns.family("step.started"), "")
        self.assertEqual(commandruns.phase({"type": "outside.ticket-consent"}), "")
        self.assertEqual(commandruns.phase("nie słownik"), "")

    def test_commands_module_exposes_the_same_adapter(self):
        for name in ("phase", "family", "runs", "pending", "Run", "PHASES", "STARTS", "FINISHES", "INTENTS"):
            self.assertIs(getattr(commands, name), getattr(commandruns, name), name)

    def test_adapter_only_reads(self):
        legacy, new = self.both()
        for fixture in (legacy, new):
            snapshot = json.dumps(fixture.events, sort_keys=True)
            commandruns.runs(fixture.events)
            commandruns.pending(fixture.events)
            fold(fixture.events)
            self.assertEqual(json.dumps(fixture.events, sort_keys=True), snapshot)


class CharacterizationTest(FixtureCase):
    """Wynik konsumentów na zapisie `outside.*` (utrwalony przed adapterem) i ten sam wynik na `command.*`."""

    def test_card_state_and_queue(self):
        for family in ("outside", "command"):
            with self.subTest(family=family):
                fx = self.fixture(family)
                st = fold(fx.events)
                self.assertEqual(sorted(st.outside_started), sorted([S2, MERGE]))
                self.assertEqual(sorted(st.outside_launched), sorted([S1, S2, MERGE]))
                self.assertEqual((st.outside_started[S2]["seq"], st.outside_started[S2]["command"],
                                  st.outside_started[S2]["rerunOf"]), (12, fx.rerun, S1))
                self.assertEqual(outside_queued(st), [S3])
                self.assertEqual(mergecheck.phase(st, {"check": "PT-4:1"}), "running")
                self.assertEqual([g["status"] for g in mergecheck.gate(st)], [mergecheck.PENDING])
                view = queueview.build({CARD: st}, {"heavyLimit": 2}, heavy_of=lambda *a: False,
                                       outside_limits=(1, 2), now=_epoch(_ts(30)),
                                       load=lambda: (_ for _ in ()).throw(OSError("brak")))
                running = [(e["step"], e["kind"], e["ticket"], e["command"], e["since"]) for e in view["heavy"]["running"]]
                self.assertEqual(running, [(S2, "outside", "PT-5", fx.rerun, _ts(12)),
                                           (MERGE, "merge-check", "PT-4", FULL, _ts(17))])
                self.assertEqual(view["outside"]["occupied"]["total"], 2)
                self.assertEqual([e["step"] for e in view["outside"]["waiting"]], [S3])
                text = (derived(st, {"id": CARD, "column": "todo"}) or {}).get("text")
                self.assertEqual(text, derived(fold(self.fixture("outside").events),
                                               {"id": CARD, "column": "todo"}).get("text"))

    def test_process_identity_and_slot_occupancy(self):
        legacy, new = self.both()
        for fx in (legacy, new):
            with self.subTest(family=fx.family):
                state, started, finished = processidentity.outside_run(fx.events, S1)
                self.assertEqual((state, started["seq"], finished["seq"], finished["exit"]), ("ok", 6, 7, 1))
                state, started, finished = processidentity.outside_run(fx.events, S2)
                self.assertEqual((state, started["seq"], finished), ("ok", 12, None))
                self.assertEqual({k: v["seq"] for k, v in processidentity.outside_runs(fx.events).items()},
                                 {S2: 12, MERGE: 17})
                identities = processidentity.process_identities(fx.events)
                self.assertEqual({k: i.state for k, i in identities.items()},
                                 {S1: "ok", S2: "ok", S3: "ok", MERGE: "ok"})
        for name in (S1, S2):
            self.assertEqual([_plain(e) for e in processidentity.outside_run(legacy.events, name)[1:]],
                             [_plain(e) for e in processidentity.outside_run(new.events, name)[1:]])

    def test_exit_file_and_busy_after_restart(self):
        legacy, new = self.both()
        for fx in (legacy, new):
            with self.subTest(family=fx.family):
                journal = _Journal({CARD: fx.events})
                self.assertEqual(commands.outside_exit(journal, CARD, S2),
                                 os.path.join(os.path.dirname(fx.log2), "in", "exit-code"))
                state = commands.CommandsState()
                ports = mock.Mock(journal=journal, project=lambda card: (None, mock.Mock(slug="proj")),
                                  outside_exit=lambda card, name: commands.outside_exit(journal, card, name))
                self.assertTrue(commands.outside_busy(ports, state, CARD, total=2, per_project=1))
                self.assertEqual(state.active, {S2: (CARD, f"tide-outside-{S2}"),
                                                MERGE: (CARD, f"tide-outside-{MERGE}")})
                self.assertEqual(commands.outside_describe(journal, state, S2), "echo 'Tide: ten sam HEAD aaaaaaaaaaaa i…"
                                 f" ({CARD})")

    def test_guard_runs(self):
        legacy, new = self.both()
        expected = [{"step": S1, "worktree": guard.where(legacy.worktree), "started": 6, "finished": 7},
                    {"step": S2, "worktree": guard.where(legacy.worktree), "started": 12, "finished": None},
                    {"step": MERGE, "worktree": guard.where(legacy.card_worktree), "started": 17, "finished": None}]
        self.assertEqual(guard.runs(legacy.events), expected)
        self.assertEqual(guard.runs(new.events), expected)

    def test_failed_modules_rerun(self):
        for family in ("outside", "command"):
            with self.subTest(family=family):
                fx = self.fixture(family)
                with mock.patch.object(testrerun, "head", return_value=SHA):
                    command, head, of = testrerun.substitute(fx.events, fx.worktree, FULL)
                self.assertEqual((command, head, of),
                                 (testrerun.rerun_command(["daemon/tests/test_a.py"], SHA, S1, FULL), SHA, S1))
                # Ponowienie trwa (S2 bez końca) — kolejny pełny przebieg nie powtarza go, bo S2 nie jest pełnym testem.
                fx.finish(S2, "PT-5", fx.rerun, exit=0, tail="OK")
                with mock.patch.object(testrerun, "head", return_value=SHA):
                    self.assertEqual(testrerun.substitute(fx.events, fx.worktree, FULL)[2], S1)
                with mock.patch.object(testrerun, "head", return_value="b" * 40):
                    self.assertEqual(testrerun.substitute(fx.events, fx.worktree, FULL), (FULL, "b" * 40, ""))

    def test_trial_inventory_of_held_runs(self):
        for family in ("outside", "command"):
            with self.subTest(family=family):
                fx = self.fixture(family, ticket="PT-173")    # dawna próba K-38: proces wstrzymany (trialpolicy)
                st = fold(fx.events)
                engine = mock.Mock(procs={}, _unit_state=lambda step: "inactive")
                with mock.patch.object(trialhold.stepstop, "unit_state", return_value="active"):
                    items, _ = trialhold._inventory_items(engine, st, fx.events)
                outside = [(i["step"], i["unit"], i["state"]) for i in items if i["kind"] == "outside"]
                self.assertEqual(outside, [(S1, f"tide-outside-{S1}", "active"), (S2, f"tide-outside-{S2}", "active")])

    def test_metrics_counters(self):
        outputs = []
        for family in ("outside", "command"):
            fx = self.fixture(family, unfinished=False)
            metrics = _metrics()
            with mock.patch.object(metrics, "events", lambda since: iter([(CARD, fx.events)])), \
                    mock.patch.object(sys, "argv", ["metrics.py", "--since", "2026-11-01"]), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                metrics.main()
            outputs.append(out.getvalue().splitlines()[1])
        # Dwa pełne przebiegi (S1 padł, S2 to ponowienie modułów — też liczone jako przebieg testów), oba naraz max 1.
        self.assertEqual(outputs[0], "2026-11-10 |    2 (+0 czerw.) |    50% |   0 |   1 |    0 |   0 |      - | 0.00")
        self.assertEqual(outputs[1], outputs[0])


COUNTING_SYSTEMD_RUN = r'''#!/usr/bin/python3
import os, sys, time
# Ścieżki wpisane przy instalacji: krok agenta biegnie z oczyszczonym środowiskiem (bez zmiennych testu).
LAUNCHES, RELEASE = {launches!r}, {release!r}
args = sys.argv[1:]
unit = next((a.split("=", 1)[1] for a in args if a.startswith("--unit=")), "")
with open(LAUNCHES, "a", encoding="utf-8") as fh:
    fh.write(unit + "\n")
if unit.startswith("tide-outside-"):
    # Jednostka „żyje” (plik pid jak w atrapie flowfakes) do zwolnienia przez test: polecenie trwa podczas restartu.
    folder = os.path.join(os.environ["XDG_RUNTIME_DIR"], "fake-units")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, unit + ".pid"), "w") as fh:
        fh.write(str(os.getpid()))
    while not os.path.exists(RELEASE):
        time.sleep(0.05)
os.execv(sys.argv[0] + ".real", [sys.argv[0] + ".real", *args])
'''


def _flow_case():
    from .test_cardflow import FlowInProcess
    return FlowInProcess


class RestartRecoveryTest(_flow_case()):
    """Niezakończone `outside.started` po restarcie usługi: odzyskane przez jednostkę `tide-outside-<krok>` (wynik
    z pliku kodu wyjścia), bez ponownego uruchomienia polecenia (licznik wywołań atrapy `systemd-run`)."""

    def setUp(self):
        super().setUp()
        self.launch_log = os.path.join(self.base, "launches.log")
        self.release = os.path.join(self.base, "release-outside")
        real = self.fakes["systemd-run"]
        os.rename(real, real + ".real")
        with open(real, "w", encoding="utf-8") as fh:
            fh.write(COUNTING_SYSTEMD_RUN.replace("{launches!r}", repr(self.launch_log))
                     .replace("{release!r}", repr(self.release)))
        os.chmod(real, os.stat(real + ".real").st_mode | stat.S_IXUSR)
        self.addCleanup(lambda: open(self.release, "a").close())

    def launches(self):
        try:
            with open(self.launch_log, encoding="utf-8") as fh:
                return [line.strip() for line in fh if line.startswith("tide-outside-")]
        except FileNotFoundError:
            return []

    def decide(self, card_id, **request):
        from .privatebus import wait_until
        box = []
        self.board.resolve_card_step(card_id, json.dumps(request), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20), "ResolveCardStep bez odpowiedzi")
        return json.loads(box[0])

    def legacy_start(self, engine):
        """Start polecenia dawną drogą sprzed PT-479 (historia `outside.started`, jednostka `--scope`
        `tide-outside-<krok>`): od PT-479 prośba kroku zapisuje `command.*`, a ten test utrwala odzyskiwanie
        przebiegów zapisanych przed przełączeniem. argv to dawne `commands.outside_launch` (usunięte w PT-483, gdy
        merge-check przeszedł na `Runner.run_command`) — tu jako fixture dawnej drogi."""
        import subprocess
        from tide_daemon.tasks import agentcmd

        def start(card, step, command):
            name = step.get("step") or ""
            worktree = step.get("worktree") or ""
            folder = os.path.join(step.get("stepDir") or "", "outside")
            os.makedirs(os.path.join(folder, "in"), exist_ok=True)
            exit_file, log, unit = os.path.join(folder, "in", "exit-code"), os.path.join(folder, "outside.log"), \
                f"tide-outside-{name}"
            env = commands.outside_env(worktree, old_jobs=4)
            argv = [engine.systemd_run or "systemd-run", "--user", "--scope", "--collect", "--quiet",
                    f"--unit={unit}", "-p", "CPUWeight=40", "-p", "IOWeight=40", "--",
                    "/usr/bin/env", "-u", "INVOCATION_ID", "/bin/sh", "-c", agentcmd.EXIT_WRAPPER, "tide-outside",
                    exit_file, "/bin/sh", "-c", command]
            with open(log, "ab") as out, engine.journal.lock:
                subprocess.Popen(argv, cwd=worktree or None, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                 stderr=subprocess.STDOUT, start_new_session=True)
                engine.journal.append(card, "outside.started", step=name, ticket=step.get("ticket") or "",
                                      command=command, unit=unit, log=log, exitFile=exit_file, worktree=worktree,
                                      head="", rerunOf="")
        engine._start_outside = start

    def test_unfinished_outside_run_is_recovered_by_its_unit_without_a_second_start(self):
        import time
        from .privatebus import wait_until
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: poza")
        self.assertTrue(self.start(card_id)["ok"])
        self.wait_column(card_id, "waiting")
        self.legacy_start(self.flow.engine)
        reply = self.decide(card_id, choice="run")
        self.assertTrue(reply["ok"], (reply, [(e["type"], e.get("result"), e.get("reason"), e.get("error")) for e in self.events(card_id)]))
        self.assertTrue(wait_until(lambda: self.of(card_id, "outside.started"), timeout=20), self.events(card_id))
        [started] = self.of(card_id, "outside.started")
        unit = "tide-outside-" + started["step"]
        self.assertEqual(started["unit"], unit)
        self.assertTrue(wait_until(lambda: self.launches() == [unit], timeout=10), self.launches())
        # Awaria usługi w trakcie polecenia: nowy silnik bez pamięci przebiegów czyta tylko dziennik.
        self.flow.stop()
        self.flow = self.make_flow()
        time.sleep(1.5)                         # kilka ticków nowego silnika (co 150 ms) przy żyjącej jednostce
        self.assertEqual(self.of(card_id, "outside.finished"), [])
        self.assertEqual(self.launches(), [unit])
        open(self.release, "a").close()         # polecenie kończy się i zapisuje kod wyjścia
        self.wait_column(card_id, "review", timeout=40)
        finished = self.of(card_id, "outside.finished")
        self.assertEqual([(e["step"], e["exit"], e["tail"].strip()) for e in finished],
                         [(started["step"], 0, "zewnetrzne-ok")])
        self.assertEqual(self.launches(), [unit], "ponowne uruchomienie polecenia po restarcie")
        # Widok przebiegów tej karty: jeden przebieg w starym zapisie, zakończony.
        [run] = commandruns.runs(self.events(card_id))
        self.assertEqual((run.family, run.unit, run.finished, run.exit), ("outside", unit, True, 0))


class _Journal:
    def __init__(self, cards):
        self.cards_data = cards

    def cards(self):
        return list(self.cards_data)

    def read(self, card):
        return list(self.cards_data[card])

    def state(self, card):
        return fold(self.cards_data[card])


def _metrics():
    spec = importlib.util.spec_from_file_location("pt481_metrics", ROOT / "tools" / "coordinator" / "metrics.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
