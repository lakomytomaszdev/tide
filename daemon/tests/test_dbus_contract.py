"""Cienki test kontraktu: te same sesje co z rdzenia wychodzą przez D-Bus.

Usługa startuje jako osobny proces na prywatnej magistrali (privatebus.py), więc test
nie dotyka magistrali sesji pulpitu. Bez dbus-daemon albo QtDBus test jest pomijany.
"""
import json
import os
import shutil
import tempfile
import time
import unittest

from . import FIXTURES
from .privatebus import SERVICE, PrivateBus, requires_dbus
from tide_daemon.agents import scan
from tide_daemon.agents.timeutil import parse_ts

try:
    from PyQt6.QtDBus import QDBusMessage
except ImportError:  # pragma: no cover
    pass

NOW_TEXT = "2026-10-26T12:00:00Z"
PATH, IFACE = "/Agents", "org.tide.Daemon.Agents"


def start_daemon(bus: PrivateBus, claude_dir, codex_dir, workspace):
    return bus.spawn_daemon("--claude-dir", claude_dir, "--codex-dir", codex_dir, "--no-processes",
                            "--now", NOW_TEXT, "--workspace", workspace)


@requires_dbus
class DBusContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.empty = tempfile.mkdtemp(prefix="tide-empty-")
        cls.service = PrivateBus("tide-contract-test")
        cls.daemon = start_daemon(cls.service, os.path.join(FIXTURES, "claude"), os.path.join(FIXTURES, "codex"),
                                  cls.empty)
        cls.bus = cls.service.bus

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()
        shutil.rmtree(cls.empty, ignore_errors=True)

    def call(self, member, *args):
        reply = self.service.call(SERVICE, PATH, IFACE, member, *args)
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
        xml = self.service.call(SERVICE, PATH, "org.freedesktop.DBus.Introspectable", "Introspect").arguments()[0]
        self.assertIn(f'<interface name="{IFACE}">', xml)
        self.assertIn('<signal name="Changed"', xml)
        self.assertIn('<method name="ActivateLongestWaiting">', xml)

    def test_longest_waiting_without_windows_does_nothing(self):
        # Bez procesów żadna czekająca sesja nie ma okna — skrót po cichu nic nie robi.
        self.assertIs(self.call("ActivateLongestWaiting"), False)


@requires_dbus
class LiveUpdateTest(unittest.TestCase):
    """Dopisanie wpisu do dziennika zmienia stan widoczny przez D-Bus w ciągu kilku sekund."""

    def test_appended_entry_changes_state_quickly(self):
        tmp = tempfile.mkdtemp(prefix="tide-live-")
        self.addCleanup(shutil.rmtree, tmp)
        shutil.copytree(os.path.join(FIXTURES, "codex"), os.path.join(tmp, "codex"))
        os.makedirs(os.path.join(tmp, "work"))
        service = PrivateBus("tide-live-test")
        self.addCleanup(service.stop)
        start_daemon(service, os.path.join(tmp, "claude"), os.path.join(tmp, "codex"), os.path.join(tmp, "work"))
        sid = "codex:0000000a-0000-4000-8000-000000000001"

        def state():
            reply = service.call(SERVICE, PATH, IFACE, "Sessions")
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
