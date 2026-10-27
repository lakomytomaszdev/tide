"""Synchronizacja z YouTrackiem w tablicy (PT-7): projekt z kluczem, BoardMonitor z YouTrackSyncMonitor
w procesie testu i /Board na prywatnej magistrali — zawsze na atrapie YouTracka (fake_youtrack.py).

Tablica to katalog tymczasowy z własną konfiguracją gita; prawdziwa tablica i prawdziwy YouTrack
nie są dotykane.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from . import coordfix as F
from .fake_youtrack import FakeYouTrack
from .privatebus import SERVICE, PrivateBus, app, requires_dbus, wait_until
from .test_board_dbus import git_config, git_out

try:
    from PyQt6.QtDBus import QDBusMessage
except ImportError:  # pragma: no cover
    pass

TOKEN = F.TOKEN_SENTINEL
PATH, IFACE = "/Board", "org.tide.Daemon.Board"


class BoardWorld(unittest.TestCase):
    def setUp(self):
        app()
        self.tmp = tempfile.mkdtemp(prefix="tide-yt-board-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, git_config(self.tmp))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fake = FakeYouTrack(TOKEN)
        self.addCleanup(self.fake.stop)
        token = os.path.join(self.tmp, "token")
        with open(token, "w", encoding="utf-8") as fh:
            fh.write(TOKEN)
        self.config = {"url": self.fake.url, "tokenFile": token, "tokenVar": ""}
        self.root = os.path.join(self.tmp, "board")

    def monitor(self, youtrack=True):
        from tide_daemon.board import ytsync
        from tide_daemon.board.store import BoardStore
        from tide_daemon.board_monitor import BoardMonitor
        from tide_daemon.coordination import youtrack as yt
        from tide_daemon.ytsync_monitor import YouTrackSyncMonitor
        sync = None
        if youtrack:
            settings = dict(yt.load_sync_settings(os.path.join(self.tmp, "brak.json")), importCheckMinutes=0)
            self.engine = ytsync.SyncEngine(path=os.path.join(self.tmp, "state", "youtrack-sync.json"),
                                            client=ytsync.Client(config_loader=lambda: dict(self.config), timeout=1.0),
                                            settings_loader=lambda: settings, debounce=0.2)
            sync = YouTrackSyncMonitor(self.engine, tick_ms=50, start_grace_s=0)
        monitor = BoardMonitor(BoardStore(self.root), debounce_ms=200, poll_ms=60_000, youtrack=sync)
        self.addCleanup(monitor.stop)
        return monitor

    @staticmethod
    def ok(text):
        reply = json.loads(text)
        assert reply.get("ok"), reply
        return reply

    def snapshot(self, monitor):
        return json.loads(monitor.snapshot_json())


class ProjectSettingsTest(BoardWorld):
    def test_project_key_and_sync_toggle(self):
        m = self.monitor(youtrack=False)
        self.ok(m.upsert_project(json.dumps({"name": "logtool", "youtrack": "log"})))
        project = self.snapshot(m)["projects"][0]
        self.assertEqual((project["youtrack"], project["youtrackSync"]), ("LOG", True))
        with open(os.path.join(self.root, "projects", "logtool.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("youtrack: LOG\n", text)
        self.assertNotIn("youtrack-sync", text, "domyślnie włączona — bez klucza w pliku")
        self.ok(m.upsert_project(json.dumps({"slug": "logtool", "youtrackSync": False})))
        self.assertFalse(self.snapshot(m)["projects"][0]["youtrackSync"])
        self.assertEqual(git_out(self.root, "log", "-1", "--format=%s").strip(), "project logtool: update")
        self.ok(m.upsert_project(json.dumps({"name": "inny"})))
        clash = json.loads(m.upsert_project(json.dumps({"slug": "inny", "youtrack": "LOG"})))
        self.assertEqual((clash["ok"], clash["error"]), (False, "exists"))
        bad = json.loads(m.upsert_project(json.dumps({"slug": "inny", "youtrack": "log-1"})))
        self.assertEqual(bad["error"], "bad-request")
        bad = json.loads(m.upsert_project(json.dumps({"slug": "inny", "youtrackSync": "tak"})))
        self.assertEqual(bad["error"], "bad-request")
        self.ok(m.upsert_project(json.dumps({"slug": "logtool", "youtrack": ""})))
        project = next(p for p in self.snapshot(m)["projects"] if p["slug"] == "logtool")
        self.assertEqual((project["youtrack"], project["youtrackSync"]), ("", False), "bez klucza nie ma synchronizacji")

    def test_card_link_is_normalised_and_validated(self):
        m = self.monitor(youtrack=False)
        reply = self.ok(m.create_card(json.dumps({"title": "a", "youtrack": "http://yt:8111/issue/legal-35"})))
        card = self.snapshot(m)["cards"][0]
        self.assertEqual((reply["id"], card["youtrack"]), ("K-1", "LEGAL-35"))
        bad = json.loads(m.update_card("K-1", json.dumps({"youtrack": "nie klucz"})))
        self.assertEqual(bad["error"], "bad-request")
        self.ok(m.update_card("K-1", json.dumps({"youtrack": ""})))
        self.assertIsNone(json.loads(m.snapshot_json())["cards"][0].get("youtrackSync"))


class MonitorSyncTest(BoardWorld):
    def test_moving_card_updates_youtrack_and_status_reaches_snapshot(self):
        self.fake.add("LOG-1", "Open")
        m = self.monitor()
        m.start()
        self.ok(m.upsert_project(json.dumps({"name": "logtool", "youtrack": "LOG"})))
        self.ok(m.create_card(json.dumps({"title": "Eksport", "project": "logtool", "youtrack": "LOG-1"})))
        self.assertTrue(wait_until(lambda: "LOG-1" in self.engine.records, timeout=5), "pierwsze spotkanie")
        self.assertEqual(self.fake.posts(), [], "Backlog → Open: zgłoszenie już otwarte, Tide przyjmuje stan")
        self.ok(m.move_card("K-1", "done", 0))
        self.assertTrue(wait_until(lambda: self.fake.state("LOG-1") == "Verified", timeout=5))
        self.assertTrue(wait_until(lambda: (self.snapshot(m)["cards"][0]["youtrackSync"] or {}).get("status") == "ok"))
        sync = self.snapshot(m)["cards"][0]["youtrackSync"]
        self.assertEqual((sync["issue"], sync["issueState"], sync["url"]), ("LOG-1", "Verified", self.fake.url + "/issue/LOG-1"))
        self.assertEqual(self.snapshot(m)["youtrack"]["enabled"], True)
        self.ok(m.move_card("K-1", "todo", 0))
        self.assertTrue(wait_until(lambda: self.fake.state("LOG-1") == "Reopened", timeout=5), self.fake.posts())

    def test_conflict_shows_in_snapshot_and_resolves_by_card(self):
        self.fake.add("LOG-2", "Open")
        m = self.monitor()
        m.start()
        self.ok(m.upsert_project(json.dumps({"name": "logtool", "youtrack": "LOG"})))
        self.ok(m.create_card(json.dumps({"title": "x", "project": "logtool", "youtrack": "LOG-2"})))
        self.assertTrue(wait_until(lambda: "LOG-2" in self.engine.records, timeout=5))
        self.fake.human_sets("LOG-2", "In Progress")
        self.ok(m.move_card("K-1", "done", 0))
        self.assertTrue(wait_until(lambda: self.snapshot(m)["youtrack"]["conflicts"] == 1, timeout=5))
        snap = self.snapshot(m)
        self.assertEqual(snap["cards"][0]["youtrackSync"]["conflict"], {"youtrack": "In Progress", "tide": "Verified"})
        self.assertTrue(any("LOG-2" in e["message"] and "ręcznie" in e["message"] for e in snap["errors"]))
        self.assertEqual(self.fake.state("LOG-2"), "In Progress")
        bad = json.loads(m.resolve_youtrack("K-1", "cokolwiek"))
        self.assertEqual(bad["error"], "bad-request")
        self.ok(m.resolve_youtrack("K-1", "tide"))
        self.assertTrue(wait_until(lambda: self.fake.state("LOG-2") == "Verified", timeout=5))
        self.assertTrue(wait_until(lambda: self.snapshot(m)["youtrack"]["conflicts"] == 0))
        self.assertEqual(json.loads(m.resolve_youtrack("K-9", "tide"))["error"], "not-found")

    def test_import_creates_backlog_cards_once_with_project_mapping(self):
        self.fake.add("LOG-5", "In Progress", summary="Eksport do CSV", description="Szczegóły\nzgłoszenia",
                      mine="assignee")
        self.fake.add("LEGAL-6", "Open", summary="Umowa", mine="reporter")
        self.fake.add("APP5-7", "Open", summary="Bez projektu", mine="reporter")
        m = self.monitor()
        m.start()
        self.ok(m.upsert_project(json.dumps({"name": "logtool", "youtrack": "LOG"})))
        self.ok(m.create_card(json.dumps({"title": "Już jest", "youtrack": "LEGAL-6"})))
        replies = []
        m.youtrack_issues(True, replies.append)
        self.assertTrue(wait_until(lambda: replies, timeout=5))
        listing = self.ok(replies[0])
        items = {i["id"]: i for i in listing["issues"]}
        self.assertEqual(set(items), {"LOG-5", "LEGAL-6", "APP5-7"})
        self.assertEqual(items["LEGAL-6"]["card"], "K-1")
        self.assertEqual(items["LOG-5"]["boardProject"], "logtool")
        self.assertTrue(wait_until(lambda: self.snapshot(m)["youtrack"]["newIssues"] == 0), "pokazane = przejrzane")
        result = self.ok(m.import_youtrack(json.dumps({"issues": ["LOG-5", "LEGAL-6", "APP5-7", "LOG-99", "zły"]})))
        self.assertEqual([c["issue"] for c in result["created"]], ["LOG-5", "APP5-7"])
        self.assertEqual({s["issue"]: s["reason"] for s in result["skipped"]},
                         {"LEGAL-6": "już jest na tablicy", "LOG-99": "nie ma go na liście z YouTracka — odśwież listę",
                          "zły": "to nie jest klucz zgłoszenia"})
        cards = {c["youtrack"]: c for c in self.snapshot(m)["cards"]}
        self.assertEqual(cards["LOG-5"]["column"], "backlog")
        self.assertEqual(cards["LOG-5"]["project"], "logtool")
        self.assertEqual(cards["LOG-5"]["title"], "Eksport do CSV")
        self.assertEqual(cards["LOG-5"]["description"].strip(), "Szczegóły\nzgłoszenia")
        self.assertEqual(cards["APP5-7"]["project"], "")
        again = self.ok(m.import_youtrack(json.dumps({"issues": ["LOG-5"]})))
        self.assertEqual((again["created"], again["skipped"][0]["card"]), ([], cards["LOG-5"]["id"]), "bez duplikatów")
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")
        # Zaimportowana karta w Backlogu nie cofa zgłoszenia „In Progress” do „Open”.
        self.assertTrue(wait_until(lambda: "LOG-5" in self.engine.records, timeout=5))
        self.assertEqual(self.fake.state("LOG-5"), "In Progress")
        self.assertEqual(self.fake.posts(), [])

    def test_without_youtrack_methods_answer_unavailable(self):
        m = self.monitor(youtrack=False)
        replies = []
        m.youtrack_issues(True, replies.append)
        self.assertEqual(json.loads(replies[0])["error"], "unavailable")
        self.assertEqual(json.loads(m.import_youtrack(json.dumps({"issues": ["LOG-1"]})))["error"], "unavailable")
        self.assertEqual(json.loads(m.resolve_youtrack("LOG-1", "tide"))["error"], "unavailable")
        self.assertNotIn("youtrack", self.snapshot(m))


@requires_dbus
class BoardYouTrackDBusTest(unittest.TestCase):
    """/Board na prywatnej magistrali: usługa z ustawieniami YouTracka wskazującymi atrapę."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-yt-dbus-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake = FakeYouTrack(TOKEN)
        self.addCleanup(self.fake.stop)
        token = os.path.join(self.tmp, "token")
        with open(token, "w", encoding="utf-8") as fh:
            fh.write(TOKEN)
        config = os.path.join(self.tmp, "youtrack.json")
        with open(config, "w", encoding="utf-8") as fh:
            json.dump({"url": self.fake.url, "tokenFile": token, "importCheckMinutes": 0}, fh)
        self.service = PrivateBus("tide-yt-dbus-test")
        self.addCleanup(self.service.stop)
        workspace = os.path.join(self.tmp, "ws")
        os.makedirs(workspace)
        env = dict(git_config(self.tmp), TIDE_YOUTRACK_CONFIG=config, TIDE_YOUTRACK_START_GRACE="0")
        self.daemon = self.service.spawn_daemon(
            "--claude-dir", os.path.join(self.tmp, "claude"), "--codex-dir", os.path.join(self.tmp, "codex"),
            "--no-processes", "--no-notifications", "--no-krunner", "--no-idle", "--no-limits", "--no-report",
            "--no-coordination", "--no-docker", "--workspace", workspace,
            "--board-dir", os.path.join(self.tmp, "board"), extra_env=env)

    def call(self, member, *args, timeout=10000):
        reply = self.service.call(SERVICE, PATH, IFACE, member, *args, timeout=timeout)
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        return json.loads(reply.arguments()[0])

    def test_import_and_sync_over_dbus(self):
        self.fake.add("LOG-1", "Open", summary="Import przez D-Bus", mine="assignee")
        self.assertTrue(self.call("UpsertProject", json.dumps({"name": "logtool", "youtrack": "LOG"}))["ok"])
        listing = self.call("YouTrackIssues", True)
        self.assertEqual([i["id"] for i in listing["issues"]], ["LOG-1"])
        created = self.call("ImportYouTrack", json.dumps({"issues": ["LOG-1"]}))
        self.assertEqual(created["created"][0]["card"], "K-1")
        self.assertTrue(self.call("MoveCard", "K-1", "done", 0)["ok"])
        self.assertTrue(wait_until(lambda: self.fake.state("LOG-1") == "Verified", timeout=15), self.fake.calls)
        card = self.call("Snapshot")["cards"][0]
        self.assertEqual((card["youtrack"], card["project"]), ("LOG-1", "logtool"))
        self.assertTrue(wait_until(lambda: self.call("Snapshot")["cards"][0]["youtrackSync"]["status"] == "ok", timeout=5))
        self.assertEqual(self.call("ResolveYouTrack", "K-1", "youtrack")["error"], "not-found", "bez konfliktu")
        self.assertIsNone(self.daemon.poll())


if __name__ == "__main__":
    unittest.main()
