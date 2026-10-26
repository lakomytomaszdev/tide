"""Cienki test kontraktu obszaru worktrees: te same dane co z rdzenia wychodzą przez D-Bus,
a Remove() zwraca wynik dla każdej ścieżki.

Usługa działa na prywatnej magistrali (privatebus.py) i na tymczasowych repozytoriach (gitrepos.py).
Agent jest zasymulowany sesją Claude Code z katalogiem roboczym w worktree „agent”.
Open() jest wołane tylko z argumentami, które nic nie uruchamiają.
"""
import json
import os
import shutil
import tempfile
import unittest

from . import gitrepos
from .privatebus import SERVICE, PrivateBus, requires_dbus, wait_until
from tide_daemon.worktrees import scan

try:
    from PyQt6.QtCore import QMetaType
    from PyQt6.QtDBus import QDBusArgument, QDBusMessage
except ImportError:  # pragma: no cover
    pass

PATH, IFACE = "/Worktrees", "org.tide.Daemon.Worktrees"
NOW_TEXT = "2026-10-26T12:00:00Z"


def write_agent_session(claude_dir: str, cwd: str) -> None:
    project = os.path.join(claude_dir, "projects", cwd.replace("/", "-"))
    os.makedirs(project)
    with open(os.path.join(project, "s-agent.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "timestamp": "2026-10-26T11:59:00.000Z", "cwd": cwd,
                             "message": {"role": "user", "content": "hej"}}) + "\n")


@requires_dbus
@unittest.skipUnless(shutil.which("git"), "brak gita")
class WorktreesContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-wt-dbus-")
        cls.root = os.path.join(cls.tmp, "work")
        cls.p = gitrepos.build(cls.root)
        cls.agent_cwd = os.path.join(cls.p["agent"], "src")
        write_agent_session(os.path.join(cls.tmp, "claude"), cls.agent_cwd)
        cls.service = PrivateBus("tide-worktrees-test")
        cls.daemon = cls.service.spawn_daemon(
            "--claude-dir", os.path.join(cls.tmp, "claude"), "--codex-dir", os.path.join(cls.tmp, "codex"),
            "--no-processes", "--now", NOW_TEXT, "--workspace", cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, member, *args, timeout=10000):
        reply = self.service.call(SERVICE, PATH, IFACE, member, *args, timeout=timeout)
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        return reply.arguments()[0] if reply.arguments() else None

    def listed(self):
        return json.loads(self.call("List"))

    def measured(self):
        items = self.listed()
        return items if items and all(w["size"] >= 0 or w["missing"] for w in items) else None

    def test_1_list_matches_core(self):
        over_dbus = wait_until(self.measured, timeout=15)
        self.assertTrue(over_dbus, "lista z rozmiarami nie pojawiła się")
        self.assertEqual(over_dbus, scan(self.root, [self.agent_cwd]))
        agent = next(w for w in over_dbus if w["path"] == self.p["agent"])
        self.assertTrue(agent["hasAgent"], "sesja agenta z obszaru Agents oznacza worktree")

    def test_2_remove_reports_each_path(self):
        wait_until(self.measured, timeout=15)
        ask = [self.p["merged"], self.p["dirty"], self.p["agent"], self.p["alpha"]]
        as_paths = QDBusArgument(ask, QMetaType.Type.QStringList.value)  # sygnatura „as”, nie „av”
        results = json.loads(self.call("Remove", as_paths, timeout=60000))
        self.assertEqual([(r["path"], r["removed"], r["reason"]) for r in results], [
            (self.p["merged"], True, ""),
            (self.p["dirty"], False, "dirty"),
            (self.p["agent"], False, "agent"),
            (self.p["alpha"], False, "main"),
        ])
        self.assertTrue(results[0]["branchDeleted"])
        self.assertFalse(os.path.exists(self.p["merged"]))
        gone = wait_until(lambda: self.p["merged"] not in {w["path"] for w in self.listed()})
        self.assertTrue(gone, "lista po sprzątaniu nie została odświeżona")

    def test_3_refresh_on_demand(self):
        wait_until(self.measured, timeout=15)
        with open(os.path.join(self.p["detached"], "nowy.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        self.call("Refresh")
        dirty = wait_until(lambda: next((w["dirty"] for w in self.listed() if w["path"] == self.p["detached"]), 0))
        self.assertEqual(dirty, 1)

    def test_open_refuses_unknown_targets(self):
        self.assertIs(self.call("Open", os.path.join(self.root, "nie-ma"), "terminal"), False)
        self.assertIs(self.call("Open", self.p["alpha"], "nieznany-program"), False)

    def test_launchers_and_introspection(self):
        self.assertEqual(set(json.loads(self.call("Launchers"))), {"terminal", "rider"})
        xml = self.service.call(SERVICE, PATH, "org.freedesktop.DBus.Introspectable", "Introspect").arguments()[0]
        self.assertIn(f'<interface name="{IFACE}">', xml)
        self.assertIn('<arg name="paths" type="as" direction="in"/>', xml)
        self.assertIn('<signal name="Changed"', xml)


if __name__ == "__main__":
    unittest.main()
