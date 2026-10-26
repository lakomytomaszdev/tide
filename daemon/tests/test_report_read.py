"""Przeczytanie raportu porannego: stan w report-state.json, Status() i Open() bez D-Bus.

Przejścia: nowy raport → nieprzeczytany, otwarcie (widżet, powiadomienie, KRunner) → przeczytany,
ponowne wygenerowanie tej samej doby → dalej przeczytany, nowa doba → znowu nieprzeczytany.
ReportMonitor dostaje gotowy wynik składania (Future), atrapę otwierania i zamrożony zegar."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from concurrent.futures import Future
from types import SimpleNamespace

from . import DAEMON_DIR  # noqa: F401 — ścieżka do tide_daemon
from tide_daemon.reports import schedule
from tide_daemon.reports.schedule import State
from tide_daemon.report_monitor import ReportMonitor
from tide_daemon.krunner.runner import REPORT_MATCH, KRunnerCore


def write_report(root: str, day: str) -> str:
    os.makedirs(os.path.join(root, day), exist_ok=True)
    path = os.path.join(root, day, "index.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("<html></html>")
    return path


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-report-read-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "reports")

    def test_no_report(self):
        self.assertEqual(schedule.status(self.root, State()),
                         {"path": "", "day": "", "generatedAt": None, "projects": None, "waiting": None,
                          "unread": False})

    def test_fresh_report_is_unread_and_opening_marks_it_read(self):
        path = write_report(self.root, "2026-10-26")
        state = State(day="2026-10-26", start=10.0, end=20.0, projects=3, waiting=1)
        self.assertEqual(schedule.status(self.root, state),
                         {"path": path, "day": "2026-10-26", "generatedAt": 20.0, "projects": 3, "waiting": 1,
                          "unread": True})
        self.assertTrue(schedule.mark_opened(state, "2026-10-26"))
        self.assertFalse(schedule.mark_opened(state, "2026-10-26"), "drugi raz bez zmiany")
        self.assertFalse(schedule.status(self.root, state)["unread"])

    def test_report_without_state_uses_file_time(self):
        # Raport z `--report` nie zapisuje stanu: czas pliku, liczby nieznane, nieprzeczytany.
        path = write_report(self.root, "2026-10-26")
        os.utime(path, (1234.0, 1234.0))
        status = schedule.status(self.root, State(day="2026-10-25", end=99.0, projects=2, waiting=0,
                                                  opened="2026-10-25"))
        self.assertEqual((status["generatedAt"], status["projects"], status["waiting"], status["unread"]),
                         (1234.0, None, None, True))

    def test_state_round_trip_and_legacy_file(self):
        path = os.path.join(self.tmp, "state", "report-state.json")
        state = State(day="2026-10-26", start=1.0, end=2.0, notified="2026-10-26", opened="2026-10-25",
                      projects=4, waiting=2)
        schedule.save_state(path, state)
        self.assertEqual(schedule.load_state(path), state)
        # Plik sprzed śledzenia przeczytania: istniejący raport nie zapala kropki po aktualizacji.
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"day": "2026-10-26", "start": 1.0, "end": 2.0, "notified": "2026-10-26"}, fh)
        self.assertEqual(schedule.load_state(path).opened, "2026-10-26")
        # Złe liczby to „nieznane”, nie błąd.
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"day": "2026-10-26", "opened": "", "projects": -1, "waiting": True}, fh)
        loaded = schedule.load_state(path)
        self.assertEqual((loaded.opened, loaded.projects, loaded.waiting), ("", None, None))

    def test_day_of(self):
        self.assertEqual(schedule.day_of("/r/2026-10-26/index.html"), "2026-10-26")
        self.assertEqual(schedule.day_of("/r/notes/index.html"), "")
        self.assertEqual(schedule.day_of(""), "")


class MonitorReadTest(unittest.TestCase):
    """ReportMonitor: przejścia stanu przeczytania i sygnał changed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-report-monitor-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "reports")
        self.state_file = os.path.join(self.tmp, "state", "report-state.json")
        self.opened: list[str] = []
        self.open_ok = True
        self.now = 1793001600.0  # 2026-10-26 07:00 w Warszawie
        self.monitor = ReportMonitor(
            schedule.Settings(), root=self.root, state_file=self.state_file, palette_paths=[], workspace=self.tmp,
            claude_root=self.tmp, codex_root=self.tmp, clock=lambda: self.now, opener=self.fake_open,
            fetch_limits=False)
        self.addCleanup(self.monitor.stop)
        self.changes = 0
        self.monitor.changed.connect(self.count_change)

    def count_change(self):
        self.changes += 1

    def fake_open(self, path):
        self.opened.append(path)
        return self.open_ok

    def deliver(self, day, active=2, waiting=1, scheduled=True):
        """Kończy składanie raportu doby `day` tak, jak wątek roboczy (bez gita i dzienników)."""
        path = write_report(self.root, day)
        report = SimpleNamespace(active=[SimpleNamespace(commits=1, sessions=[]) for _ in range(active)],
                                 waiting=[object()] * waiting)
        future = Future()
        future.set_result((path, report))
        with contextlib.redirect_stdout(io.StringIO()):  # wpis do dziennika usługi
            self.monitor._deliver({"scheduled": scheduled, "day": day, "start": self.now - 86400,
                                   "end": self.now}, future)
        return path

    def test_generated_unread_then_open_read_then_new_day_unread(self):
        self.assertEqual(self.monitor.status()["path"], "")
        self.assertFalse(self.monitor.open(), "bez raportu nie ma czego otworzyć")
        self.assertEqual(self.opened, [])

        path = self.deliver("2026-10-26")
        status = self.monitor.status()
        self.assertEqual(status, {"path": path, "day": "2026-10-26", "generatedAt": self.now, "projects": 2,
                                  "waiting": 1, "unread": True})
        self.assertEqual(self.changes, 1, "nowy raport → changed")

        self.assertTrue(self.monitor.open())
        self.assertEqual(self.opened, [path])
        self.assertFalse(self.monitor.status()["unread"])
        self.assertEqual(self.changes, 2, "przeczytanie → changed")
        with open(self.state_file, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["opened"], "2026-10-26", "przeczytanie w report-state.json")

        self.assertTrue(self.monitor.open())
        self.assertEqual(self.changes, 2, "drugie otwarcie niczego nie zmienia")

        # Generate() w tej samej dobie: nowe liczby, ale dalej przeczytany.
        self.now += 3600
        self.deliver("2026-10-26", active=3, waiting=0, scheduled=False)
        status = self.monitor.status()
        self.assertEqual((status["projects"], status["waiting"], status["unread"]), (3, 0, False))

        # Następna doba: nowy raport, znowu nieprzeczytany.
        self.now += 86400
        path2 = self.deliver("2026-10-27")
        status = self.monitor.status()
        self.assertEqual((status["path"], status["day"], status["unread"]), (path2, "2026-10-27", True))

    def test_failed_open_keeps_unread(self):
        self.deliver("2026-10-26")
        self.open_ok = False
        self.assertFalse(self.monitor.open())
        self.assertTrue(self.monitor.status()["unread"])

    def test_notification_action_marks_read_only_for_latest(self):
        old = self.deliver("2026-10-25")
        new = self.deliver("2026-10-26")
        # „Otwórz” w starym powiadomieniu: otwiera stary raport, nowy zostaje nieprzeczytany.
        self.assertTrue(self.monitor.open_path(old))
        self.assertEqual(self.opened, [old])
        self.assertTrue(self.monitor.status()["unread"])
        self.assertTrue(self.monitor.open_path(new))
        self.assertFalse(self.monitor.status()["unread"])

    def test_opener_error_does_not_raise(self):
        self.deliver("2026-10-26")

        def broken(path):
            raise OSError("brak przeglądarki")
        self.monitor.opener = broken
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(self.monitor.open())
        self.assertTrue(self.monitor.status()["unread"])

    def test_krunner_opens_through_the_monitor(self):
        path = self.deliver("2026-10-26")
        launchers = SimpleNamespace(open_report=lambda p: self.fail("KRunner ma iść przez ReportMonitor"))
        core = KRunnerCore(SimpleNamespace(), lambda: [], launchers, report=self.monitor.latest,
                           open_report=self.monitor.open, home=self.tmp)
        self.assertTrue(core.run(REPORT_MATCH))
        self.assertEqual(self.opened, [path])
        self.assertFalse(self.monitor.status()["unread"])


if __name__ == "__main__":
    unittest.main()
