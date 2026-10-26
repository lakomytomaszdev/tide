"""Cienki test kontraktu: te same sesje co z rdzenia wychodzą przez D-Bus.

Usługa startuje jako osobny proces na prywatnej magistrali (własny dbus-daemon), więc test
nie dotyka magistrali sesji pulpitu. Bez dbus-daemon albo QtDBus test jest pomijany.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from . import DAEMON_DIR, FIXTURES
from tide_daemon.agents import scan
from tide_daemon.agents.timeutil import parse_ts

try:
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage
except ImportError:  # pragma: no cover
    QDBusConnection = None

NOW_TEXT = "2026-10-26T12:00:00Z"
SERVICE, PATH, IFACE = "org.tide.Daemon", "/Agents", "org.tide.Daemon.Agents"


class PrivateBusDaemon:
    """Prywatny dbus-daemon i tide-daemon na podanych katalogach dzienników."""

    def __init__(self, claude_dir, codex_dir, name):
        self.name = name
        self.bus_proc = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--nopidfile", "--print-address=1"],
            stdout=subprocess.PIPE, text=True)
        self.address = self.bus_proc.stdout.readline().strip()
        self.daemon = None
        if not self.address:
            self.stop()
            raise unittest.SkipTest("prywatny dbus-daemon nie wystartował")
        env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=self.address)
        self.daemon = subprocess.Popen(
            [sys.executable, os.path.join(DAEMON_DIR, "tide-daemon.py"),
             "--claude-dir", claude_dir, "--codex-dir", codex_dir, "--no-processes", "--now", NOW_TEXT],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.bus = QDBusConnection.connectToBus(self.address, name)
        deadline = time.time() + 10
        while time.time() < deadline and self.daemon.poll() is None:
            if self.bus.interface().isServiceRegistered(SERVICE).value():
                return
            time.sleep(0.1)
        self.stop()
        raise AssertionError("usługa nie zarejestrowała się na magistrali")

    def call(self, member, *args):
        msg = QDBusMessage.createMethodCall(SERVICE, PATH, IFACE, member)
        if args:
            msg.setArguments(list(args))
        return self.bus.call(msg, QDBus.CallMode.Block, 5000)

    def stop(self):
        QDBusConnection.disconnectFromBus(self.name)
        for proc in (self.daemon, self.bus_proc):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if self.bus_proc.stdout:
            self.bus_proc.stdout.close()


requires_dbus = unittest.skipUnless(QDBusConnection is not None and shutil.which("dbus-daemon"),
                                    "brak QtDBus albo dbus-daemon")


@requires_dbus
class DBusContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])
        cls.service = PrivateBusDaemon(os.path.join(FIXTURES, "claude"), os.path.join(FIXTURES, "codex"),
                                       "tide-contract-test")
        cls.bus = cls.service.bus
        cls.daemon = cls.service.daemon

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()

    def call(self, member, *args):
        reply = self.service.call(member, *args)
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        return reply.arguments()[0]

    def test_sessions_match_core(self):
        self.assertIsNone(self.daemon.poll(), "usługa nie działa")
        over_dbus = json.loads(self.call("Sessions"))
        expected = scan(os.path.join(FIXTURES, "claude"), os.path.join(FIXTURES, "codex"), parse_ts(NOW_TEXT))
        self.assertEqual(over_dbus, expected)
        self.assertGreater(len(over_dbus), 0)

    def test_activate_unknown_window_returns_false(self):
        self.assertIs(self.call("ActivateSession", "claude:c-waiting"), False)
        self.assertIs(self.call("ActivateSession", "nie-ma-takiej"), False)

    def test_introspection_declares_changed_signal(self):
        msg = QDBusMessage.createMethodCall(SERVICE, PATH, "org.freedesktop.DBus.Introspectable", "Introspect")
        xml = self.bus.call(msg, QDBus.CallMode.Block, 5000).arguments()[0]
        self.assertIn(f'<interface name="{IFACE}">', xml)
        self.assertIn('<signal name="Changed"', xml)


@requires_dbus
class LiveUpdateTest(unittest.TestCase):
    """Dopisanie wpisu do dziennika zmienia stan widoczny przez D-Bus w ciągu kilku sekund."""

    def test_appended_entry_changes_state_quickly(self):
        QCoreApplication.instance() or QCoreApplication([])
        tmp = tempfile.mkdtemp(prefix="tide-live-")
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copytree(os.path.join(FIXTURES, "codex"), os.path.join(tmp, "codex"))
        service = PrivateBusDaemon(os.path.join(tmp, "claude"), os.path.join(tmp, "codex"), "tide-live-test")
        self.addCleanup(service.stop)
        sid = "codex:0000000a-0000-4000-8000-000000000001"

        def state():
            reply = service.call("Sessions")
            return {s["id"]: s["state"] for s in json.loads(reply.arguments()[0])}.get(sid)

        self.assertEqual(state(), "working")
        log = os.path.join(tmp, "codex/sessions/2026/10/26/"
                                "rollout-2026-10-26T11-30-00-0000000a-0000-4000-8000-000000000001.jsonl")
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"timestamp": "2026-10-26T11:59:58.000Z", "type": "event_msg",
                                 "payload": {"type": "task_complete", "turn_id": "t1"}}) + "\n")
        started = time.time()
        while time.time() - started < 3 and state() != "waiting":
            time.sleep(0.1)
        self.assertEqual(state(), "waiting", "stan nie zmienił się w ciągu 3 s")


if __name__ == "__main__":
    unittest.main()
