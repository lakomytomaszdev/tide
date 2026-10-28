"""Kilka katalogów danych Codexa (codex_homes.py): ~/.codex (Codex Desktop) i ~/snap/codex/<rewizja>
(Codex CLI ze snapa, `current` → bieżąca rewizja), a $CODEX_HOME zastępuje oba.

Świat w katalogu tymczasowym (Homes): HOME z `.codex`, `snap/codex/33` (stara rewizja), `snap/codex/34`
i dowiązaniem `current` → `34`. Nigdy nie czytamy prawdziwych ~/.codex ani ~/snap. Sprawdzamy każdego
czytelnika: obszar Agents (także procesy i Meta+A), obserwację katalogów, KRunnera (tytuły z obu katalogów,
wznowienie właściwym programem), worktrees, raport poranny i koordynację — oraz to, że ta sama sesja
w dwóch katalogach jest jedną pozycją.
"""
import json
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from . import coordfix as F
from . import history
from .fakebin import make_env
from tide_daemon import codex_homes, windows
from tide_daemon.agents import AgentScanner, scan
from tide_daemon.agents.timeutil import parse_ts
from tide_daemon.coordination.scanner import CoordinationScanner
from tide_daemon.krunner.index import SessionIndex
from tide_daemon.krunner.runner import KRunnerCore
from tide_daemon.processes import ProcessInfo, ProcessTable
from tide_daemon.reports import sessions as report_sessions
from tide_daemon.worktrees import sessions as wt_sessions
from tide_daemon.worktrees.launchers import Launchers, codex_binaries

NOW = parse_ts("2026-10-26T12:00:00Z")


def uid(n: int) -> str:
    return f"0000000e-0000-4000-8000-{n:012d}"


def at(text: str) -> str:
    return f"2026-10-26T{text}.000Z"


def event(kind, ts, **extra):
    return {"timestamp": at(ts), "type": "event_msg", "payload": {"type": kind, **extra}}


def session(sid, cwd, prompt="Zrób to", originator="codex_exec", state="done", last="11:50:00",
            start="11:00:00", message="Gotowe."):
    """Wątek Codexa (albo jego segment od `start`): meta, kontekst tury, polecenie i tura skończona,
    trwająca albo z pytaniem."""
    records = [
        {"timestamp": at(start), "type": "session_meta",
         "payload": {"id": sid, "timestamp": at(start), "cwd": cwd, "originator": originator,
                     "source": "exec", "model_provider": "ollama", "git": {"branch": "main"}}},
        {"timestamp": at(start), "type": "turn_context",
         "payload": {"cwd": cwd, "approval_policy": "on-request", "model": "qwen3:8b"}},
        event("user_message", start, message=prompt),
    ]
    if state == "done":
        records += [event("task_started", start), event("task_complete", last, last_agent_message=message)]
    elif state == "working":
        records += [event("task_started", last)]
    else:  # pytanie do użytkownika
        records += [event("task_started", start),
                    {"timestamp": at(last), "type": "response_item",
                     "payload": {"type": "function_call", "name": "request_user_input", "call_id": "q1",
                                 "arguments": "{}"}}]
    return records


class Homes:
    """HOME z ~/.codex i snapem Codexa (rewizje 33 i 34, current → 34) w katalogu tymczasowym."""

    def __init__(self, test):
        self.tmp = tempfile.mkdtemp(prefix="tide-codex-homes-")
        test.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        self.dot = os.path.join(self.home, ".codex")
        snap = os.path.join(self.home, "snap", "codex")
        self.rev = os.path.join(snap, "34")
        self.old = os.path.join(snap, "33")
        self.current = os.path.join(snap, "current")
        for path in (self.dot, self.rev, self.old, os.path.join(snap, "common")):
            os.makedirs(path)
        os.symlink("34", self.current)
        self.claude = os.path.join(self.tmp, "claude")
        self.work = os.path.join(self.tmp, "work", "proj")
        os.makedirs(os.path.join(self.work, ".git"))
        self.paths = {}

    @property
    def roots(self) -> list[str]:
        return codex_homes.default_homes({"HOME": self.home})

    def write(self, home, sid, records, day=("2026", "10", "26"), name="") -> str:
        """Plik sesji; name — inna nazwa pliku (np. segment wątku `rollout-…-<wątek>_<segment>.jsonl`)."""
        folder = os.path.join(home, "sessions", *day)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name or f"rollout-{'-'.join(day)}T11-00-00-{sid}.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record) + "\n")
        last = parse_ts(records[-1]["timestamp"])
        os.utime(path, (last, last))
        self.paths[(home, sid)] = path
        return path

    def thread_names(self, home, names: dict) -> None:
        with open(os.path.join(home, "session_index.jsonl"), "w", encoding="utf-8") as fh:
            for tid, name in names.items():
                fh.write(json.dumps({"id": tid, "thread_name": name, "updated_at": at("11:30:00")}) + "\n")


def real(path: str) -> str:
    return os.path.realpath(path)


# --- lista katalogów ----------------------------------------------------------------------------

class DefaultHomesTest(unittest.TestCase):
    def test_codex_home_replaces_both(self):
        self.assertEqual(codex_homes.default_homes({"CODEX_HOME": "/x/codex", "HOME": "/h"}), ["/x/codex"])

    def test_default_is_dot_codex_and_snap_current(self):
        self.assertEqual(codex_homes.default_homes({"HOME": "/h"}), ["/h/.codex", "/h/snap/codex/current"])

    def test_resolve_follows_current_and_drops_duplicates(self):
        h = Homes(self)
        self.assertEqual(codex_homes.resolve([h.dot, h.current, h.rev]), [real(h.dot), real(h.rev)])
        self.assertEqual(codex_homes.resolve(h.current), [real(h.rev)])  # pojedynczy napis też
        self.assertEqual(codex_homes.sessions_dirs(h.roots),
                         [os.path.join(real(h.dot), "sessions"), os.path.join(real(h.rev), "sessions")])

    def test_resolve_follows_a_snap_refresh(self):
        h = Homes(self)
        os.remove(h.current)
        os.makedirs(os.path.join(os.path.dirname(h.current), "35"))
        os.symlink("35", h.current)
        self.assertEqual(codex_homes.resolve(h.roots)[1], real(os.path.join(os.path.dirname(h.current), "35")))

    def test_is_snap(self):
        h = Homes(self)
        self.assertTrue(codex_homes.is_snap(os.path.join(h.current, "sessions", "x.jsonl"), h.home))
        self.assertTrue(codex_homes.is_snap(os.path.join(h.rev, "sessions", "x.jsonl"), h.home))
        self.assertFalse(codex_homes.is_snap(os.path.join(h.dot, "sessions", "x.jsonl"), h.home))
        self.assertFalse(codex_homes.is_snap("", h.home))
        # Tylko snap Codexa w katalogu domowym — nie dowolny katalog z „snap/codex” w ścieżce.
        self.assertFalse(codex_homes.is_snap("/data/snap/codex/x/sessions/a.jsonl", h.home))
        self.assertFalse(codex_homes.is_snap(os.path.join(h.tmp, "other", "snap", "codex", "34", "a.jsonl"), h.home))
        with mock.patch.dict(os.environ, {"HOME": h.home}):
            self.assertTrue(codex_homes.is_snap(os.path.join(h.rev, "sessions", "x.jsonl")))

    def test_newest_keeps_first_position_and_latest_item(self):
        items = [("a", 1), ("b", 5), ("a", 3), ("a", 2)]
        self.assertEqual(codex_homes.newest(items, key=lambda i: i[0], stamp=lambda i: i[1]), [("a", 3), ("b", 5)])

    def test_daemon_arguments(self):
        from tide_daemon.app import parse_args
        with mock.patch.dict(os.environ, {"HOME": "/h"}):
            os.environ.pop("CODEX_HOME", None)
            self.assertEqual(parse_args([]).codex_dir, ["/h/.codex", "/h/snap/codex/current"])
        with mock.patch.dict(os.environ, {"HOME": "/h", "CODEX_HOME": "/c"}):
            self.assertEqual(parse_args([]).codex_dir, ["/c"])
            self.assertEqual(parse_args(["--codex-dir", "/a", "--codex-dir", "/b"]).codex_dir, ["/a", "/b"])


# --- obszar Agents ------------------------------------------------------------------------------

class AgentsTest(unittest.TestCase):
    def setUp(self):
        self.h = Homes(self)

    def ids(self, sessions):
        return {s["id"]: s for s in sessions}

    def test_sessions_from_both_homes_but_not_old_revisions(self):
        h = self.h
        h.write(h.dot, uid(1), session(uid(1), h.work, originator="Codex Desktop"))
        h.write(h.current, uid(2), session(uid(2), h.work, state="working", last="11:59:00"))
        h.write(h.old, uid(3), session(uid(3), h.work))  # kopia sprzed odświeżenia snapa — nie czytamy
        sessions = self.ids(scan(h.claude, h.roots, NOW))
        self.assertEqual(set(sessions), {f"codex:{uid(1)}", f"codex:{uid(2)}"})
        self.assertEqual(sessions[f"codex:{uid(2)}"]["state"], "working")
        self.assertEqual(sessions[f"codex:{uid(2)}"]["project"], "proj")

    def test_same_session_in_two_homes_is_one_entry_with_newer_activity(self):
        h = self.h
        h.write(h.dot, uid(4), session(uid(4), h.work, last="11:10:00"))
        h.write(h.current, uid(4), session(uid(4), h.work, state="working", last="11:58:00"))
        sessions = [s for s in scan(h.claude, h.roots, NOW) if s["sessionId"] == uid(4)]
        self.assertEqual(len(sessions), 1)
        self.assertEqual((sessions[0]["state"], sessions[0]["lastActivity"]), ("working", "2026-10-26T11:58:00Z"))

    def test_log_roots_and_watches_are_resolved_snap_revision(self):
        h = self.h
        h.write(h.current, uid(5), session(uid(5), h.work))
        scanner = AgentScanner(h.claude, h.roots)
        scanner.discover(NOW)
        snap_sessions = os.path.join(real(h.rev), "sessions")
        self.assertEqual(scanner.codex_log_roots(), (os.path.join(real(h.dot), "sessions"), snap_sessions))
        self.assertEqual(list(scanner.logs), [os.path.join(snap_sessions, "2026", "10", "26",
                                                           os.path.basename(h.paths[(h.current, uid(5))]))])
        watched = scanner.watched_dirs()
        for part in ("", "2026", "2026/10", "2026/10/26"):
            self.assertIn(os.path.join(snap_sessions, part).rstrip("/"), watched)

    def table(self, path):
        """Konsole → bash → codex ze snapa, który trzyma otwarty dziennik (ścieżka z /proc: bez `current`)."""
        konsole = {"KONSOLE_DBUS_SERVICE": ":1.9", "KONSOLE_DBUS_WINDOW": "/Windows/1",
                   "KONSOLE_DBUS_SESSION": "/Sessions/2"}
        return ProcessTable([
            ProcessInfo(1, 0, "systemd"),
            ProcessInfo(300, 1, "konsole", parse_ts(at("10:00:00"))),
            ProcessInfo(301, 300, "bash", parse_ts(at("10:00:01"))),
            ProcessInfo(400, 301, "codex", parse_ts(at("10:59:00")), cwd=self.h.work, tool="codex", env=konsole,
                        open_files=(path,)),
        ])

    def test_snap_cli_session_is_bound_to_its_process_and_meta_a_jumps_to_it(self):
        h = self.h
        h.write(h.current, uid(6), session(uid(6), h.work, state="asks", last="11:40:00"))
        opened = os.path.join(real(h.rev), "sessions", "2026", "10", "26",
                              os.path.basename(h.paths[(h.current, uid(6))]))
        procs = self.table(opened)
        s = self.ids(scan(h.claude, h.roots, NOW, procs))[f"codex:{uid(6)}"]
        self.assertEqual((s["state"], s["reason"], s["pid"]), ("waiting", "question", 400))
        self.assertTrue(s["canActivate"])
        self.assertEqual((s["terminal"], s["activation"]), ("Konsole", "window"))

        from PyQt6.QtCore import QCoreApplication
        from tide_daemon.monitor import AgentMonitor
        QCoreApplication.instance() or QCoreApplication([])
        scanner = AgentScanner(h.claude, h.roots)
        scanner.discover(NOW)
        scanner.update()
        monitor = AgentMonitor(scanner, use_processes=True, clock=lambda: NOW)
        monitor._probe = lambda: procs
        raised = []
        with mock.patch.object(windows, "activate", side_effect=lambda t: raised.append(t) or True):
            self.assertTrue(monitor.activate_longest_waiting())
        self.assertEqual(raised[0]["pids"], [301, 300])
        self.assertEqual(raised[0]["konsole"]["session"], "/Sessions/2")


class WatchTest(unittest.TestCase):
    """Nowy plik sesji w katalogu snapa widać tak szybko jak w ~/.codex — zdarzenie katalogu, nie przegląd co 30 s."""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtCore import QCoreApplication
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_new_files_in_both_homes_are_noticed_by_the_watcher(self):
        from tide_daemon.monitor import AgentMonitor
        from .privatebus import wait_until
        h = Homes(self)
        h.write(h.dot, uid(10), session(uid(10), h.work))
        h.write(h.current, uid(11), session(uid(11), h.work))
        monitor = AgentMonitor(AgentScanner(h.claude, h.roots), use_processes=False, clock=lambda: NOW)
        monitor._rescan.setInterval(10 ** 7)  # bez pełnego przeglądu — tylko zdarzenia plików
        monitor.start()
        self.addCleanup(lambda m=monitor: (m._tick.stop(), m._rescan.stop(), m.watcher.removePaths(
            m.watcher.files() + m.watcher.directories())))

        def ids():
            return {s["sessionId"] for s in monitor.sessions}
        self.assertEqual(ids(), {uid(10), uid(11)})
        h.write(h.current, uid(12), session(uid(12), h.work, state="working", last="11:59:00"))
        self.assertTrue(wait_until(lambda: uid(12) in ids(), timeout=3), "nowy plik w snapie")
        h.write(h.dot, uid(13), session(uid(13), h.work, state="working", last="11:59:00"))
        self.assertTrue(wait_until(lambda: uid(13) in ids(), timeout=3), "nowy plik w ~/.codex")
        # Dopisany wpis w pliku ze snapa (obserwacja pliku) zmienia stan.
        with open(h.paths[(h.current, uid(11))], "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event("task_started", "11:59:30")) + "\n")
        self.assertTrue(wait_until(lambda: next(s for s in monitor.sessions if s["sessionId"] == uid(11))["state"]
                                   == "working", timeout=3))


    def test_watches_move_to_the_new_revision_after_a_snap_refresh(self):
        from tide_daemon.monitor import AgentMonitor
        from .privatebus import wait_until
        h = Homes(self)
        h.write(h.current, uid(14), session(uid(14), h.work))
        monitor = AgentMonitor(AgentScanner(h.claude, h.roots), use_processes=False, clock=lambda: NOW)
        monitor._rescan.setInterval(10 ** 7)
        monitor.start()
        self.addCleanup(lambda m=monitor: (m._tick.stop(), m._rescan.stop(), m.watcher.removePaths(
            m.watcher.files() + m.watcher.directories())))
        old = os.path.join(real(h.rev), "sessions")
        self.assertIn(os.path.join(old, "2026", "10", "26"), monitor.watcher.directories())

        # Odświeżenie snapa: snapd kopiuje dane do rewizji 35 i przestawia current.
        new_rev = os.path.join(os.path.dirname(h.current), "35")
        shutil.copytree(h.rev, new_rev, symlinks=True)
        os.remove(h.current)
        os.symlink("35", h.current)
        monitor.rescan()
        new = os.path.join(real(new_rev), "sessions")
        watched = monitor.watcher.directories() + monitor.watcher.files()
        self.assertIn(os.path.join(new, "2026", "10", "26"), watched)
        self.assertFalse([p for p in watched if p.startswith(old)], "stara rewizja nie jest już obserwowana")
        self.assertEqual({s["sessionId"] for s in monitor.sessions}, {uid(14)}, "kopia w 35 to ta sama sesja")
        h.write(h.current, uid(15), session(uid(15), h.work, state="working", last="11:59:00"))
        self.assertTrue(wait_until(lambda: uid(15) in {s["sessionId"] for s in monitor.sessions}, timeout=3))


# --- segmenty jednego wątku ----------------------------------------------------------------------

class SegmentsTest(unittest.TestCase):
    """Codex Desktop dzieli długi wątek na pliki `rollout-…-<wątek>.jsonl` i `rollout-…-<wątek>_<segment>.jsonl`
    (to samo id, kolejne przedziały czasu). Raport i worktrees widzą segmenty jak dotąd (osobno), karty
    Agents i KRunner — jako jedną pozycję (najnowszy segment). Kopia w drugim katalogu danych to ten sam plik
    (id i ścieżka względem sessions/) i nie dokłada pozycji."""

    SEG = "0000000e-0000-4000-8000-0000000000ff"

    def setUp(self):
        h = self.h = Homes(self)
        self.other = os.path.join(h.tmp, "work", "other")
        os.makedirs(os.path.join(self.other, ".git"))
        sid = self.sid = uid(50)
        h.write(h.dot, sid, session(sid, h.work, prompt="Pierwszy segment", start="10:00:00", last="10:30:00",
                                    message="Wynik pierwszego", originator="Codex Desktop"),
                name=f"rollout-2026-10-26T10-00-00-{sid}.jsonl")
        h.write(h.dot, sid, session(sid, self.other, prompt="Drugi segment", start="11:00:00", last="11:30:00",
                                    message="Wynik drugiego", originator="Codex Desktop"),
                name=f"rollout-2026-10-26T11-00-00-{sid}_{self.SEG}.jsonl")
        # Kopia pierwszego segmentu w snapie (np. przeniesiona ręcznie) — ten sam plik, nie trzeci segment.
        h.write(h.current, sid, session(sid, h.work, prompt="Pierwszy segment", start="10:00:00", last="10:20:00",
                                        message="Starsza kopia", originator="Codex Desktop"),
                name=f"rollout-2026-10-26T10-00-00-{sid}.jsonl")

    def test_report_keeps_segments_apart(self):
        found = [r for r in report_sessions.collect(self.h.claude, self.h.roots, NOW - 86400, NOW)
                 if r.session_id == self.sid]
        self.assertEqual(sorted((r.prompt, r.final_message) for r in found),
                         [("Drugi segment", "Wynik drugiego"), ("Pierwszy segment", "Wynik pierwszego")])
        self.assertFalse(any(codex_homes.is_snap(r.path, self.h.home) for r in found), "nowsza kopia z ~/.codex")

    def test_worktrees_link_every_segment_directory(self):
        index = wt_sessions.SessionIndex(self.h.claude, self.h.roots)
        found = [s for s in index.refresh(NOW) if s.session_id == self.sid]
        self.assertEqual(sorted(s.cwds for s in found), sorted([(self.h.work,), (self.other,)]))
        linked = index.link([(self.h.work, real(self.h.work)), (self.other, real(self.other))])
        self.assertEqual((linked[self.h.work].session_id, linked[self.other].session_id), (self.sid, self.sid))
        self.assertEqual(linked[self.h.work].last_ts, parse_ts(at("10:30:00")))

    def test_agents_card_is_one_entry_from_the_newest_segment(self):
        found = [s for s in scan(self.h.claude, self.h.roots, NOW) if s["sessionId"] == self.sid]
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["cwd"], found[0]["lastActivity"]), (self.other, "2026-10-26T11:30:00Z"))

    def test_krunner_is_one_match_from_the_newest_segment(self):
        index = SessionIndex(self.h.claude, self.h.roots, clock=lambda: NOW)
        index.refresh()
        found = [e for e in index.entries() if e["sessionId"] == self.sid]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["cwd"], self.other)
        self.assertTrue(found[0]["source"].endswith(f"_{self.SEG}.jsonl"))


# --- KRunner ------------------------------------------------------------------------------------

class RecordingLaunchers(Launchers):
    def __init__(self, env):
        super().__init__(env=env, path=env["PATH"])
        self.spawned = []

    def spawn(self, argv, path):
        self.spawned.append((argv, path))
        return bool(argv)


def fake_program(folder: str, name: str = "codex") -> str:
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\nexit 0\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


class KRunnerTest(unittest.TestCase):
    def setUp(self):
        h = self.h = Homes(self)
        h.write(h.dot, uid(20), session(uid(20), h.work, prompt="Desktop: przegląd", originator="Codex Desktop",
                                        last="10:00:00"))
        h.write(h.current, uid(21), session(uid(21), h.work, prompt="Snap: lokalny model", last="11:40:00"))
        h.write(h.current, uid(22), session(uid(22), h.work, prompt="Snap: drugi wątek", last="11:30:00"))
        h.write(h.old, uid(23), session(uid(23), h.work, prompt="Stara rewizja"))
        h.thread_names(h.dot, {uid(20): "Przegląd z aplikacji"})
        h.thread_names(h.current, {uid(21): "Nazwa ze snapa"})
        history.add_state_db(h.rev, {uid(22): "Tytuł z bazy snapa"})
        self.index = SessionIndex(h.claude, h.roots, clock=lambda: NOW)

    def entries(self):
        return {e["sessionId"]: e for e in self.index.entries()}

    def test_index_has_sessions_and_titles_from_both_homes(self):
        self.index.refresh()
        e = self.entries()
        self.assertEqual(set(e), {uid(20), uid(21), uid(22)})
        self.assertEqual(e[uid(20)]["title"], "Przegląd z aplikacji")
        self.assertEqual(e[uid(21)]["title"], "Nazwa ze snapa")
        self.assertEqual(e[uid(22)]["title"], "Tytuł z bazy snapa")
        self.assertTrue(e[uid(20)]["desktop"])
        self.assertFalse(e[uid(21)]["desktop"])
        self.assertTrue(e[uid(21)]["source"].startswith(os.path.join(real(self.h.rev), "sessions")))

    def test_same_session_in_both_homes_is_one_match(self):
        h = self.h
        h.write(h.dot, uid(21), session(uid(21), h.work, prompt="Kopia", last="11:20:00"))
        self.index.refresh()
        ids = [e["sessionId"] for e in self.index.entries()]
        self.assertEqual(ids.count(uid(21)), 1)
        self.assertTrue(codex_homes.is_snap(self.entries()[uid(21)]["source"], self.h.home), "nowsza kopia ze snapa")

    def test_new_thread_name_in_snap_home_is_read_incrementally(self):
        self.index.refresh()
        with open(os.path.join(self.h.current, "session_index.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": uid(22), "thread_name": "Nowa nazwa"}) + "\n")
        self.index.refresh()
        self.assertEqual(self.entries()[uid(22)]["title"], "Nowa nazwa")

    def test_thread_name_readers_follow_the_current_revision(self):
        self.index.refresh()
        self.assertEqual(set(self.index._thread_names), {real(self.h.dot), real(self.h.rev)})
        new_rev = os.path.join(os.path.dirname(self.h.current), "35")
        shutil.copytree(self.h.rev, new_rev, symlinks=True)
        os.remove(self.h.current)
        os.symlink("35", self.h.current)
        self.index.refresh()
        self.assertEqual(set(self.index._thread_names), {real(self.h.dot), real(new_rev)})
        self.assertEqual(self.entries()[uid(21)]["title"], "Nazwa ze snapa")

    def core(self, path_dirs):
        env = make_env(self.h.tmp)
        env["PATH"] = os.pathsep.join(path_dirs + ["/usr/bin", "/bin"])
        launchers = RecordingLaunchers(env)
        core = KRunnerCore(self.index, lambda: [], launchers, clock=lambda: NOW, home=self.h.home)
        return core, launchers

    def test_resume_runs_the_codex_that_owns_the_session_home(self):
        self.index.refresh()
        fake = os.path.join(self.h.tmp, "fakebin")
        snap_bin = fake_program(os.path.join(self.h.tmp, "root", "snap", "bin"))
        core, launchers = self.core([fake, os.path.dirname(snap_bin)])
        self.assertTrue(core.run(f"session:codex:{uid(21)}"))
        self.assertEqual(launchers.spawned[-1][0][-3:], [snap_bin, "resume", uid(21)])
        self.assertEqual(launchers.spawned[-1][1], self.h.work)
        # Wątek z ~/.codex (bez Codex Desktop w teście) — Codex spoza snapa.
        self.assertTrue(core.run(f"session:codex:{uid(20)}"))
        self.assertEqual(launchers.spawned[-1][0][-3:], [os.path.join(fake, "codex"), "resume", uid(20)])

    def test_without_matching_program_resume_uses_codex_from_path(self):
        self.index.refresh()
        snap_bin = fake_program(os.path.join(self.h.tmp, "root", "snap", "bin"))
        core, launchers = self.core([os.path.dirname(snap_bin)])
        self.assertTrue(core.run(f"session:codex:{uid(20)}"))
        self.assertEqual(launchers.spawned[-1][0][-3:], [snap_bin, "resume", uid(20)])


class LaunchersTest(unittest.TestCase):
    def test_codex_binaries_split_snap_and_other(self):
        tmp = tempfile.mkdtemp(prefix="tide-codex-bins-")
        self.addCleanup(shutil.rmtree, tmp, True)
        snap = fake_program(os.path.join(tmp, "snap", "bin"))
        npm = fake_program(os.path.join(tmp, "npm", "bin"))
        local = fake_program(os.path.join(tmp, "home", ".local", "bin"))
        path = os.pathsep.join([os.path.dirname(snap), os.path.dirname(npm)])
        self.assertEqual(codex_binaries(path, os.path.join(tmp, "home")), {"snap": snap, "other": npm})
        self.assertEqual(codex_binaries(os.path.dirname(snap), os.path.join(tmp, "home")),
                         {"snap": snap, "other": local})
        none = os.path.join(tmp, "nowhere", "codex")
        self.assertEqual(codex_binaries("", os.path.join(tmp, "nohome"), snap_fallback=none), {"snap": "", "other": ""})
        # Bez /snap/bin w PATH usługi — zapasowo stała ścieżka snapa (jak kandydaci Ridera).
        self.assertEqual(codex_binaries(os.path.dirname(npm), os.path.join(tmp, "nohome"), snap_fallback=snap),
                         {"snap": snap, "other": npm})

    def test_resume_command_with_source(self):
        tmp = tempfile.mkdtemp(prefix="tide-codex-resume-")
        self.addCleanup(shutil.rmtree, tmp, True)
        env = make_env(tmp)
        snap = fake_program(os.path.join(tmp, "snap", "bin"))
        env["PATH"] = os.pathsep.join([os.path.join(tmp, "fakebin"), os.path.dirname(snap)])
        launchers = Launchers(env=env, path=env["PATH"])
        snap_log = os.path.join(tmp, "home", "snap", "codex", "34", "sessions", "x.jsonl")
        dot_log = os.path.join(tmp, "home", ".codex", "sessions", "x.jsonl")
        self.assertEqual(launchers.resume_command("codex", "id", tmp, source=snap_log)[-3:], [snap, "resume", "id"])
        fake_codex = os.path.join(tmp, "fakebin", "codex")
        self.assertEqual(launchers.resume_command("codex", "id", tmp, source=dot_log)[-3:], [fake_codex, "resume", "id"])
        self.assertEqual(launchers.resume_command("codex", "id", tmp)[-3:], [fake_codex, "resume", "id"])


class ReportResumeTest(unittest.TestCase):
    def test_resume_command_runs_the_snap_codex_for_snap_sessions(self):
        from types import SimpleNamespace
        from tide_daemon.reports import render
        h = Homes(self)
        snap = SimpleNamespace(tool="codex", cwd="/w/a b", session_id="id-1",
                               path=os.path.join(h.rev, "sessions", "2026", "10", "26", "r-id-1.jsonl"))
        dot = SimpleNamespace(tool="codex", cwd="/w", session_id="id-2",
                              path=os.path.join(h.dot, "sessions", "2026", "10", "26", "r-id-2.jsonl"))
        claude = SimpleNamespace(tool="claude", cwd="/w", session_id="c-1", path="/x.jsonl")
        with mock.patch.dict(os.environ, {"HOME": h.home}):
            self.assertEqual(render.resume_command(snap), "cd '/w/a b' && /snap/bin/codex resume id-1")
            self.assertEqual(render.resume_command(dot), "cd /w && codex resume id-2")
            self.assertEqual(render.resume_command(claude), "cd /w && claude --resume c-1")


# --- worktrees, raport, koordynacja --------------------------------------------------------------

class WorktreeSessionsTest(unittest.TestCase):
    def test_both_homes_names_and_dedup(self):
        h = Homes(self)
        h.write(h.dot, uid(30), session(uid(30), h.work, last="10:00:00"))
        h.write(h.current, uid(31), session(uid(31), h.work, last="11:40:00"))
        h.write(h.dot, uid(31), session(uid(31), h.work, last="11:20:00"))  # starsza kopia
        h.thread_names(h.current, {uid(31): "Ze snapa"})
        index = wt_sessions.SessionIndex(h.claude, h.roots)
        found = index.refresh(NOW)
        by_id = {}
        for s in found:
            by_id.setdefault(s.session_id, []).append(s)
        self.assertEqual(set(by_id), {uid(30), uid(31)})
        self.assertEqual(len(by_id[uid(31)]), 1)
        self.assertEqual((by_id[uid(31)][0].title, by_id[uid(31)][0].last_ts),
                         ("Ze snapa", parse_ts(at("11:40:00"))))
        linked = index.link([(h.work, real(h.work))])
        self.assertEqual(linked[h.work].session_id, uid(31))


class ReportSessionsTest(unittest.TestCase):
    def test_report_lists_sessions_from_both_homes_once(self):
        h = Homes(self)
        h.write(h.dot, uid(40), session(uid(40), h.work, prompt="Z aplikacji", last="10:00:00"))
        h.write(h.current, uid(41), session(uid(41), h.work, prompt="Z terminala", last="11:40:00"))
        h.write(h.dot, uid(41), session(uid(41), h.work, prompt="Kopia", last="11:20:00"))
        h.write(h.old, uid(42), session(uid(42), h.work))
        h.thread_names(h.current, {uid(41): "Test modelu lokalnego"})
        found = report_sessions.collect(h.claude, h.roots, NOW - 86400, NOW)
        ids = [r.session_id for r in found]
        self.assertEqual(sorted(ids), [uid(40), uid(41)])
        snap = next(r for r in found if r.session_id == uid(41))
        self.assertTrue(codex_homes.is_snap(snap.path, h.home), "nowsza kopia ze snapa")
        self.assertEqual((snap.prompt, snap.last_ts), ("Z terminala", parse_ts(at("11:40:00"))))
        self.assertEqual(report_sessions.codex_titles(h.roots)[uid(41)], "Test modelu lokalnego")


class CoordinationTest(unittest.TestCase):
    """Koordynator Codexa ze snapa, podagent w drugim katalogu, kopia wątku w ~/.codex."""

    def test_coordinator_across_homes(self):
        h = Homes(self)
        world = F.build_world(os.path.join(h.tmp, "world"))
        os.rmdir(h.rev)
        shutil.move(world["codex"], h.rev)
        day = ("sessions", "2026", "10", "26")
        impl = next(n for n in os.listdir(os.path.join(h.rev, *day)) if n.endswith(F.CODEX_IMPL + ".jsonl"))
        os.makedirs(os.path.join(h.dot, *day))
        src = os.path.join(h.rev, *day, impl)
        st = os.stat(src)
        shutil.move(src, os.path.join(h.dot, *day, impl))
        os.utime(os.path.join(h.dot, *day, impl), (st.st_atime, st.st_mtime))
        # Starsza kopia wątku koordynatora w ~/.codex — ten sam wątek, wygrywa nowszy plik.
        root = next(n for n in os.listdir(os.path.join(h.rev, *day)) if n.endswith(F.CODEX_ROOT + ".jsonl"))
        copy = os.path.join(h.dot, *day, root)
        shutil.copy(os.path.join(h.rev, *day, root), copy)
        os.utime(copy, (F.NOW - 3 * 86400, F.NOW - 3 * 86400))

        scanner = CoordinationScanner(world["claude"], h.roots, clock=lambda: F.NOW)
        coords = scanner.read()
        coord = coords["codex:" + F.CODEX_ROOT]
        self.assertEqual(set(coord.runs), {F.CODEX_IMPL, F.CODEX_REVIEW, F.CODEX_PROBE})
        self.assertTrue(codex_homes.is_snap(coord.path, h.home))
        self.assertEqual(sum(1 for key in coords if key == "codex:" + F.CODEX_ROOT), 1)

        # „Przejdź” do zakończonego koordynatora ze snapa wznawia go Codexem ze snapa.
        from PyQt6.QtCore import QCoreApplication
        from tide_daemon.coordination_monitor import CoordinationMonitor
        QCoreApplication.instance() or QCoreApplication([])
        env = make_env(h.tmp)
        snap_bin = fake_program(os.path.join(h.tmp, "root", "snap", "bin"))
        env["PATH"] = os.pathsep.join([os.path.join(h.tmp, "fakebin"), os.path.dirname(snap_bin), "/usr/bin", "/bin"])
        launchers = RecordingLaunchers(env)
        monitor = CoordinationMonitor(scanner, launchers=launchers, clock=lambda: F.NOW)
        self.addCleanup(monitor.stop)
        monitor._publish(scanner.refresh([]))
        self.assertTrue(monitor.activate("codex:" + F.CODEX_ROOT))
        self.assertEqual(launchers.spawned[-1][0][-3:], [snap_bin, "resume", F.CODEX_ROOT])


if __name__ == "__main__":
    unittest.main()
