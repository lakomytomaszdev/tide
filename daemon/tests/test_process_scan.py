"""K-98 #5 (PT-346): przyrostowa sonda procesów (`processes.Prober`) na atrapie /proc i zegarze.

- na postoju (bez nowych i zakończonych procesów) w 20 kolejnych przebiegach najwyżej 1 pełny skan (odczyt
  `/proc/<pid>/stat` wszystkich procesów); stara funkcja `processes.probe` (baza ticketu) robi pełny skan zawsze,
- „brak zmian” to ten sam zbiór PID-ów z jednego `listdir` i niezmienieni agenci — bez pełnego skanu,
- nowy i zakończony potomek agenta, przełączenie dziennika agenta i zmiana rodzica po śmierci przodka trafiają do
  migawki w pierwszej sondzie po zmianie; na postoju najdłużej jeden tick monitora (`MAX_VISIBLE_DELAY_S` ≤ 10 s),
- monitor sesji (okno Sesje) widzi zmianę po jednym ticku, a akcje użytkownika (PT-345) dalej działają na sondzie
  nowszej niż wywołanie, także gdy zmiana jest tylko w liście PID-ów.

Testy liczą odczyty i skany; nie mierzą czasu."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tide_daemon import monitor as monitor_mod
from tide_daemon import processes, windows
from tide_daemon.agents import AgentScanner
from tide_daemon.agents.timeutil import parse_ts
from tide_daemon.monitor import AgentMonitor

from .privatebus import app
from .test_codex_desktop import ASKS, DONE, NOW, at, meta, uid
from .test_monitor_probe import KONSOLE, pump

TICK_S = monitor_mod.TICK_MS / 1000.0
CLK = os.sysconf("SC_CLK_TCK")


class FakeProc:
    """Katalog w kształcie /proc: `stat` z btime 0, `<pid>/stat`, `cmdline`, `environ`, `cwd` i `fd/*` (dowiązania)."""

    def __init__(self, root):
        self.root = root
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "stat"), "w", encoding="utf-8") as fh:
            fh.write("cpu 0 0 0 0\nbtime 0\n")

    def add(self, pid, ppid, name, start=0.0, cmdline=(), env=None, cwd="", files=()):
        base = os.path.join(self.root, str(pid))
        shutil.rmtree(base, ignore_errors=True)
        os.makedirs(os.path.join(base, "fd"))
        fields = ["S", str(ppid)] + ["0"] * 17 + [str(int(round(start * CLK)))]
        with open(os.path.join(base, "stat"), "w", encoding="utf-8") as fh:
            fh.write(f"{pid} ({name}) " + " ".join(fields) + "\n")
        with open(os.path.join(base, "cmdline"), "wb") as fh:
            fh.write(b"".join(a.encode() + b"\0" for a in cmdline))
        with open(os.path.join(base, "environ"), "wb") as fh:
            fh.write(b"".join(f"{k}={v}".encode() + b"\0" for k, v in (env or {}).items()))
        if cwd:
            os.symlink(cwd, os.path.join(base, "cwd"))
        self.files(pid, files)

    def files(self, pid, files):
        fd = os.path.join(self.root, str(pid), "fd")
        for name in os.listdir(fd):
            os.unlink(os.path.join(fd, name))
        for n, path in enumerate(files, start=3):
            os.symlink(path, os.path.join(fd, str(n)))

    def remove(self, pid):
        shutil.rmtree(os.path.join(self.root, str(pid)))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ProberTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-proc-scan-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.proc = FakeProc(os.path.join(self.tmp, "proc"))
        self.logs = os.path.join(self.tmp, "logs")
        self.proc.add(1, 0, "systemd")
        self.proc.add(300, 1, "konsole", 10.0)
        self.proc.add(301, 300, "bash", 11.0)
        self.proc.add(400, 301, "codex", 20.0, cmdline=("codex",), env=KONSOLE, cwd="/srv/w",
                      files=(os.path.join(self.logs, "a.jsonl"),))
        for pid in range(500, 540):        # tło: inne procesy użytkownika
            self.proc.add(pid, 1, f"proc{pid}", 5.0)
        self.clock = Clock()
        self.scan = processes.Prober(self.proc.root, monotonic=self.clock)

    def probe(self):
        return self.scan.probe(log_roots=(self.logs,))

    def tick(self):
        self.clock.now += TICK_S
        return self.probe()

    def test_idle_twenty_probes_do_at_most_one_full_scan(self):
        first = self.probe()
        self.assertEqual(self.scan.counters["fullScans"], 1)
        reads = self.scan.counters["statReads"]
        tables = [self.tick() for _ in range(19)]
        self.assertLessEqual(self.scan.counters["fullScans"], 1, "20 przebiegów na postoju — najwyżej 1 pełny skan")
        self.assertEqual(self.scan.counters["statReads"] - reads, 19, "na postoju tylko `stat` agenta")
        self.assertTrue(all(t is first for t in tables), "bez zmian — ten sam wynik (ponowne użycie)")
        self.assertEqual(self.scan.counters["reused"], 19)
        self.assertEqual(first.get(400).open_files, (os.path.join(self.logs, "a.jsonl"),))
        self.assertEqual(first.get(400).env, KONSOLE)

    def test_baseline_function_reads_every_process_each_time(self):
        """Baza ticketu (`processes.probe` w monitorze): 20 przebiegów = 20 pełnych skanów."""
        count = len([d for d in os.listdir(self.proc.root) if d.isdigit()])
        with mock.patch.object(processes, "_read_process", wraps=processes._read_process) as reads:
            for _ in range(20):
                processes.probe(self.proc.root, (self.logs,))
        self.assertEqual(reads.call_count, 20 * count)

    def test_periodic_full_scan_catches_exec_in_place(self):
        self.probe()
        self.proc.add(301, 300, "zsh", 11.0)        # exec w tym samym procesie: lista PID-ów bez zmian
        self.assertEqual(self.tick().get(301).name, "bash")
        steps = int(processes.FULL_EVERY_S // TICK_S)
        for _ in range(steps):
            table = self.tick()
        self.assertEqual(table.get(301).name, "zsh", "pełny skan co FULL_EVERY_S")
        self.assertEqual(self.scan.counters["fullScans"], 2)

    def test_changes_of_an_agent_are_visible_in_the_first_probe_after_the_change(self):
        self.probe()
        # Nowy potomek agenta (polecenie z narzędzia).
        self.proc.add(410, 400, "bash", 30.0)
        table = self.tick()
        self.assertEqual([p.pid for p in table.children[400]], [410])
        self.assertTrue(table.started_after(400, 29.0))
        # Zakończony potomek.
        self.proc.remove(410)
        table = self.tick()
        self.assertNotIn(400, table.children)
        self.assertFalse(table.started_after(400, 29.0))
        # Agent przełącza dziennik (lista PID-ów bez zmian).
        self.proc.files(400, (os.path.join(self.logs, "b.jsonl"),))
        self.assertEqual(self.tick().get(400).open_files, (os.path.join(self.logs, "b.jsonl"),))
        # Agent kończy pracę.
        self.proc.remove(400)
        self.assertIsNone(self.tick().get(400))
        self.assertEqual(self.scan.counters["fullScans"], 1, "żadna z tych zmian nie wymagała pełnego skanu")

    def test_orphans_get_a_new_parent_and_a_forked_child_is_reread_after_exec(self):
        self.probe()
        self.proc.add(420, 301, "bash", 40.0)       # odczyt tuż po fork — przed exec
        self.assertEqual(self.tick().get(420).name, "bash")
        self.proc.add(420, 301, "codex", 40.0, cmdline=("codex",), cwd="/srv/x")
        table = self.tick()
        self.assertEqual((table.get(420).name, table.get(420).tool), ("codex", "codex"), "nowy PID czytany jeszcze raz")
        self.proc.remove(301)
        self.proc.add(400, 1, "codex", 20.0, cmdline=("codex",), env=KONSOLE, cwd="/srv/w",
                      files=(os.path.join(self.logs, "a.jsonl"),))
        self.proc.add(420, 1, "codex", 40.0, cmdline=("codex",), cwd="/srv/x")
        table = self.tick()
        self.assertEqual((table.get(400).ppid, table.get(420).ppid), (1, 1))
        self.assertEqual(self.scan.counters["fullScans"], 1)

    def test_max_delay_on_idle_is_one_tick(self):
        self.assertLessEqual(monitor_mod.MAX_VISIBLE_DELAY_S, 10.0)
        self.assertEqual(monitor_mod.MAX_VISIBLE_DELAY_S, TICK_S)
        self.probe()
        for offset in (0.1, 2.5, TICK_S - 0.1):
            changed_at = self.clock.now + offset
            pid = 600 + int(offset * 10)
            self.proc.add(pid, 400, "git", 50.0)
            table = self.tick()                      # następny tick monitora
            self.assertIsNotNone(table.get(pid))
            self.assertLessEqual(self.clock.now - changed_at, monitor_mod.MAX_VISIBLE_DELAY_S)
        self.assertEqual(self.scan.counters["fullScans"], 1)


class LateExecTest(unittest.TestCase):
    """PT-385 (N1): wrapper, który robi `exec claude` po kilku sekundach od `fork`, trafia do migawki w najbliższej
    sondzie, dopóki od startu procesu minęło mniej niż `SETTLE_S` (30 s); później — dopiero przy pełnym skanie.
    Na postoju bez nowych PID-ów sonda nie czyta ponownie żadnego procesu. Zegary: monotoniczny (pełny skan co
    FULL_EVERY_S) i ścienny (wiek procesu z `stat`, btime atrapy = 0)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-proc-late-exec-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.proc = FakeProc(os.path.join(self.tmp, "proc"))
        self.proc.add(1, 0, "systemd")
        self.proc.add(300, 1, "konsole", 10.0)
        for pid in range(500, 540):
            self.proc.add(pid, 1, f"proc{pid}", 5.0)
        self.mono, self.wall = Clock(), Clock()
        self.wall.now = 50_000.0
        self.scan = processes.Prober(self.proc.root, monotonic=self.mono, wall=self.wall)

    def tick(self):
        self.mono.now += TICK_S
        self.wall.now += TICK_S
        return self.scan.probe()

    def wrapper_with_exec_after(self, delay):
        """Pełny skan, potem `fork` wrappera (1 s przed najbliższą sondą) i `exec claude` `delay` s po starcie.
        Zwraca (tabela pierwszej sondy po `exec`, chwila startu procesu)."""
        self.scan.probe()
        start = self.wall.now + TICK_S - 1.0
        self.proc.add(700, 300, "bash", start)
        table = self.tick()
        self.assertEqual(table.get(700).name, "bash", "odczyt tuż po fork — przed exec")
        while self.wall.now + TICK_S < start + delay:
            table = self.tick()
        self.proc.add(700, 300, "claude", start, cmdline=("claude",), cwd="/srv/w")
        return self.tick(), start

    def test_exec_after_25_seconds_is_seen_in_the_next_probe(self):
        self.assertEqual(processes.SETTLE_S, 30.0)
        table, start = self.wrapper_with_exec_after(25.0)
        self.assertLessEqual(self.wall.now - start, 30.0)
        self.assertEqual((table.get(700).name, table.get(700).tool), ("claude", "claude"))
        self.assertEqual(self.scan.counters["fullScans"], 1, "bez pełnego skanu")

    def test_exec_after_35_seconds_waits_for_the_full_scan(self):
        table, start = self.wrapper_with_exec_after(35.0)
        self.assertGreater(self.wall.now - start, 35.0)
        self.assertEqual(table.get(700).name, "bash", "po 30 s od startu proces nie jest czytany co sondę")
        self.assertEqual(self.scan.counters["fullScans"], 1)
        while self.scan.counters["fullScans"] < 2:
            table = self.tick()
        self.assertEqual(table.get(700).tool, "claude", "pełny skan co FULL_EVERY_S rozpoznaje agenta")

    def test_two_execs_within_the_window_are_both_seen(self):
        self.scan.probe()
        start = self.wall.now + TICK_S - 1.0
        self.proc.add(700, 300, "bash", start)
        self.tick()
        self.proc.add(700, 300, "env", start)            # pierwszy exec (np. `env … claude`)
        self.assertEqual(self.tick().get(700).name, "env")
        self.tick()
        self.proc.add(700, 300, "claude", start, cmdline=("claude",), cwd="/srv/w")
        self.assertEqual(self.tick().get(700).tool, "claude")

    def test_young_process_from_the_full_scan_is_reread_too(self):
        self.proc.add(700, 300, "bash", self.wall.now - 2.0)   # wystartował tuż przed pierwszym (pełnym) skanem
        self.scan.probe()
        self.tick()
        self.proc.add(700, 300, "claude", self.wall.now - 2.0 - TICK_S, cmdline=("claude",), cwd="/srv/w")
        self.assertEqual(self.tick().get(700).tool, "claude")
        self.assertEqual(self.scan.counters["fullScans"], 1)

    def test_idle_without_new_pids_rereads_nothing(self):
        self.scan.probe()
        reads = self.scan.counters["statReads"]
        for _ in range(20):
            self.tick()
        self.assertEqual(self.scan.counters["statReads"] - reads, 0, "postój bez nowych PID-ów i agentów — 0 odczytów")
        # Świeży proces jest czytany tylko do końca okna; potem postój znów kosztuje 0 odczytów.
        self.proc.add(700, 300, "sleep", self.wall.now + 1.0)
        for _ in range(int(processes.SETTLE_S // TICK_S) + 2):
            self.tick()
        reads = self.scan.counters["statReads"]
        for _ in range(20):
            self.tick()
        self.assertEqual(self.scan.counters["statReads"] - reads, 0, "po oknie SETTLE_S — znów 0 ponownych odczytów")
        self.assertEqual(self.scan.counters["fullScans"], 1)


class MonitorTest(unittest.TestCase):
    """Monitor sesji z prawdziwym skanerem dzienników i przyrostową sondą na atrapie /proc."""

    def setUp(self):
        app()
        self.tmp = tempfile.mkdtemp(prefix="tide-proc-monitor-")
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
        self.proc = FakeProc(os.path.join(self.tmp, "proc"))
        self.proc.add(1, 0, "systemd")
        self.proc.add(300, 1, "konsole", parse_ts(at("10:00:00")))
        self.proc.add(301, 300, "bash", parse_ts(at("10:00:01")))
        for pid in range(500, 530):
            self.proc.add(pid, 1, f"proc{pid}", 5.0)
        scanner = AgentScanner(os.path.join(self.tmp, "brak"), os.path.join(self.tmp, "codex"))
        self.monitor = AgentMonitor(scanner, use_processes=True, clock=lambda: NOW, probe_wait=10.0)
        self.clock = Clock()
        self.monitor.scan = processes.Prober(self.proc.root, monotonic=self.clock)
        self.addCleanup(self.monitor.stop)
        scanner.discover(NOW)
        scanner.update()
        self.raised = []
        patcher = mock.patch.object(windows, "activate", side_effect=lambda t: self.raised.append(t) or True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def agent(self, log):
        self.proc.add(400, 301, "codex", parse_ts(at("10:59:00")), cmdline=("codex",), env=KONSOLE, cwd="/srv/w",
                      files=(self.paths[log],))

    def session(self, n):
        return next(s for s in json.loads(self.monitor.sessions_json()) if s["sessionId"] == uid(n))

    def tick(self):
        """Jeden tick monitora (`recompute`) i jego sonda opublikowana w migawce."""
        self.clock.now += TICK_S
        seq = self.monitor._request_probe()       # to samo co `recompute` z ticku, z numerem sondy
        self.monitor._publish()
        self.assertTrue(pump(lambda: self.monitor._last is not None and self.monitor._last.seq >= seq))

    def test_agent_changes_reach_the_sessions_snapshot_after_one_tick_without_full_scans(self):
        self.agent(6)
        self.tick()
        self.assertEqual((self.session(6)["pid"], self.session(6)["processes"]), (400, "fresh"))
        self.assertEqual(self.session(7)["pid"], 0)
        self.proc.files(400, (self.paths[7],))           # agent przeszedł do innej sesji (inny dziennik)
        self.tick()
        self.assertEqual((self.session(6)["pid"], self.session(7)["pid"]), (0, 400), "widoczne po jednym ticku")
        self.proc.add(410, 400, "bash", parse_ts(at("11:30:00")))
        self.tick()
        self.assertEqual([p.pid for p in self.monitor.procs.children[400]], [410], "nowy potomek w migawce")
        self.proc.remove(410)
        self.tick()
        self.assertNotIn(400, self.monitor.procs.children, "zakończony potomek znika z migawki")
        for _ in range(16):
            self.tick()
        self.assertEqual(self.monitor.scan.counters["fullScans"], 1, "20 ticków — jeden pełny skan (pierwszy)")

    def test_activation_uses_a_fresh_probe_that_sees_a_new_agent_without_full_scan(self):
        self.tick()
        self.assertFalse(self.session(6)["canActivate"], "w opublikowanej migawce nie ma agenta")
        self.agent(6)                                    # agent działa od teraz — zmiana tylko w liście PID-ów
        self.assertTrue(self.monitor.activate(f"codex:{uid(6)}"))
        self.assertEqual(self.raised[0]["pids"], [301, 300])
        self.assertEqual(self.monitor.scan.counters["fullScans"], 1)
        self.proc.remove(400)                            # agent zakończony — świeża sonda odmawia
        self.assertFalse(self.monitor.activate_longest_waiting())
        self.assertEqual(len(self.raised), 1)


if __name__ == "__main__":
    unittest.main()
