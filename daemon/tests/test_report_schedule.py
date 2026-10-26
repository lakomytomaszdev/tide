"""Termin raportu porannego (pierwsze logowanie w dobie albo 07:00), okres, retencja, ustawienia i stan.

Czas lokalny Europe/Warsaw ustawiany na czas testów (TZ + tzset), zegar zawsze podawany jawnie."""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import DAEMON_DIR  # noqa: F401 — ścieżka do tide_daemon
from tide_daemon.reports import schedule
from tide_daemon.reports.schedule import Settings, State

WARSAW = ZoneInfo("Europe/Warsaw")
_OLD_TZ = None


def setUpModule():
    global _OLD_TZ
    _OLD_TZ = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Warsaw"
    time.tzset()


def tearDownModule():
    if _OLD_TZ is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = _OLD_TZ
    time.tzset()


def at(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=WARSAW).timestamp()


class DueTest(unittest.TestCase):
    settings = Settings()  # 07:00, doba od 04:00

    def test_first_login_of_the_day_generates_even_before_seven(self):
        state = State(day="2026-10-25", end=at("2026-10-25T07:00"))
        self.assertTrue(schedule.due(at("2026-10-26T05:30"), state, self.settings, at_login=True))

    def test_timer_waits_for_seven_when_already_logged_in(self):
        state = State(day="2026-10-25", end=at("2026-10-25T07:00"))
        self.assertFalse(schedule.due(at("2026-10-26T06:59"), state, self.settings, at_login=False))
        self.assertTrue(schedule.due(at("2026-10-26T07:00"), state, self.settings, at_login=False))
        self.assertTrue(schedule.due(at("2026-10-26T13:10"), state, self.settings, at_login=False),
                        "wybudzenie po uśpieniu w ciągu dnia — raport doby jeszcze należny")

    def test_once_per_day(self):
        state = State(day="2026-10-26", end=at("2026-10-26T07:00"))
        for when, login in (("2026-10-26T07:01", False), ("2026-10-26T15:00", True), ("2026-10-27T03:59", True)):
            self.assertFalse(schedule.due(at(when), state, self.settings, at_login=login), when)
        self.assertTrue(schedule.due(at("2026-10-27T07:00"), state, self.settings, at_login=False))

    def test_login_after_midnight_belongs_to_previous_day(self):
        state = State(day="2026-10-26", end=at("2026-10-26T07:00"))
        self.assertEqual(schedule.report_day(at("2026-10-27T01:00"), self.settings), date(2026, 10, 26))
        self.assertFalse(schedule.due(at("2026-10-27T01:00"), state, self.settings, at_login=True))
        self.assertTrue(schedule.due(at("2026-10-27T04:00"), state, self.settings, at_login=True))

    def test_first_ever_report_and_disabled(self):
        self.assertTrue(schedule.due(at("2026-10-26T09:00"), State(), self.settings, at_login=True))
        off = Settings(enabled=False)
        self.assertFalse(schedule.due(at("2026-10-26T09:00"), State(), off, at_login=True))
        self.assertFalse(schedule.due(at("2026-10-26T09:00"), State(), off, at_login=False))

    def test_custom_time(self):
        settings = Settings(at=schedule.parse_hhmm("08:30"))
        state = State(day="2026-10-25")
        self.assertFalse(schedule.due(at("2026-10-26T08:29"), state, settings, at_login=False))
        self.assertTrue(schedule.due(at("2026-10-26T08:30"), state, settings, at_login=False))

    def test_dst_change_keeps_seven_oclock_local(self):
        # 25.10.2026 zmiana czasu z CEST na CET — 07:00 to wciąż 07:00 na zegarku.
        state = State(day="2026-10-24")
        self.assertFalse(schedule.due(at("2026-10-25T06:59"), state, self.settings, at_login=False))
        self.assertTrue(schedule.due(at("2026-10-25T07:00"), state, self.settings, at_login=False))


class PeriodTest(unittest.TestCase):
    def test_first_report_covers_last_day(self):
        now = at("2026-10-26T07:00")
        self.assertEqual(schedule.period(now, State(), "2026-10-26"), (now - 86400, now))

    def test_since_previous_report(self):
        now = at("2026-10-26T07:00")
        prev = at("2026-10-24T08:15")
        self.assertEqual(schedule.period(now, State(day="2026-10-24", start=prev - 86400, end=prev), "2026-10-26"),
                         (prev, now))

    def test_at_most_seven_days(self):
        now = at("2026-10-26T07:00")
        state = State(day="2026-09-01", end=at("2026-09-01T07:00"))
        self.assertEqual(schedule.period(now, state, "2026-10-26"), (now - 7 * 86400, now))

    def test_regenerating_same_day_keeps_start(self):
        morning = at("2026-10-26T07:00")
        state = State(day="2026-10-26", start=morning - 86400, end=morning)
        later = at("2026-10-26T15:00")
        self.assertEqual(schedule.period(later, state, "2026-10-26"), (morning - 86400, later))

    def test_notification_once_per_day(self):
        self.assertTrue(schedule.should_notify(State(notified="2026-10-25"), "2026-10-26"))
        self.assertFalse(schedule.should_notify(State(notified="2026-10-26"), "2026-10-26"))


class SettingsAndStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-report-sched-")
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, data) -> str:
        path = os.path.join(self.tmp, "report.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_defaults_without_file(self):
        settings, warnings = schedule.load_settings(os.path.join(self.tmp, "nope.json"))
        self.assertEqual((settings.at, settings.day_start, settings.keep_days, settings.enabled), (420, 240, 30, True))
        self.assertEqual(warnings, [])

    def test_file_and_cli_override(self):
        path = self.write({"at": "06:45", "dayStart": "03:00", "keepDays": 14, "enabled": True})
        settings, warnings = schedule.load_settings(path)
        self.assertEqual((settings.at, settings.day_start, settings.keep_days), (405, 180, 14))
        settings, _ = schedule.load_settings(path, at="08:00", enabled=False)
        self.assertEqual((settings.at, settings.enabled), (480, False))

    def test_bad_values_keep_defaults_with_warning(self):
        path = self.write({"at": "25:00", "keepDays": 0})
        settings, warnings = schedule.load_settings(path)
        self.assertEqual((settings.at, settings.keep_days), (420, 30))
        self.assertEqual(len(warnings), 2)
        settings, warnings = schedule.load_settings(self.write("{nie json"))
        self.assertEqual(settings.at, 420)
        self.assertEqual(len(warnings), 1)
        with self.assertRaises(ValueError):
            schedule.load_settings(None, at="7")

    def test_day_start_after_report_time_is_clamped(self):
        settings, warnings = schedule.load_settings(self.write({"at": "05:00", "dayStart": "06:00"}))
        self.assertEqual(settings.day_start, settings.at)
        self.assertEqual(len(warnings), 1)

    def test_state_round_trip_and_garbage(self):
        path = os.path.join(self.tmp, "state", "report-state.json")
        self.assertEqual(schedule.load_state(path), State())
        schedule.save_state(path, State(day="2026-10-26", start=1.5, end=2.5, notified="2026-10-26"))
        self.assertEqual(schedule.load_state(path), State(day="2026-10-26", start=1.5, end=2.5, notified="2026-10-26"))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"day": 5, "end": "x"}')
        self.assertEqual(schedule.load_state(path), State())

    def test_paths_follow_xdg(self):
        env = {"HOME": "/h", "XDG_STATE_HOME": "/s", "XDG_DATA_HOME": "/d", "XDG_CONFIG_HOME": "/c"}
        self.assertEqual(schedule.state_path(env), "/s/tide/report-state.json")
        self.assertEqual(schedule.reports_root(env), "/d/tide/reports")
        self.assertEqual(schedule.config_path(env), "/c/tide/report.json")
        self.assertEqual(schedule.reports_root({"HOME": "/h"}), "/h/.local/share/tide/reports")


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tide-report-ret-")
        self.addCleanup(shutil.rmtree, self.root)

    def day_dir(self, name: str, index=True) -> str:
        path = os.path.join(self.root, name)
        os.makedirs(path)
        if index:
            with open(os.path.join(path, "index.html"), "w", encoding="utf-8") as fh:
                fh.write("x")
        return path

    def test_keeps_thirty_days_and_only_report_dirs(self):
        for name in ("2026-09-25", "2026-09-26", "2026-10-01", "2026-10-26", "notes", "2026-13-40"):
            self.day_dir(name)
        outside = tempfile.mkdtemp(prefix="tide-report-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        os.symlink(outside, os.path.join(self.root, "2026-01-01"))
        removed = schedule.apply_retention(self.root, date(2026, 10, 26), 30)
        self.assertEqual([os.path.basename(p) for p in removed], ["2026-09-25"])
        self.assertEqual(sorted(os.listdir(self.root)),
                         ["2026-01-01", "2026-09-26", "2026-10-01", "2026-10-26", "2026-13-40", "notes"])
        self.assertTrue(os.path.isdir(outside), "dowiązanie nie jest śledzone ani kasowane")

    def test_latest_index(self):
        self.assertEqual(schedule.latest_index(self.root), "")
        self.day_dir("2026-10-24")
        self.day_dir("2026-10-26", index=False)  # przerwany zapis — bez index.html
        self.assertEqual(schedule.latest_index(self.root), os.path.join(self.root, "2026-10-24", "index.html"))


if __name__ == "__main__":
    unittest.main()
