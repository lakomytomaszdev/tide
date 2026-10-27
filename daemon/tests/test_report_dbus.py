"""Raport poranny w całej usłudze na prywatnej magistrali: raport przy „logowaniu” (start usługi),
jedno powiadomienie na dobę z przyciskiem „Otwórz”, /Reports Generate() i Latest(), KRunner „raport”;
przeczytanie: Status(), Open() i sygnał Changed (link „Raport” w widżetach), „Otwórz” w powiadomieniu
oznacza przeczytany, nowa doba po restarcie usługi — znowu nieprzeczytany.

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

    def start_daemon(self, now="2026-10-26T06:00:00Z"):
        return self.bus.spawn_daemon(
            "--claude-dir", self.p["claude"], "--codex-dir", self.p["codex"], "--workspace", self.p["work"],
            "--no-processes", "--no-idle", "--now", now, "--report-delay", "0",
            "--report-dir", self.reports, extra_env=self.env)

    def stop_daemon(self, daemon):
        daemon.terminate()
        daemon.wait(10)
        wait_until(lambda: not self.bus.bus.interface().isServiceRegistered(SERVICE).value())

    def status(self):
        return json.loads(self.call("Status"))

    def notifies(self):
        reply = self.bus.call(NOTIFY, NOTIFY_PATH, NOTIFY, "Calls")
        return [c for c in json.loads(reply.arguments()[0]) if c["method"] == "Notify"]

    def call(self, member, *args):
        reply = self.bus.call(SERVICE, "/Reports", IFACE, member, *args, timeout=60000)
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
        self.assertTrue(wait_until(lambda: not self.status()["unread"]), "„Otwórz” w powiadomieniu → przeczytany")

        # Generate() na żądanie: ten sam raport doby, bez drugiego powiadomienia.
        self.assertEqual(self.call("Generate"), index)
        wait_until(lambda: False, timeout=0.5)
        self.assertEqual(len(self.notifies()), 1)

        with open(os.path.join(self.bus.tmp, "state", "tide", "report-state.json"), encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual((state["day"], state["notified"], state["opened"]), (reportfix.DAY, reportfix.DAY, reportfix.DAY))
        self.assertEqual(state["end"] - state["start"], 86400, "pierwszy raport: ostatnia doba")

        # Ponowne „logowanie” tego samego dnia: bez nowego raportu i bez powiadomienia.
        daemon.terminate()
        daemon.wait(10)
        wait_until(lambda: not self.bus.bus.interface().isServiceRegistered(SERVICE).value())
        self.start_daemon()
        wait_until(lambda: False, timeout=1.5)
        self.assertEqual(len(self.notifies()), 1, "jedno powiadomienie na dobę")

    def test_status_open_and_changed(self):
        from PyQt6.QtCore import QObject, pyqtSlot

        changes = []

        class Receiver(QObject):
            @pyqtSlot()
            def changed(self):
                changes.append(True)

        receiver = Receiver()
        self.assertTrue(self.bus.bus.connect(SERVICE, "/Reports", IFACE, "Changed", receiver.changed))
        daemon = self.start_daemon()
        self.assertTrue(wait_until(lambda: self.status()["path"], timeout=20), "raport przy starcie")
        self.assertTrue(wait_until(lambda: changes), "sygnał Changed po nowym raporcie")

        index = os.path.join(self.reports, reportfix.DAY, "index.html")
        status = self.status()
        self.assertEqual(status, {"path": index, "day": reportfix.DAY, "generatedAt": reportfix.NOW, "projects": 2,
                                  "waiting": 1, "unread": True, "waitingNow": 1, "finished": 0},
                         "„czeka” przeliczone na żywo z /Agents: c-ask stoi na AskUserQuestion")

        # Open() z widżetu: xdg-open przez systemd-run, przeczytany, sygnał Changed.
        changes.clear()
        self.assertIs(self.call("Open"), True)
        opened = wait_until(lambda: [c for c in calls(self.env) if "xdg-open" in c])
        self.assertTrue(opened and opened[0].startswith("systemd-run --user --scope"), calls(self.env))
        self.assertTrue(opened[0].endswith(f"xdg-open {index}"), opened[0])
        self.assertFalse(self.status()["unread"])
        self.assertTrue(wait_until(lambda: changes), "sygnał Changed po przeczytaniu")

        # Następna doba (restart usługi = logowanie): nowy raport, znowu nieprzeczytany.
        self.stop_daemon(daemon)
        self.start_daemon(now="2026-10-27T06:00:00Z")
        self.assertTrue(wait_until(lambda: self.status()["day"] == "2026-10-27", timeout=20))
        self.assertTrue(self.status()["unread"])

    def test_open_without_report_and_introspection(self):
        self.env["XDG_CONFIG_HOME"] = os.path.join(self.tmp, "config")
        os.makedirs(os.path.join(self.env["XDG_CONFIG_HOME"], "tide"), exist_ok=True)
        with open(os.path.join(self.env["XDG_CONFIG_HOME"], "tide", "report.json"), "w", encoding="utf-8") as fh:
            json.dump({"enabled": False}, fh)
        self.start_daemon()
        self.assertTrue(wait_until(lambda: self.bus.bus.interface().isServiceRegistered(SERVICE).value()))
        self.assertEqual(self.status(), {"path": "", "day": "", "generatedAt": None, "projects": None,
                                         "waiting": None, "unread": False})
        self.assertIs(self.call("Open"), False)
        self.assertEqual([c for c in calls(self.env) if "xdg-open" in c], [])
        xml = self.bus.call(SERVICE, "/Reports", "org.freedesktop.DBus.Introspectable", "Introspect").arguments()[0]
        for piece in ('<method name="Status">', '<method name="Open">', '<signal name="Changed"/>',
                      '<signal name="Ready">', '<method name="Days">', '<method name="Day">',
                      '<method name="OpenDay">', '<method name="MarkRead">'):
            self.assertIn(piece, xml)
        days = json.loads(self.call("Days"))
        self.assertEqual((days["days"], days["latest"], days["unread"], days["root"]), ([], "", False, self.reports))
        self.assertEqual(json.loads(self.call("Day", reportfix.DAY)), {"ok": False, "error": "missing",
                                                                      "day": reportfix.DAY})
        self.assertIs(self.call("OpenDay", reportfix.DAY, ""), False)
        self.assertIs(self.call("MarkRead", reportfix.DAY), False)

    def test_tide_window_days_day_open_day_and_mark_read(self):
        """Widok „Raporty” okna Tide: Days(), Day(doba), OpenDay(doba, projekt) i MarkRead(doba) —
        obejrzenie najnowszego raportu w oknie gasi kropkę (Status().unread) i wysyła Changed."""
        from PyQt6.QtCore import QObject, pyqtSlot

        changes = []

        class Receiver(QObject):
            @pyqtSlot()
            def changed(self):
                changes.append(True)

        receiver = Receiver()
        self.assertTrue(self.bus.bus.connect(SERVICE, "/Reports", IFACE, "Changed", receiver.changed))
        # Starszy raport sprzed report.json (tylko strony) — też jest na liście dni.
        old = os.path.join(self.reports, "2026-10-25")
        os.makedirs(old)
        for name in ("index.html", "alpha.html"):
            with open(os.path.join(old, name), "w", encoding="utf-8") as fh:
                fh.write("<html></html>")
        self.start_daemon()
        self.assertTrue(wait_until(lambda: self.status()["day"] == reportfix.DAY, timeout=20), "raport przy starcie")

        days = json.loads(self.call("Days"))
        self.assertEqual((days["today"], days["latest"], days["unread"], days["keepDays"]),
                         (reportfix.DAY, reportfix.DAY, True, 30))
        self.assertEqual([(d["day"], d["projects"], d["data"]) for d in days["days"]],
                         [(reportfix.DAY, 2, True), ("2026-10-25", 1, False)])
        day = json.loads(self.call("Day", reportfix.DAY))
        self.assertEqual((day["ok"], day["latest"], day["unread"], day["legacy"]), (True, True, True, False))
        self.assertEqual([p["name"] for p in day["projects"]], ["alpha", "beta"])
        self.assertIn(reportfix.INJECTION, day["projects"][0]["done"][0]["text"], "surowy tekst, nie HTML")
        self.assertTrue(json.loads(self.call("Day", "2026-10-25"))["legacy"])
        self.assertEqual(json.loads(self.call("Day", "../2026-10-25"))["ok"], False)

        # Strona starszego raportu: xdg-open, kropka zostaje.
        changes.clear()
        self.assertIs(self.call("OpenDay", "2026-10-25", "alpha"), True)
        opened = wait_until(lambda: [c for c in calls(self.env) if "xdg-open" in c])
        self.assertTrue(opened[0].endswith(f"xdg-open {os.path.join(old, 'alpha.html')}"), opened[0])
        self.assertTrue(self.status()["unread"])
        self.assertIs(self.call("OpenDay", reportfix.DAY, "../2026-10-25/alpha"), False)
        self.assertIs(self.call("MarkRead", "2026-10-25"), False, "starsza doba nie gasi kropki")
        self.assertTrue(self.status()["unread"])

        # Obejrzenie najnowszego raportu w oknie Tide: przeczytany i sygnał Changed, bez przeglądarki.
        self.assertIs(self.call("MarkRead", reportfix.DAY), True)
        self.assertFalse(self.status()["unread"])
        self.assertFalse(json.loads(self.call("Days"))["unread"])
        self.assertTrue(wait_until(lambda: changes), "sygnał Changed po przeczytaniu")
        self.assertIs(self.call("MarkRead", reportfix.DAY), False, "drugi raz — bez zmiany")
        self.assertEqual(len([c for c in calls(self.env) if "xdg-open" in c]), 1)
        self.assertIs(self.call("OpenDay", reportfix.DAY, "beta"), True)
        self.assertTrue(wait_until(lambda: any(c.endswith("beta.html") for c in calls(self.env))))

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
