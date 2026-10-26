"""Raport poranny w całej usłudze na prywatnej magistrali: raport przy „logowaniu” (start usługi),
jedno powiadomienie na dobę z przyciskiem „Otwórz”, /Reports Generate() i Latest(), KRunner „raport”.

Usługa dostaje zamrożony zegar (pon. 26.10.2026 07:00 w Warszawie), syntetyczny świat z
reportfix.py, tymczasowe HOME i XDG_*, atrapy programów (systemd-run, xdg-open tylko zapisują
wywołanie) i nieistniejącą konfigurację Limitów AI — nie dotyka pulpitu ani prawdziwych danych."""
import json
import os
import shutil
import sys
import tempfile
import unittest

from . import reportfix
from .fakebin import calls, make_env
from .privatebus import PrivateBus, requires_dbus, wait_until

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_notifications.py")
NOTIFY = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"
SERVICE = "org.tide.Daemon"
IFACE = "org.tide.Daemon.Reports"


@requires_dbus
class ReportDaemonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-report-dbus-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.p = reportfix.build(os.path.join(self.tmp, "world"))
        self.env = make_env(self.tmp)
        self.env.update({"TZ": reportfix.TZ, "LIMITY_CONFIG": os.path.join(self.tmp, "no-limity.json")})
        self.reports = os.path.join(self.tmp, "reports")
        self.bus = PrivateBus("tide-report-dbus-test")
        self.addCleanup(self.bus.stop)
        self.bus.spawn([sys.executable, FAKE], NOTIFY)

    def start_daemon(self):
        return self.bus.spawn_daemon(
            "--claude-dir", self.p["claude"], "--codex-dir", self.p["codex"], "--workspace", self.p["work"],
            "--no-processes", "--no-idle", "--now", "2026-10-26T06:00:00Z", "--report-delay", "0",
            "--report-dir", self.reports, extra_env=self.env)

    def notifies(self):
        reply = self.bus.call(NOTIFY, NOTIFY_PATH, NOTIFY, "Calls")
        return [c for c in json.loads(reply.arguments()[0]) if c["method"] == "Notify"]

    def call(self, member):
        reply = self.bus.call(SERVICE, "/Reports", IFACE, member, timeout=60000)
        self.assertEqual(reply.errorMessage(), "", reply.errorMessage())
        return reply.arguments()[0]

    def test_login_report_notification_open_and_once_per_day(self):
        daemon = self.start_daemon()
        sent = wait_until(self.notifies, timeout=20)
        self.assertEqual(len(sent), 1, sent)
        self.assertEqual((sent[0]["app"], sent[0]["summary"]), ("Tide", "Raport poranny gotowy — 2 projekty"))
        self.assertEqual(sent[0]["body"], "5 commitów · 4 sesje agentów · 1 czeka na ciebie")
        self.assertEqual(sent[0]["actions"], ["default", "Otwórz", "open", "Otwórz"])

        index = os.path.join(self.reports, reportfix.DAY, "index.html")
        self.assertEqual(self.call("Latest"), index)
        self.assertTrue(os.path.isfile(os.path.join(self.reports, reportfix.DAY, "alpha.html")))

        # „Otwórz” w powiadomieniu → xdg-open z raportem (przez systemd-run, atrapa tylko zapisuje).
        from PyQt6.QtCore import QMetaType
        from PyQt6.QtDBus import QDBusArgument
        self.bus.call(NOTIFY, NOTIFY_PATH, NOTIFY, "Invoke", QDBusArgument(sent[0]["id"], QMetaType.Type.UInt.value),
                      "open")
        opened = wait_until(lambda: [c for c in calls(self.env) if "xdg-open" in c])
        self.assertEqual(len(opened), 1, calls(self.env))
        self.assertTrue(opened[0].startswith("systemd-run --user --scope"), opened[0])
        self.assertTrue(opened[0].endswith(f"xdg-open {index}"), opened[0])

        # Generate() na żądanie: ten sam raport doby, bez drugiego powiadomienia.
        self.assertEqual(self.call("Generate"), index)
        wait_until(lambda: False, timeout=0.5)
        self.assertEqual(len(self.notifies()), 1)

        with open(os.path.join(self.bus.tmp, "state", "tide", "report-state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual((state["day"], state["notified"]), (reportfix.DAY, reportfix.DAY))
        self.assertEqual(state["end"] - state["start"], 86400, "pierwszy raport: ostatnia doba")

        # Ponowne „logowanie” tego samego dnia: bez nowego raportu i bez powiadomienia.
        daemon.terminate()
        daemon.wait(10)
        wait_until(lambda: not self.bus.bus.interface().isServiceRegistered(SERVICE).value())
        self.start_daemon()
        wait_until(lambda: False, timeout=1.5)
        self.assertEqual(len(self.notifies()), 1, "jedno powiadomienie na dobę")

    def test_no_report_option_still_answers_generate(self):
        self.env["XDG_CONFIG_HOME"] = os.path.join(self.tmp, "config")
        os.makedirs(os.path.join(self.env["XDG_CONFIG_HOME"], "tide"), exist_ok=True)
        with open(os.path.join(self.env["XDG_CONFIG_HOME"], "tide", "report.json"), "w", encoding="utf-8") as fh:
            json.dump({"enabled": False}, fh)
        self.start_daemon()
        wait_until(lambda: False, timeout=1.0)
        self.assertEqual(self.notifies(), [], "wyłączony termin — nic samo nie powstaje")
        self.assertEqual(self.call("Latest"), "")
        index = self.call("Generate")
        self.assertEqual(index, os.path.join(self.reports, reportfix.DAY, "index.html"))
        self.assertEqual(self.notifies(), [], "Generate() nie powiadamia")
        self.assertEqual(self.call("Latest"), index)


if __name__ == "__main__":
    unittest.main()
