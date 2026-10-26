"""Obiekt /Coordination usługi na prywatnej magistrali: syntetyczny świat koordynacji (coordfix.py),
atrapa YouTracka (HTTP w wątku testu, token z pliku) i atrapy programów (fakebin.py).

Sprawdzamy kontrakt (Coordinators, Board, RunDetail, Activate, OpenDiff, ShowWindow, Refresh, sygnał
Changed), pole `coordination` przy sesji koordynatora w /Agents, plik dla raportu porannego
(0600, bez tokenu i bez raportów podagentów) i to, że do YouTracka idą wyłącznie zapytania GET.
"""
import json
import os
import shutil
import stat
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

from . import coordfix as F
from .fakebin import calls, make_env
from .privatebus import SERVICE, PrivateBus, requires_dbus, wait_until
from .test_coordination_core import FakeYouTrack

PATH, IFACE = "/Coordination", "org.tide.Daemon.Coordination"
AGENTS_PATH, AGENTS_IFACE = "/Agents", "org.tide.Daemon.Agents"
CLAUDE_ID = "claude:" + F.CLAUDE_COORD
CODEX_ID = "codex:" + F.CODEX_ROOT


@requires_dbus
class CoordinationDBusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-coord-dbus-")
        self.addCleanup(shutil.rmtree, self.tmp)
        FakeYouTrack.calls = []
        FakeYouTrack.status = 200
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeYouTrack)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.world = F.build_world(self.tmp, plan_url=url)
        self.env = make_env(self.tmp)
        token = os.path.join(self.tmp, "token")
        with open(token, "w", encoding="utf-8") as fh:
            fh.write(F.TOKEN_SENTINEL)
        os.makedirs(os.path.join(self.env["XDG_CONFIG_HOME"], "tide"))
        with open(os.path.join(self.env["XDG_CONFIG_HOME"], "tide", "youtrack.json"), "w", encoding="utf-8") as fh:
            json.dump({"url": url, "tokenFile": token}, fh)
        self.window = os.path.join(self.tmp, "sessions-window.py")
        self.state = os.path.join(self.tmp, "state")
        self.bus = PrivateBus("tide-coord-test")
        self.addCleanup(self.bus.stop)

    def spawn(self, *extra, window=True):
        env = {k: self.env[k] for k in ("HOME", "PATH", "TIDE_FAKE_LOG", "XDG_DATA_HOME", "XDG_CONFIG_HOME")}
        env.update(XDG_STATE_HOME=self.state, TIDE_SESSIONS_WINDOW=self.window if window else "")
        workspace = os.path.join(self.tmp, "empty-workspace")
        os.makedirs(workspace, exist_ok=True)
        return self.bus.spawn_daemon("--claude-dir", self.world["claude"], "--codex-dir", self.world["codex"],
                                     "--workspace", workspace, "--no-processes", "--no-notifications",
                                     "--no-krunner", "--no-idle", "--no-limits", "--no-report",
                                     "--now", "2026-10-26T12:00:00Z", *extra, extra_env=env)

    def call(self, member, *args, path=PATH, iface=IFACE):
        reply = self.bus.call(SERVICE, path, iface, member, *args)
        self.assertEqual(reply.errorMessage(), "", member)
        return reply.arguments()[0] if reply.arguments() else None

    def coordinators(self):
        return json.loads(self.call("Coordinators"))

    def test_contract_board_detail_agents_and_cache(self):
        self.spawn()
        listed = wait_until(lambda: len(self.coordinators()) == 2 and self.coordinators(), timeout=15)
        self.assertTrue(listed, "lista koordynatorów po pierwszym odczycie w tle")
        ids = {c["id"] for c in listed}
        self.assertEqual(ids, {CLAUDE_ID, CODEX_ID})
        claude = next(c for c in listed if c["id"] == CLAUDE_ID)
        self.assertEqual((claude["label"], claude["progress"]["text"], claude["tracker"]),
                         ("TOM-20", "TOM-20 · 3/10", "youtrack"))
        self.assertNotIn("tickets", claude)

        board = json.loads(self.call("Board", CLAUDE_ID))
        states = {t["id"] or t["key"]: t["state"] for t in board["tickets"]}
        self.assertEqual(states["TOM-140"], "w-toku")
        self.assertEqual(states["TOM-141"], "czeka")
        self.assertEqual(states["LOG-7"], "gotowe", "stan z atrapy YouTracka")
        self.assertTrue(board["tracker"]["available"])
        self.assertEqual(json.loads(self.call("Board", "claude:nie-ma")), {})

        detail = json.loads(self.call("RunDetail", CLAUDE_ID, "a140rev00000000001"))
        self.assertIn("MAJOR-1", detail["result"])
        self.assertEqual(json.loads(self.call("RunDetail", CLAUDE_ID, "nie-ma")), {})

        sessions = json.loads(self.call("Sessions", path=AGENTS_PATH, iface=AGENTS_IFACE))
        coordinated = wait_until(lambda: [s for s in json.loads(self.call("Sessions", path=AGENTS_PATH,
                                                                          iface=AGENTS_IFACE))
                                          if s.get("coordination")], timeout=10)
        self.assertTrue(coordinated, sessions)
        by_id = {s["id"]: s for s in coordinated}
        self.assertEqual(set(by_id), {CLAUDE_ID, CODEX_ID})
        self.assertEqual(by_id[CLAUDE_ID]["coordination"]["text"], "TOM-20 · 3/10")

        cache = os.path.join(self.state, "tide", "coordination.json")
        self.assertTrue(wait_until(lambda: os.path.exists(cache), timeout=5))
        self.assertEqual(stat.S_IMODE(os.stat(cache).st_mode), 0o600)
        for dirpath, _dirs, names in os.walk(self.state):
            for name in names:
                with open(os.path.join(dirpath, name), "rb") as fh:
                    data = fh.read()
                self.assertNotIn(F.TOKEN_SENTINEL.encode(), data, name)
                self.assertNotIn(b"MAJOR-1", data, name)
        self.assertTrue(FakeYouTrack.calls)
        self.assertEqual({c[0] for c in FakeYouTrack.calls}, {"GET"})

    def test_changed_signal_actions_and_window(self):
        from PyQt6.QtCore import QObject, pyqtSlot

        self.spawn()
        self.assertTrue(wait_until(lambda: len(self.coordinators()) == 2, timeout=15))
        changes = []

        class Receiver(QObject):
            @pyqtSlot()
            def changed(self):
                changes.append(True)

        receiver = Receiver()
        self.assertTrue(self.bus.bus.connect(SERVICE, PATH, IFACE, "Changed", receiver.changed))
        extra = F.ClaudeLog(self.world["claude"], F.CLAUDE_COORD, self.world["repo"], "feat/tom-20-release")
        extra.notification("a142rev00000000001", "completed", 0.2, "Review finished", "Verdict: APPROVE. No MAJOR.")
        self.world["log"].append(extra.lines)
        self.call("Refresh", False)
        self.assertTrue(wait_until(lambda: changes, timeout=10), "sygnał Changed po nowym wpisie")
        board = json.loads(self.call("Board", CLAUDE_ID))
        self.assertEqual({t["id"]: t["state"] for t in board["tickets"]}["TOM-142"], "gotowe")

        self.assertTrue(self.call("Activate", CLAUDE_ID))
        launched = wait_until(lambda: [c for c in calls(self.env) if "--resume" in c], timeout=5)
        self.assertTrue(launched, calls(self.env))
        self.assertIn(f"--workdir {self.world['repo']}", launched[0])
        self.assertIn(f"--resume {F.CLAUDE_COORD}", launched[0])
        self.assertFalse(self.call("Activate", "claude:nie-ma"))
        self.assertFalse(self.call("OpenDiff", CLAUDE_ID, "TOM-140"), "bez repozytorium na liście worktrees")
        self.assertFalse(self.call("OpenWorktree", CLAUDE_ID, "TOM-146"))

        self.assertFalse(self.call("ShowWindow", ""), "okno nie jest zainstalowane")
        with open(self.window, "w", encoding="utf-8") as fh:
            fh.write("# atrapa okna\n")
        self.assertTrue(self.call("ShowWindow", CODEX_ID))
        shown = wait_until(lambda: [c for c in calls(self.env) if "sessions-window.py" in c], timeout=5)
        self.assertIn(f"--select {CODEX_ID}", shown[0])

    def test_disabled_area_and_no_youtrack(self):
        self.spawn("--no-coordination")
        reply = self.bus.call(SERVICE, PATH, IFACE, "Coordinators")
        self.assertNotEqual(reply.errorMessage(), "", "obiekt /Coordination wyłączony")
        self.bus.stop()

    def test_without_youtrack_option_nothing_is_asked(self):
        self.spawn("--no-youtrack")
        self.assertTrue(wait_until(lambda: len(self.coordinators()) == 2, timeout=15))
        board = json.loads(self.call("Board", CLAUDE_ID))
        self.assertEqual(board["tracker"]["kind"], "youtrack")
        self.assertEqual(FakeYouTrack.calls, [])


if __name__ == "__main__":
    unittest.main()
