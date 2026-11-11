"""K-98 #4 (PT-345): `processes.probe` monitora sesji poza wątkiem głównym (D-Bus).

- sonda trzymana na barierze: przeliczenie listy sesji, Snapshot i Panel karty odpowiadają przed jej zwolnieniem,
  a wynik sondy pojawia się w migawce dopiero po zwolnieniu,
- akcje użytkownika (aktywacja sesji, otwarcie wątku Codexa, Meta+A) działają na sondzie rozpoczętej po wywołaniu —
  nigdy na starszej migawce ani na sondzie, która trwała już w chwili wywołania; wersje D-Bus odpowiadają z opóźnieniem,
- decyzje przepływu karty (uruchomienie, odzyskiwanie, zgoda) nie czytają wyniku sondy: uszkodzona historia i nieznana
  tożsamość odmawiają bez skutku także przy świeżej sondzie, która widzi proces kroku,
- wynik sondy jest publikowany atomowo; przed pierwszym wynikiem i przy zawieszonej sondzie migawka mówi to jawnie
  (`processes`: `none` / `stale` / `error`) zamiast listy sesji bez procesów,
- zawieszenie albo wyjątek sondy nie blokuje odczytów ani następnej sondy: jedna sonda naraz, jeden wątek.

Testy nie mierzą czasu (pomiar p95 pierwszego wywołania: `perf_dbus_first_call.py`) — sprawdzają kolejność, liczniki
i zachowanie; czekanie ma tylko limity bezpieczeństwa."""
import contextlib
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from unittest import mock

from tide_daemon import procprobe, windows
from tide_daemon.agents import AgentScanner
from tide_daemon.monitor import AgentMonitor
from tide_daemon.processes import ProcessInfo, ProcessTable
from tide_daemon.procprobe import ProcessProber

from tide_daemon.agents.timeutil import parse_ts

from . import flowfakes
from .privatebus import app
from .test_codex_desktop import ASKS, DONE, NOW, at, meta, uid

KONSOLE = {"KONSOLE_DBUS_SERVICE": ":1.9", "KONSOLE_DBUS_WINDOW": "/Windows/1", "KONSOLE_DBUS_SESSION": "/Sessions/2"}


def pump(until, timeout=15.0):
    deadline = time.monotonic() + timeout
    while not until() and time.monotonic() < deadline:
        app().processEvents()
        time.sleep(0.002)
    return until()


def probe_threads():
    return [t for t in threading.enumerate() if t.name == procprobe.THREAD_NAME and t.is_alive()]


class HeldProbe:
    """Atrapa `processes.probe`: zwraca `self.table` z chwili STARTU sondy (albo rzuca `self.error`) — jak /proc
    czytane od początku przebiegu; `hold()` zatrzymuje kolejne wywołanie na barierze aż do `release()`. Liczy
    wywołania i zapisuje chwile startu (zegar monotoniczny)."""

    def __init__(self, table=None):
        self.table = table
        self.error = None
        self.calls = 0
        self.starts = []
        self.threads = []
        self.entered = threading.Event()
        self._gate = None

    def hold(self):
        self.entered.clear()
        self._gate = threading.Event()
        return self._gate

    def release(self):
        if self._gate is not None:
            self._gate.set()

    def __call__(self):
        self.calls += 1
        self.starts.append(time.monotonic())
        self.threads.append(threading.current_thread().name)
        gate, table, error = self._gate, self.table, self.error
        self.entered.set()
        if gate is not None:
            gate.wait(30)
            self._gate = None if self._gate is gate else self._gate
        if error is not None:
            raise error
        return table


class ProberTest(unittest.TestCase):
    def prober(self, probe, delivered=None):
        prober = ProcessProber(probe, delivered=delivered)
        self.addCleanup(lambda: (probe.release(), prober.stop(1)))
        return prober

    def test_requests_during_a_probe_coalesce_into_one_next_probe_on_one_thread(self):
        probe = HeldProbe(ProcessTable([]))
        prober = self.prober(probe)
        gate = probe.hold()
        first = prober.request()
        self.assertTrue(probe.entered.wait(10))
        tickets = {prober.request() for _ in range(20)}
        self.assertEqual(tickets, {first + 1}, "prośby w trakcie sondy czekają na jedną następną")
        self.assertEqual(len(probe_threads()), 1)
        gate.set()
        result = prober.wait(first + 1, 10)
        self.assertIsNotNone(result)
        self.assertEqual(result.seq, first + 1)
        self.assertEqual(probe.calls, 2, "20 próśb w trakcie sondy = jedna następna sonda")
        self.assertTrue(all(name == procprobe.THREAD_NAME for name in probe.threads), probe.threads)

    def test_result_for_a_ticket_started_after_the_request(self):
        probe = HeldProbe(ProcessTable([]))
        prober = self.prober(probe)
        gate = probe.hold()
        prober.request()
        self.assertTrue(probe.entered.wait(10))
        asked = time.monotonic()
        ticket = prober.request()
        gate.set()
        result = prober.wait(ticket, 10)
        self.assertGreaterEqual(result.started, asked, "wynik pochodzi z sondy, która zaczęła się po prośbie")

    def test_exception_gives_an_error_result_and_the_next_probe_runs(self):
        probe = HeldProbe(ProcessTable([]))
        probe.error = OSError("brak /proc")
        prober = self.prober(probe)
        bad = prober.wait(prober.request(), 10)
        self.assertEqual((bad.table, "OSError" in bad.error), (None, True))
        self.assertEqual(prober.status(60)["state"], "error")
        probe.error = None
        good = prober.wait(prober.request(), 10)
        self.assertIsNotNone(good.table)
        self.assertEqual(prober.status(60)["state"], "fresh")
        self.assertEqual(len(probe_threads()), 1)

    def test_hung_probe_times_out_the_waiter_keeps_one_thread_and_resumes(self):
        probe = HeldProbe(ProcessTable([]))
        prober = self.prober(probe)
        first = prober.wait(prober.request(), 10)
        gate = probe.hold()
        hung = prober.request()
        self.assertTrue(probe.entered.wait(10))
        self.assertIsNone(prober.wait(hung, 0.05), "czekanie akcji ma limit — zawieszona sonda nie trzyma wołającego")
        self.assertIs(prober.latest, first, "odczyt dostaje ostatni wynik bez czekania")
        for _ in range(50):
            prober.request()
        self.assertEqual(len(probe_threads()), 1, "zawieszona sonda nie mnoży wątków")
        status = prober.status(0.0)
        self.assertEqual(status["state"], "stale")
        self.assertIsNotNone(status["probingS"])
        gate.set()
        after = prober.wait(hung + 1, 10)
        self.assertIsNotNone(after, "po zawieszeniu biegnie jedna następna sonda")
        self.assertEqual(probe.calls, 3)


class Fixture(unittest.TestCase):
    """AgentMonitor z prawdziwym skanerem: sesja Codexa CLI czeka na odpowiedź (Meta+A ją wybiera), a jej okno
    (Konsole → bash → codex z otwartym dziennikiem) zna tylko migawka procesów."""

    def setUp(self):
        app()
        self.tmp = tempfile.mkdtemp(prefix="tide-probe-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        day = os.path.join(self.tmp, "codex", "sessions", "2026", "10", "26")
        os.makedirs(day)
        self.paths = {}
        for n, records in ((6, [meta(uid(6), originator="codex_cli_rs")] + ASKS),
                           (7, [meta(uid(7), originator="codex_cli_rs")] + DONE)):
            path = os.path.join(day, f"rollout-2026-10-26T11-00-00-{uid(n)}.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("".join(json.dumps(r) + "\n" for r in records))
            last = parse_ts(records[-1]["timestamp"])
            os.utime(path, (last, last))
            self.paths[n] = path
        scanner = AgentScanner(os.path.join(self.tmp, "brak"), os.path.join(self.tmp, "codex"))
        self.probe = HeldProbe(self.table())
        self.monitor = AgentMonitor(scanner, use_processes=True, clock=lambda: NOW, probe_wait=10.0)
        self.monitor._probe = self.probe
        self.addCleanup(self.cleanup)
        scanner.discover(NOW)
        scanner.update()
        self.raised = []
        patcher = mock.patch.object(windows, "activate", side_effect=lambda t: self.raised.append(t) or True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cleanup(self):
        self.probe.release()
        self.monitor.stop()

    def table(self, pid=400, with_agent=True):
        procs = [ProcessInfo(1, 0, "systemd"), ProcessInfo(300, 1, "konsole", parse_ts(at("10:00:00"))),
                 ProcessInfo(301, 300, "bash", parse_ts(at("10:00:01")))]
        if with_agent:
            procs.append(ProcessInfo(pid, 301, "codex", parse_ts(at("10:59:00")), cwd="/srv/w", tool="codex",
                                     env=KONSOLE, open_files=(self.paths[6],)))
        return ProcessTable(procs)

    def session(self, n=6):
        return next(s for s in json.loads(self.monitor.sessions_json()) if s["sessionId"] == uid(n))

    def settle(self):
        """Pierwszy wynik sondy opublikowany (jak usługa po starcie)."""
        self.monitor.recompute()
        self.assertTrue(pump(lambda: self.monitor.processes_status()["state"] == "fresh"))


class ProbeOffMainThreadTest(Fixture):
    def test_recompute_does_not_wait_for_the_probe_and_the_result_appears_after_release(self):
        gate = self.probe.hold()
        self.monitor.recompute()          # tick: wraca od razu, sonda stoi na barierze w swoim wątku
        self.assertTrue(self.probe.entered.wait(10))
        self.assertFalse(gate.is_set())
        self.assertEqual(self.probe.threads, [procprobe.THREAD_NAME])
        before = self.session()
        self.assertEqual((before["processes"], before["pid"]), ("none", 0))
        self.assertNotEqual(before["state"], "ended", "brak danych o procesach nie udaje zakończonej sesji")
        self.assertTrue(json.loads(self.monitor.sessions_json()), "lista sesji nie jest pusta przed pierwszą sondą")
        pump(lambda: False, 0.05)
        self.assertEqual(self.session()["processes"], "none", "wynik nie pojawia się przed zwolnieniem sondy")
        gate.set()
        self.assertTrue(pump(lambda: self.session()["processes"] == "fresh"))
        after = self.session()
        self.assertEqual((after["pid"], after["canActivate"]), (400, True))

    def test_snapshot_is_published_atomically(self):
        self.settle()
        old = self.monitor.procs
        built = threading.Event()
        proceed = threading.Event()

        def partial():
            """Nowa tabela budowana w sondzie: w połowie listy sonda stoi."""
            out = []
            for proc in self.table(pid=500).by_pid.values():
                out.append(proc)
                if len(out) == 2:
                    built.set()
                    proceed.wait(30)
            return ProcessTable(out)
        self.monitor._probe = partial
        self.addCleanup(proceed.set)
        self.monitor.recompute()
        self.assertTrue(built.wait(10))
        self.monitor.recompute()
        self.assertIs(self.monitor.procs, old, "migawka to wciąż poprzednia, pełna tabela")
        self.assertEqual(len(self.monitor.procs.by_pid), 4)
        self.assertEqual(self.session()["pid"], 400)
        proceed.set()
        self.assertTrue(pump(lambda: self.session()["pid"] == 500))
        self.assertEqual(len(self.monitor.procs.by_pid), 4, "nowa tabela pojawia się w całości")

    def test_hung_probe_is_explicit_and_does_not_block_reads(self):
        self.settle()
        self.monitor.stale_after = 0.0
        gate = self.probe.hold()
        self.monitor.recompute()
        self.assertTrue(self.probe.entered.wait(10))
        for _ in range(10):
            self.monitor.recompute()     # kolejne ticki i odczyty nie czekają na zawieszoną sondę
        status = self.monitor.processes_status()
        self.assertEqual(status["state"], "stale")
        self.assertIsNotNone(status["ageS"])
        self.assertEqual(self.session()["processes"], "stale")
        self.assertEqual(self.session()["pid"], 400, "ostatnia pełna migawka zostaje do prezentacji")
        self.assertEqual(len(probe_threads()), 1)
        calls = self.probe.calls
        gate.set()
        self.monitor.stale_after = 60.0
        self.assertTrue(pump(lambda: self.probe.calls == calls + 1 and self.monitor.processes_status()["state"]
                             == "fresh"), "po zawieszeniu jedna następna sonda")
        pump(lambda: False, 0.05)
        self.assertEqual(self.probe.calls, calls + 1, "11 próśb w trakcie zawieszenia = jedna następna sonda")

    def test_probe_exception_is_explicit_and_the_next_probe_recovers(self):
        self.probe.error = OSError("odmowa /proc")
        self.monitor.recompute()
        self.assertTrue(pump(lambda: self.monitor.processes_status()["state"] == "error"))
        self.assertEqual(self.session()["processes"], "error")
        self.assertFalse(self.monitor.activate(f"codex:{uid(6)}"), "akcja nie działa bez świeżej migawki")
        self.assertEqual(self.raised, [])
        self.probe.error = None
        self.monitor.recompute()
        self.assertTrue(pump(lambda: self.session()["processes"] == "fresh"))
        self.assertEqual(len(probe_threads()), 1)

    def test_no_processes_mode_keeps_the_old_shape(self):
        monitor = AgentMonitor(self.monitor.scanner, use_processes=False, clock=lambda: NOW)
        self.addCleanup(monitor.stop)
        monitor.recompute()
        self.assertNotIn("processes", monitor.sessions[0])
        self.assertEqual(monitor.processes_status()["state"], "off")
        self.assertEqual(self.probe.calls, 0)


class FreshForUserActionsTest(Fixture):
    def released_soon(self, gate, swap=None):
        """Zwolnienie sondy z innego wątku (wątek główny czeka w akcji); `swap` — tabela dla następnych sond."""
        def later():
            self.probe.entered.wait(10)
            time.sleep(0.05)
            if swap is not None:
                self.probe.table = swap
            gate.set()
        thread = threading.Thread(target=later)
        thread.start()
        self.addCleanup(thread.join, 10)

    def test_activation_uses_a_probe_newer_than_the_call(self):
        self.probe.table = self.table(with_agent=False)
        self.settle()
        self.assertEqual(self.session()["canActivate"], False, "w opublikowanej migawce sesja nie ma okna")
        self.probe.table = self.table()                 # agent działa teraz
        called = time.monotonic()
        self.assertTrue(self.monitor.activate(f"codex:{uid(6)}"))
        self.assertEqual(self.raised[0]["pids"], [301, 300])
        self.assertGreaterEqual(self.monitor._result.started, called, "decyzja na sondzie nowszej niż wywołanie")

    def test_activation_refuses_on_a_fresh_probe_without_the_process(self):
        self.settle()
        self.assertTrue(self.session()["canActivate"], "opublikowana migawka ma okno")
        self.probe.table = self.table(with_agent=False)  # agent już nie działa
        self.assertFalse(self.monitor.activate(f"codex:{uid(6)}"), "przeterminowana migawka nie aktywuje okna")
        self.assertEqual(self.raised, [])

    def test_probe_already_running_at_the_call_does_not_count(self):
        self.settle()
        gate = self.probe.hold()
        self.monitor.recompute()                # sonda wystartowała PRZED wciśnięciem i widzi agenta
        self.assertTrue(self.probe.entered.wait(10))
        called = time.monotonic()
        # Sonda w toku kończy się z tabelą z agentem (odczytaną przy jej starcie); dopiero następna, rozpoczęta po
        # wywołaniu, rozstrzyga — agenta już nie ma.
        self.released_soon(gate, swap=self.table(with_agent=False))
        self.assertFalse(self.monitor.activate_longest_waiting())
        self.assertEqual(self.raised, [])
        self.assertGreaterEqual(self.monitor._result.started, called)

    def test_meta_a_picks_and_activates_on_a_probe_newer_than_the_call(self):
        self.probe.table = self.table(with_agent=False)
        self.settle()
        self.probe.table = self.table()
        called = time.monotonic()
        self.assertTrue(self.monitor.activate_longest_waiting())
        self.assertEqual(self.raised[0]["konsole"]["session"], "/Sessions/2")
        self.assertGreaterEqual(self.monitor._result.started, called)
        self.assertEqual(self.session()["processes"], "fresh")

    def test_hung_probe_refuses_the_action_after_an_explicit_short_wait(self):
        self.settle()
        self.monitor.probe_wait = 0.05
        gate = self.probe.hold()
        self.monitor.recompute()
        self.assertTrue(self.probe.entered.wait(10))
        self.assertFalse(self.monitor.activate(f"codex:{uid(6)}"))
        self.assertFalse(self.monitor.activate_longest_waiting())
        self.assertEqual(self.raised, [])
        gate.set()

    def test_codex_thread_opening_waits_for_a_fresh_probe(self):
        class App:
            opened = []

            def available(self):
                return True

            def open_thread(self, thread_id):
                self.opened.append(thread_id)
                return True
        self.monitor.scanner.codex_app = App()
        self.settle()
        calls = self.probe.calls
        self.assertTrue(self.monitor.codex_threads().open_thread(uid(42)))
        self.assertEqual(self.probe.calls, calls + 1, "otwarcie wątku czeka na nową sondę")
        self.monitor.probe_wait = 0.05
        gate = self.probe.hold()
        self.monitor.recompute()
        self.assertTrue(self.probe.entered.wait(10))
        self.assertFalse(self.monitor.codex_threads().open_thread(uid(43)), "bez świeżej migawki nie otwieramy")
        gate.set()

    def test_dbus_activation_replies_later_without_waiting_on_the_main_thread(self):
        self.probe.table = self.table(with_agent=False)
        self.settle()
        self.assertFalse(self.session()["canActivate"])
        self.probe.table = self.table()                 # agent działa od teraz
        gate = self.probe.hold()
        replies, meta_a = [], []
        self.monitor.activate_later(f"codex:{uid(6)}", replies.append)
        self.monitor.activate_longest_waiting_later(meta_a.append)
        self.assertTrue(self.probe.entered.wait(10))
        # Wątek główny wolny: odczyty odpowiadają, a odpowiedzi aktywacji jeszcze nie ma.
        self.assertTrue(json.loads(self.monitor.sessions_json()))
        pump(lambda: False, 0.05)
        self.assertEqual((replies, meta_a), ([], []))
        gate.set()
        self.assertTrue(pump(lambda: replies and meta_a))
        self.assertEqual((replies, meta_a), ([True], [True]))
        self.assertEqual(len(self.raised), 2)

    def test_dbus_activation_waiting_for_an_older_running_probe_takes_the_next_one(self):
        self.settle()
        gate = self.probe.hold()
        self.monitor.recompute()
        self.assertTrue(self.probe.entered.wait(10))   # sonda w toku widzi agenta
        replies = []
        self.monitor.activate_later(f"codex:{uid(6)}", replies.append)
        self.probe.table = self.table(with_agent=False)  # po zwolnieniu następna sonda już go nie widzi
        gate.set()
        self.assertTrue(pump(lambda: replies))
        self.assertEqual(replies, [False])
        self.assertEqual(self.raised, [])

    def test_dbus_activation_with_a_hung_probe_answers_false_after_the_wait(self):
        self.settle()
        self.monitor.probe_wait = 0.05
        gate = self.probe.hold()
        replies = []
        self.monitor.activate_later(f"codex:{uid(6)}", replies.append)
        self.assertTrue(self.probe.entered.wait(10))
        self.assertTrue(pump(lambda: replies))
        self.assertEqual(replies, [False])
        gate.set()
        pump(lambda: self.monitor.processes_status()["state"] == "fresh")
        self.assertEqual(replies, [False], "spóźniony wynik nie odpowiada drugi raz")


class OneSessionsPerTickTest(Fixture):
    """PT-385 (M1): tick monitora przy niezmienionej tabeli procesów (sonda przyrostowa oddaje TEN SAM obiekt,
    PT-346) wykonuje jedno `scanner.sessions` — jak na masterze przed K-98; nowa tabela albo zmiana stanu migawki
    publikuje nową listę po sondzie. Akcje użytkownika dalej czekają na sondę nowszą niż wywołanie."""

    def setUp(self):
        super().setUp()
        self.settle()
        self.calls = []
        real = self.monitor.scanner.sessions
        patcher = mock.patch.object(self.monitor.scanner, "sessions",
                                    side_effect=lambda *a, **k: self.calls.append(1) or real(*a, **k))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tick(self):
        """Jeden tick (`recompute`) i doręczenie jego sondy (`_probed`) w wątku głównym."""
        seq = self.monitor._last.seq
        self.monitor.recompute()
        self.assertTrue(pump(lambda: self.monitor._last.seq > seq), "sonda ticku nie doszła")
        app().processEvents()

    def test_unchanged_table_costs_one_sessions_call_per_tick(self):
        table = self.monitor.procs
        for _ in range(5):
            self.tick()
        self.assertIs(self.monitor.procs, table, "atrapa oddaje ten sam obiekt tabeli")
        self.assertEqual(len(self.calls), 5, "5 ticków bez zmian — 5 `scanner.sessions`, nie 10")
        self.assertEqual(self.session()["processes"], "fresh")
        self.assertEqual(self.monitor.processes_status()["seq"], self.monitor._last.seq,
                         "wynik, numer i wiek migawki aktualizowane mimo braku przeliczenia")

    def test_new_table_publishes_the_new_list_after_the_probe(self):
        self.probe.table = self.table(pid=500)
        self.tick()
        self.assertEqual(len(self.calls), 2, "tick na starej tabeli + przeliczenie po nowej sondzie")
        self.assertEqual(self.session()["pid"], 500)

    def test_state_change_with_the_same_table_publishes(self):
        table = self.probe.table
        self.probe.error = OSError("odmowa /proc")
        self.tick()
        self.assertEqual(self.session()["processes"], "error")
        self.probe.error = None
        self.calls.clear()
        self.tick()
        self.assertIs(self.monitor.procs, table)
        self.assertEqual(len(self.calls), 2, "error → fresh na tej samej tabeli — przeliczenie po sondzie")
        self.assertEqual(self.session()["processes"], "fresh")

    def test_user_actions_still_wait_for_a_probe_newer_than_the_call(self):
        self.tick()
        called = time.monotonic()
        self.assertTrue(self.monitor.activate_longest_waiting())
        self.assertGreaterEqual(self.monitor._result.started, called, "Meta+A na sondzie nowszej niż wywołanie")
        self.probe.table = self.table(with_agent=False)
        self.assertTrue(self.monitor.refresh_fresh())
        self.assertEqual(self.session()["pid"], 0, "refresh_fresh widzi świeżą sondę")
        replies = []
        self.probe.table = self.table()
        called = time.monotonic()
        self.monitor.activate_later(f"codex:{uid(6)}", replies.append)
        self.assertTrue(pump(lambda: replies))
        self.assertEqual(replies, [True])
        self.assertGreaterEqual(self.monitor._result.started, called)


class WorktreeRemovalTest(Fixture):
    """Usuwanie worktree decyduje, gdzie działa agent (`agent_cwds`) — na sondzie nowszej niż wywołanie (jak przed
    PT-345, gdy `recompute` sondował w miejscu), a bez świeżej migawki odmawia bez skutku."""

    def worktrees(self):
        from tide_daemon.worktree_monitor import WorktreeMonitor
        from tide_daemon.worktrees import scanner as worktrees
        scanner = mock.Mock()
        scanner.remove.side_effect = lambda paths, cwds, **kw: [{"path": p, "removed": False, "cwds": sorted(cwds)}
                                                                  for p in paths]
        monitor = WorktreeMonitor(scanner, lambda: worktrees.agent_cwds(self.monitor.sessions, self.monitor.procs),
                                  refresh_agents=self.monitor.refresh_fresh)
        monitor.publish = lambda: None
        self.addCleanup(monitor.stop)
        return monitor, scanner

    def test_removal_sees_an_agent_started_after_the_published_snapshot(self):
        self.probe.table = self.table(with_agent=False)
        self.settle()
        self.probe.table = self.table()          # agent wystartował po ostatnim ticku
        monitor, scanner = self.worktrees()
        replies = []
        monitor.remove(["/srv/w"], replies.append)
        self.assertTrue(pump(lambda: replies))
        cwds = scanner.remove.call_args[0][1]
        self.assertIn("/srv/w", cwds, "decyzja o usuwaniu widzi agenta ze świeżej sondy")

    def test_removal_refuses_without_a_fresh_probe(self):
        self.settle()
        self.monitor.probe_wait = 0.05
        gate = self.probe.hold()
        self.monitor.recompute()
        self.assertTrue(self.probe.entered.wait(10))
        monitor, scanner = self.worktrees()
        replies = []
        monitor.remove(["/srv/w"], replies.append)
        gate.set()
        self.assertEqual(json.loads(replies[0])[0]["status"], "refused")
        scanner.remove.assert_not_called()


@contextlib.contextmanager
def held_probe(probe):
    gate = probe.hold()
    try:
        yield gate
    finally:
        gate.set()


# Moduły z bazowymi klasami usługi w procesie importujemy jako moduły, żeby loader nie zbierał ich testów drugi raz.
from . import test_cardflow_busy_reads as busy_reads  # noqa: E402
from . import test_cardflow_tick_reads as tick_reads  # noqa: E402


class BoardReadsWhileProbeHeldTest(busy_reads.BusyReadsCase):
    """Usługa w procesie jak w app.py: monitor sesji → BoardMonitor (rederive) i przepływ (sesje kroków)."""

    def setUp(self):
        super().setUp()
        self.agents = Fixture("setUp")
        self.agents.setUp()
        self.addCleanup(self.agents.doCleanups)
        monitor = self.agents.monitor
        self.engine.sessions_source = lambda: monitor.raw_sessions
        monitor.changed.connect(self.board.rederive)
        self.monitor = monitor

    def test_snapshot_and_card_panel_answer_before_the_probe_is_released(self):
        card = self.plasma_cards[0]
        rederived = []
        self.monitor.changed.connect(lambda: rederived.append(1))   # obok board.rederive (jak app.start_board)
        if True:
            with held_probe(self.agents.probe) as gate:
                self.monitor.recompute()                    # tick monitora sesji
                self.assertTrue(self.agents.probe.entered.wait(10))
                self.board._plans_mark = None
                snap = json.loads(self.board.snapshot_json())
                self.assertEqual(snap["readState"], "ready")
                panel = self.card_of(snap, card)["derived"]["flow"]
                self.assertTrue(panel, "Panel karty bez danych")
                self.assertFalse(gate.is_set(), "odpowiedzi przyszły przed zwolnieniem sondy")
                self.assertEqual(self.agents.session()["processes"], "none")
                self.assertEqual(self.agents.session()["pid"], 0)
                waiting = len(rederived)
            self.assertTrue(pump(lambda: self.agents.session()["processes"] == "fresh"))
            self.assertEqual(self.agents.session()["pid"], 400, "wynik sondy w migawce dopiero po zwolnieniu")
            self.assertGreater(len(rederived), waiting, "nowa migawka sesji przelicza Tablicę")
        self.assertEqual(json.loads(self.board.snapshot_json())["readState"], "ready")


class DecisionsIgnoreTheProbeTest(tick_reads.TickFixture):
    """Świeża sonda widzi żywy proces kroku i sesję w stanie „working”, a decyzje przepływu i tak odmawiają."""

    def setUp(self):
        super().setUp()
        self.sources = []

    def wire(self, engine):
        """Jak start_card_flow: sesje monitora (z migawką procesów) jako źródło widoku silnika; liczy odczyty."""
        table = ProcessTable([ProcessInfo(1, 0, "systemd"),
                              ProcessInfo(self.pid, 1, "claude", time.time(), tool="claude")])
        probe = HeldProbe(table)
        monitor = AgentMonitor(AgentScanner(os.path.join(self.base, "brak"), os.path.join(self.base, "brak-codex")),
                               use_processes=True)
        monitor._probe = probe
        self.addCleanup(monitor.stop)
        monitor.recompute()
        self.assertTrue(pump(lambda: monitor.processes_status()["state"] == "fresh"))
        self.assertIn(self.pid, monitor.procs.by_pid, "świeża sonda widzi proces kroku")
        live = [{"id": f"{self.step.get('program')}:{self.step.get('session')}", "state": "working",
                 "pid": self.pid, "processes": "fresh"}]

        def sessions():
            self.sources.append(1)
            return live
        engine.sessions_source = sessions
        return probe

    def journal_bytes(self):
        out = []
        for path in (self.journal.path(self.card_id), self.journal.mirror_path(self.card_id)):
            with open(path, "rb") as fh:
                out.append(fh.read())
        return out

    def test_incomplete_identity_refuses_recovery_with_a_fresh_probe(self):
        events = self.journal.read_complete(self.card_id)
        broken = dict(next(e for e in events if e["type"] == "step.started"))
        broken.pop("ticket", None)
        broken.update(seq=len(events) + 1, unit="")
        for path in (self.journal.path(self.card_id), self.journal.mirror_path(self.card_id)):
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events + [broken]))
        engine = self.restarted()
        probe = self.wire(engine)
        calls = probe.calls
        written = self.journal_bytes()
        from tide_daemon.cardflow import boxstep
        with mock.patch.object(engine, "_recover_started_steps") as recover, \
                mock.patch.object(boxstep, "recover_network") as network:
            engine.tick()
            engine.tick()
        recover.assert_not_called()
        network.assert_not_called()
        self.assertIn("odzyskiwanie wstrzymane", engine._warnings.get("journal", ""))
        self.assertEqual(self.journal_bytes(), written, "odmowa bez skutku")
        self.assertEqual((self.sources, probe.calls), ([], calls), "tick nie czyta sesji ani sondy procesów")

    def test_damaged_history_refuses_start_and_recovery_with_a_fresh_probe(self):
        self.lose_step_started()          # bez step.started: odzyskanie startu przy restarcie
        with open(self.journal.path(self.card_id), "a", encoding="utf-8") as fh:
            fh.write('{"v": 1, "seq": \n')    # ucięty zapis — uszkodzona historia
        with open(self.journal.mirror_path(self.card_id), "a", encoding="utf-8") as fh:
            fh.write('{"v": 1, "seq": \n')
        engine = self.restarted()
        probe = self.wire(engine)
        calls = probe.calls
        written = self.journal_bytes()
        engine.tick()
        engine.tick()
        self.assertEqual(self.journal_bytes(), written, "uszkodzona historia: żaden zapis, żaden start")
        self.assertTrue(engine._warnings, "odmowa jest widoczna")
        self.assertEqual((self.sources, probe.calls), ([], calls), "tick nie czyta sesji ani sondy procesów")
        self.assertEqual(engine.procs, {}, "bez nowego procesu kroku")
        self.assertTrue(flowfakes.alive(self.pid), "odmowa nie kończy procesu bez dowodu")


if __name__ == "__main__":
    unittest.main()
