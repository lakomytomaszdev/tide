"""Obiekt /Limits usługi na prywatnej magistrali: atrapa API QuotaApp (HTTP w wątku testu)
podaje kolejne odczyty, atrapa serwera powiadomień zapisuje Notify.

Sprawdzamy kontrakt (Forecast() → JSON, sygnał Changed), powiadomienia (raz na okno, także po
restarcie usługi), zapis historii w $XDG_STATE_HOME i to, że token nie trafia do żadnego pliku.
Usługa ma zamrożony zegar (--now) i strefę Europe/Warsaw, więc prognoza jest powtarzalna.
"""
import json
import os
import secrets
import shutil
import sys
import tempfile
import unittest

from .privatebus import SERVICE, PrivateBus, requires_dbus, wait_until
from .test_limits_core import FakeQuotaPeek, MIN, T0, at, fc, series, start_fake, status_at, steady, write_config

LIMITS_PATH = "/Limits"
LIMITS_IFACE = "org.tide.Daemon.Limits"
NOTIFY_SERVICE = "org.freedesktop.Notifications"
NOTIFY_PATH = "/org/freedesktop/Notifications"
FAKE_NOTIFY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_notifications.py")
SESSION_VALUES = steady(50, 20, 90)              # 14:00–15:30, 50 → 80% zużycia sesji Claude
NOW = "2026-10-26T14:31:00Z"                     # 15:31 w Warszawie, minutę po ostatnim odczycie


class GatedQuotaPeek(FakeQuotaPeek):
    """Do otwarcia bramki oddaje stan „sprawdzanie” (bez odczytów), potem kolejne odczyty."""

    def __init__(self, token, statuses):
        super().__init__(token, statuses)
        self.open = False

    def next_status(self):
        if not self.open:
            return {"claude": {"session": {}, "weekly": {}, "lastRead": None, "state": "checking"}}
        return super().next_status()


def readings_series():
    return [status_at(T0 + i * 5 * MIN, used, 20 + i // 6, gpt_weekly=9) for i, used in enumerate(SESSION_VALUES)]


@requires_dbus
class LimitsDBusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-limits-dbus-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.empty = os.path.join(self.tmp, "empty")
        os.makedirs(self.empty)
        self.state = os.path.join(self.tmp, "state")
        self.token = "test-" + secrets.token_hex(12)
        self.bus = PrivateBus("tide-limits-test")
        self.addCleanup(self.bus.stop)

    def spawn(self, config_path, *args):
        return self.bus.spawn_daemon(
            "--claude-dir", self.empty, "--codex-dir", self.empty, "--workspace", self.empty, "--no-processes",
            "--no-krunner", "--no-idle", "--now", NOW, "--limits-poll", "0.15", *args,
            extra_env={"LIMITY_CONFIG": config_path, "XDG_STATE_HOME": self.state, "TZ": "Europe/Warsaw"})

    def forecast(self):
        reply = self.bus.call(SERVICE, LIMITS_PATH, LIMITS_IFACE, "Forecast")
        return json.loads(reply.arguments()[0]) if reply.arguments() else None

    def notifies(self):
        reply = self.bus.call(NOTIFY_SERVICE, NOTIFY_PATH, NOTIFY_SERVICE, "Calls")
        return [c for c in json.loads(reply.arguments()[0]) if c["method"] == "Notify"]

    def files_with_token(self):
        found = []
        for dirpath, _dirs, names in os.walk(self.state):
            for name in names:
                with open(os.path.join(dirpath, name), "rb") as fh:
                    if self.token.encode() in fh.read():
                        found.append(name)
        return found

    def test_forecast_signal_alerts_history_and_restart(self):
        from PyQt6.QtCore import QObject, pyqtSlot

        server = start_fake(self, self.token, readings_series(), GatedQuotaPeek)
        config_path = write_config(os.path.join(self.tmp, "limity"), self.token, server.server_address[1])
        self.bus.spawn([sys.executable, FAKE_NOTIFY], NOTIFY_SERVICE)
        proc = self.spawn(config_path)

        # Kontrakt przed pierwszymi odczytami: usługa odpowiada, prognoz jeszcze nie ma.
        first = wait_until(lambda: (self.forecast() or {}).get("available") and self.forecast())
        self.assertEqual(first["services"], {})
        self.assertEqual(first["error"], "")

        changes = []

        class Receiver(QObject):
            @pyqtSlot()
            def changed(self):
                changes.append(True)

        receiver = Receiver()
        self.assertTrue(self.bus.bus.connect(SERVICE, LIMITS_PATH, LIMITS_IFACE, "Changed", receiver.changed))
        server.open = True

        done = wait_until(lambda: len(server.statuses) == 1 and
                          (self.forecast()["services"].get("claude", {}).get("session", {}).get("used") == 80),
                          timeout=15)
        self.assertTrue(done, server.statuses[:1])
        self.assertTrue(wait_until(lambda: changes), "sygnał Changed po nowych odczytach")

        result = self.forecast()
        session = result["services"]["claude"]["session"]
        expected = fc(series(SESSION_VALUES), now=at(26, 15, 31))
        self.assertEqual(session["text"], "wyczerpie się ok. 16:30")
        self.assertEqual(session["short"], "koniec ok. 16:30")
        for key in ("state", "used", "remaining", "resetTs", "exhaustTs", "marginS", "whenText", "windowStartTs"):
            self.assertEqual(session[key], expected[key], key)
        self.assertEqual(result["services"]["claude"]["weekly"]["state"], "unknown")  # 90 min to za mało na tydzień
        self.assertEqual(result["services"]["chatgpt"]["weekly"]["used"], 9)
        self.assertNotIn("session", result["services"]["chatgpt"])

        # Powiadomienia: wyczerpanie przed resetem (gdy prognoza ruszyła) i 80% — każde raz.
        sent = wait_until(lambda: len(self.notifies()) >= 2 and self.notifies(), timeout=5)
        self.assertEqual([n["summary"] for n in sent],
                         ["Claude: limit sesji skończy się przed resetem", "Claude: zużyto 80% limitu sesji"])
        for note in sent:
            self.assertEqual((note["app"], note["icon"], note["actions"]), ("Tide", "view-statistics", []))
        self.assertEqual(sent[1]["body"], "Zostało 20% · reset 18:30")
        self.assertEqual(sent[1]["replaces"], sent[0]["id"])  # to samo okno — zastępuje poprzednie

        # Historia w $XDG_STATE_HOME/tide, bez tokenu; usługa pytała tylko GET /status.
        history_path = os.path.join(self.state, "tide", "limits-history.jsonl")
        with open(history_path, encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh]
        claude_session = [r for r in rows if (r["s"], r["w"]) == ("claude", "session")]
        self.assertEqual([r["u"] for r in claude_session], SESSION_VALUES)
        self.assertTrue(os.path.exists(os.path.join(self.state, "tide", "limits-alerts.json")))
        self.assertEqual(self.files_with_token(), [])
        self.assertEqual({(m, p, t) for m, p, t in server.requests}, {("GET", "/status", self.token)})

        # Restart usługi: ta sama historia i pamięć powiadomień → ta sama prognoza, bez powtórek.
        proc.terminate()
        proc.wait(10)
        self.assertTrue(wait_until(lambda: not self.bus.bus.interface().isServiceRegistered(SERVICE).value()))
        polled = len(server.requests)
        self.spawn(config_path)
        wait_until(lambda: len(server.requests) >= polled + 3, timeout=5)
        again = wait_until(self.forecast)
        self.assertEqual(again["services"]["claude"]["session"], session)
        wait_until(lambda: False, timeout=0.5)
        self.assertEqual(len(self.notifies()), 2, "restart nie powtarza powiadomień")

    def test_service_down_and_disabled_object(self):
        config_path = write_config(os.path.join(self.tmp, "limity"), self.token, 9)  # nic tam nie słucha
        self.spawn(config_path)
        result = wait_until(lambda: (self.forecast() or {}).get("error") and self.forecast(), timeout=5)
        self.assertFalse(result["available"])
        self.assertIn("nie odpowiada", result["error"])
        self.assertNotIn(self.token, json.dumps(result))
        self.assertEqual(result["services"], {})

    def test_no_limits_flag_drops_the_object(self):
        self.spawn(os.path.join(self.tmp, "brak.json"), "--no-limits")
        reply = self.bus.call(SERVICE, LIMITS_PATH, LIMITS_IFACE, "Forecast")
        from PyQt6.QtDBus import QDBusMessage
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ErrorMessage)


if __name__ == "__main__":
    unittest.main()
