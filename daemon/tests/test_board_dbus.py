"""Tablica pracy w usłudze: BoardMonitor (zmiany z zewnątrz) i kontrakt /Board na prywatnej magistrali.

Usługa testowa dostaje tymczasowy katalog tablicy (--board-dir) i tymczasową konfigurację gita,
więc nigdy nie dotyka ~/DevWorkspace/tide-board ani konfiguracji użytkownika.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from .privatebus import SERVICE, PrivateBus, app, requires_dbus, wait_until

try:
    from PyQt6.QtCore import QObject, pyqtSlot
    from PyQt6.QtDBus import QDBusMessage
except ImportError:  # pragma: no cover
    QObject = object

PATH, IFACE = "/Board", "org.tide.Daemon.Board"
NOW_TEXT = "2026-10-27T12:00:00Z"


def git_out(cwd, *args) -> str:
    return subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True, text=True).stdout


def git_config(tmp) -> dict:
    config = os.path.join(tmp, "gitconfig")
    with open(config, "w", encoding="utf-8") as fh:
        fh.write("[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
    return {"GIT_CONFIG_GLOBAL": config, "GIT_CONFIG_NOSYSTEM": "1"}


class MonitorTest(unittest.TestCase):
    """BoardMonitor w procesie testu: zmiana pliku z zewnątrz → commit i Changed po krótkiej ciszy."""

    def setUp(self):
        app()
        from tide_daemon.board.store import BoardStore
        from tide_daemon.board_monitor import BoardMonitor
        self.tmp = tempfile.mkdtemp(prefix="tide-board-mon-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, git_config(self.tmp))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = os.path.join(self.tmp, "board")
        self.monitor = BoardMonitor(BoardStore(self.root), debounce_ms=200, poll_ms=60_000)
        self.addCleanup(self.monitor.stop)
        self.changes = []
        self.monitor.changed.connect(lambda: self.changes.append(True))

    def test_external_edit_is_committed_and_signalled(self):
        self.monitor.start()
        self.assertFalse(json.loads(self.monitor.snapshot_json())["exists"])
        reply = json.loads(self.monitor.create_card(json.dumps({"title": "Pierwsza"})))
        self.assertEqual(reply, {"ok": True, "id": "K-1", "commitError": ""})
        self.assertTrue(self.changes)
        self.changes.clear()
        # agent pisze plan w docs/ (nowy podkatalog) i ktoś dopisuje do karty w miejscu
        os.makedirs(os.path.join(self.root, "docs", "dom", "plans"))
        wait_until(lambda: False, timeout=0.3)
        with open(os.path.join(self.root, "docs", "dom", "plans", "plan.md"), "w", encoding="utf-8") as fh:
            fh.write("# plan\n")
        with open(os.path.join(self.root, "cards", "K-1.md"), "a", encoding="utf-8") as fh:
            fh.write("\nDopisek.\n")
        self.assertTrue(wait_until(lambda: git_out(self.root, "log", "-1", "--format=%s").strip()
                                   == "sync external changes", timeout=5), git_out(self.root, "log", "--format=%s"))
        self.assertTrue(wait_until(lambda: self.changes, timeout=2))
        snap = json.loads(self.monitor.snapshot_json())
        self.assertIn("Dopisek.", snap["cards"][0]["description"])
        self.assertEqual(snap["lastCommit"]["subject"], "sync external changes")
        self.assertEqual(git_out(self.root, "status", "--porcelain"), "")

    def test_own_write_does_not_produce_sync_commit(self):
        self.monitor.start()
        self.monitor.create_card(json.dumps({"title": "a"}))
        self.monitor.move_card("K-1", "todo", 0)
        wait_until(lambda: False, timeout=0.8)  # cisza dłuższa niż debounce
        self.assertEqual(git_out(self.root, "log", "--format=%s").split("\n")[:2], ["K-1: move to todo", "K-1: create"])

    def test_errors_are_json(self):
        self.assertEqual(json.loads(self.monitor.create_card("nie json")),
                         {"ok": False, "error": "bad-request", "message": "niepoprawny JSON"})
        self.assertEqual(json.loads(self.monitor.create_card("[1]")),
                         {"ok": False, "error": "bad-request", "message": "oczekiwano obiektu JSON"})
        self.assertEqual(json.loads(self.monitor.move_card("K-1", "todo", 0))["error"], "not-found")
        self.assertEqual(json.loads(self.monitor.render_flow("zle", ""))["error"], "bad-request")
        self.assertFalse(os.path.exists(self.root))  # żaden błąd nie zakłada tablicy


class Receiver(QObject):
    def __init__(self):
        super().__init__()
        self.count = 0

    @pyqtSlot()
    def changed(self):
        self.count += 1


@requires_dbus
class BoardDBusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tide-board-dbus-")
        cls.ws = os.path.join(cls.tmp, "ws")
        cls.repo = os.path.join(cls.ws, "mobileapp")
        os.makedirs(cls.repo)
        env = git_config(cls.tmp)
        subprocess.run(["git", "-C", cls.repo, "init", "-q"], check=True, env=dict(os.environ, **env))
        cls.board = os.path.join(cls.ws, "tide-board")
        cls.service = PrivateBus("tide-board-test")
        cls.daemon = cls.service.spawn_daemon(
            "--claude-dir", os.path.join(cls.tmp, "claude"), "--codex-dir", os.path.join(cls.tmp, "codex"),
            "--no-processes", "--no-notifications", "--now", NOW_TEXT, "--workspace", cls.ws,
            "--board-dir", cls.board, extra_env=env)

    @classmethod
    def tearDownClass(cls):
        cls.service.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, member, *args):
        reply = self.service.call(SERVICE, PATH, IFACE, member, *args)
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        return json.loads(reply.arguments()[0])

    def snapshot(self):
        return self.call("Snapshot")

    def test_1_introspection(self):
        xml = self.service.call(SERVICE, PATH, "org.freedesktop.DBus.Introspectable", "Introspect").arguments()[0]
        self.assertIn(f'<interface name="{IFACE}">', xml)
        for member in ("Snapshot", "Work", "CreateCard", "UpdateCard", "MoveCard", "UpsertProject", "UpsertTag",
                       "RenderFlow", "Badge", "ShowWindow", "CardDocs"):
            self.assertIn(f'<method name="{member}">', xml)
        work = self.call("Work")  # lista „Praca” (PT-40): bez tablicy — pusta, niczego nie zakłada
        self.assertEqual((work["items"], work["counts"]["total"]), ([], 0))
        self.assertIn('<signal name="Changed"', xml)
        self.assertIn('<signal name="WorkChanged"', xml)
        self.assertIn('<arg name="order" type="i" direction="in"/>', xml)

    def test_2_full_flow(self):
        snap = self.snapshot()
        self.assertEqual(snap["boardDir"], self.board)
        self.assertFalse(os.path.exists(self.board))  # odczyt niczego nie zakłada
        self.assertTrue(wait_until(lambda: [r["path"] for r in self.snapshot()["repos"]] == [self.repo], timeout=10),
                        self.snapshot()["repos"])

        receiver = Receiver()
        self.assertTrue(self.service.bus.connect(SERVICE, PATH, IFACE, "Changed", receiver.changed))
        created = self.call("CreateCard", json.dumps({"title": "Historia diagnoz po VIN", "repo": self.repo,
                                                      "tags": ["Firma"], "description": "Opis"}))
        self.assertEqual(created, {"ok": True, "id": "K-1", "commitError": ""})
        self.assertTrue(wait_until(lambda: receiver.count > 0, timeout=5), "sygnał Changed po CreateCard")

        self.assertEqual(self.call("UpsertProject", json.dumps({"slug": "mobileapp", "tags": "Firma", "mode": "spec"})),
                         {"ok": True, "slug": "mobileapp", "changed": True, "commitError": ""})
        self.assertEqual(self.call("UpsertTag", json.dumps({"name": "pilne", "color": "rose"}))["ok"], True)
        self.call("CreateCard", json.dumps({"title": "Druga", "tags": "pilne"}))
        self.assertEqual(self.call("UpdateCard", "K-2", json.dumps({"note": "robię sam"}))["changed"], True)
        self.assertEqual(self.call("MoveCard", "K-2", "todo", 0), {"ok": True, "id": "K-2", "changed": True, "commitError": ""})

        snap = self.snapshot()
        self.assertTrue(snap["exists"])
        self.assertEqual(snap["repos"], [])  # repozytorium jest już projektem
        cards = {c["id"]: c for c in snap["cards"]}
        self.assertEqual(cards["K-1"]["project"], "mobileapp")
        self.assertEqual(cards["K-1"]["resolvedTags"], ["Firma"])
        self.assertEqual((cards["K-2"]["column"], cards["K-2"]["note"]), ("todo", "robię sam"))
        self.assertEqual(snap["lastCommit"]["subject"], "K-2: move to todo")
        self.assertEqual([c["id"] for c in snap["columns"]],
                         ["backlog", "todo", "progress", "waiting", "review", "done", "archived"])

        flow = self.call("RenderFlow", "K-1", "")
        self.assertEqual((flow["ok"], flow["mode"], flow["tool"], flow["branch"]),
                         (True, "spec", "claude", "claude/k-1-historia-diagnoz-vin"))
        self.assertIn("Karta K-1: Historia diagnoz po VIN", flow["text"])

        log = git_out(self.board, "log", "--format=%an|%s").splitlines()[::-1]
        self.assertEqual(log, ["Tester|board: create", "Tester|project mobileapp: create", "Tester|K-1: create",
                               "Tester|project mobileapp: update", "Tester|tag pilne: create", "Tester|K-2: create",
                               "Tester|K-2: update note", "Tester|K-2: move to todo"])
        self.assertEqual(git_out(self.board, "remote").strip(), "")

        # zmiana z zewnątrz → commit i Changed
        seen = receiver.count
        with open(os.path.join(self.board, "cards", "K-1.md"), "a", encoding="utf-8") as fh:
            fh.write("\nRęcznie.\n")
        self.assertTrue(wait_until(lambda: git_out(self.board, "log", "-1", "--format=%s").strip()
                                   == "sync external changes", timeout=10))
        self.assertTrue(wait_until(lambda: receiver.count > seen, timeout=5))
        self.assertIn("Ręcznie.", {c["id"]: c for c in self.snapshot()["cards"]}["K-1"]["description"])

    def test_3_errors(self):
        head = git_out(self.board, "rev-parse", "HEAD")
        for member, args, code in (("CreateCard", ("{",), "bad-request"),
                                   ("CreateCard", (json.dumps({"title": ""}),), "bad-request"),
                                   ("UpdateCard", ("K-99", json.dumps({"title": "x"})), "not-found"),
                                   ("MoveCard", ("K-1", "progress", 0), "bad-request"),
                                   ("UpsertProject", (json.dumps({"repo": "../wyjscie"}),), "bad-request"),
                                   ("UpsertTag", (json.dumps({"name": "x", "color": "zielony"}),), "bad-request"),
                                   ("RenderFlow", ("K-1", "turbo"), "bad-request")):
            reply = self.call(member, *args)
            self.assertEqual(sorted(reply), ["error", "message", "ok"], (member, reply))
            self.assertEqual((reply["ok"], reply["error"]), (False, code), (member, reply))
            self.assertTrue(reply["message"])
        self.assertEqual(git_out(self.board, "rev-parse", "HEAD"), head)  # błąd nie zostawia commita

    def test_4_card_docs(self):
        # Plan karty w repozytorium projektu (docs/agents/plans) i specyfikacja po nazwie pliku — odczyt
        # w wątku roboczym usługi, odpowiedź opóźniona; repozytorium tablicy bez zmian.
        head = git_out(self.board, "rev-parse", "HEAD")
        plans = os.path.join(self.repo, "docs", "agents", "plans")
        os.makedirs(plans)
        with open(os.path.join(plans, "2026-10-27-historia.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Historia diagnoz\ncard: K-1\nstatus: active\n---\n\n"
                     "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |\n|---|---|---|---|---|---|\n"
                     "| DP-1 | Lista | w-toku | feat/dp-1 | | |\n\n## Notatki\n\nTekst <b>planu</b>.\n")
        specs = os.path.join(self.repo, "docs", "agents", "specs")
        os.makedirs(specs)
        with open(os.path.join(specs, "k-1-historia.md"), "w", encoding="utf-8") as fh:
            fh.write("---\nstatus: gotowa\n---\n# Historia po VIN\n\nOpis.\n")
        docs = self.call("CardDocs", "k-1")
        self.assertEqual((docs["ok"], docs["card"], docs["plan"]["title"], docs["plan"]["link"]),
                         (True, "K-1", "Historia diagnoz", "card"))
        self.assertEqual([t["id"] for t in docs["plan"]["tickets"]], ["DP-1"])
        self.assertIn("Tekst <b>planu</b>.", docs["plan"]["markdown"], "surowy Markdown — oczyszcza okno")
        self.assertEqual((docs["spec"]["title"], docs["spec"]["ready"]), ("Historia po VIN", True))
        self.assertEqual(self.call("CardDocs", "K-2")["plan"], None)
        for card_id, code in (("K-99", "not-found"), ("plan", "bad-request")):
            reply = self.call("CardDocs", card_id)
            self.assertEqual((reply["ok"], reply["error"]), (False, code))
        self.assertEqual(git_out(self.board, "rev-parse", "HEAD"), head)


class BadgeTest(unittest.TestCase):
    """Reguła plakietki (ta sama w apps/board/backend.py): czeka → „● N”, inaczej karty w grze."""

    def test_badge_rule(self):
        from tide_daemon.board_monitor import badge
        cards = [{"column": "backlog", "boardColumn": "backlog"}, {"column": "todo", "boardColumn": "progress"},
                 {"column": "done", "boardColumn": "done"}, {"column": "archived", "boardColumn": "archived"}]
        self.assertEqual(badge({"cards": cards}), ("2", "muted"))
        cards.append({"column": "todo", "boardColumn": "waiting"})
        cards.append({"column": "todo", "boardColumn": "waiting"})
        self.assertEqual(badge({"cards": cards}), ("● 2", "waiting"))
        self.assertEqual(badge({"cards": []}), ("", "muted"))
        self.assertEqual(badge({"cards": "zle"}), ("", "muted"))
        self.assertEqual(badge(None), ("", "muted"))
        self.assertEqual(badge({"cards": [None, {"column": "todo"}]}), ("1", "muted"), "bez boardColumn — kolumna z pliku")


@requires_dbus
class BoardWindowTest(unittest.TestCase):
    """Badge() i ShowWindow(id) na prywatnej magistrali: atrapa okna Tide ($TIDE_WINDOW) i atrapy
    programów (systemd-run zapisuje wywołanie) — żadne prawdziwe okno nie powstaje."""

    def setUp(self):
        from .fakebin import make_env
        self.tmp = tempfile.mkdtemp(prefix="tide-board-win-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env = make_env(self.tmp)
        self.window = os.path.join(self.tmp, "tide-window", "tide.py")
        self.board = os.path.join(self.tmp, "board")
        self.service = PrivateBus("tide-board-win-test")
        self.addCleanup(self.service.stop)
        env = {k: self.env[k] for k in ("HOME", "PATH", "TIDE_FAKE_LOG", "XDG_DATA_HOME", "XDG_CONFIG_HOME")}
        env.update(git_config(self.tmp), TIDE_WINDOW=self.window)
        workspace = os.path.join(self.tmp, "ws")
        os.makedirs(workspace)
        self.service.spawn_daemon("--claude-dir", os.path.join(self.tmp, "claude"), "--codex-dir",
                                  os.path.join(self.tmp, "codex"), "--no-processes", "--no-notifications",
                                  "--no-krunner", "--no-idle", "--no-limits", "--no-report", "--no-coordination",
                                  "--no-docker", "--now", NOW_TEXT, "--workspace", workspace, "--board-dir", self.board,
                                  extra_env=env)

    def call(self, member, *args):
        reply = self.service.call(SERVICE, PATH, IFACE, member, *args)
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ReplyMessage, reply.errorMessage())
        return reply.arguments()[0]

    def shown(self):
        from .fakebin import calls
        return [c for c in calls(self.env) if self.window in c]

    def test_badge_follows_cards(self):
        self.assertEqual(json.loads(self.call("Badge")), {"text": "", "tone": "muted"}, "bez tablicy — ukryta")
        self.assertFalse(os.path.exists(self.board), "Badge() niczego nie zakłada")
        self.call("CreateCard", json.dumps({"title": "a"}))
        self.call("CreateCard", json.dumps({"title": "b", "column": "todo"}))
        self.call("CreateCard", json.dumps({"title": "c", "column": "done"}))
        self.assertEqual(json.loads(self.call("Badge")), {"text": "2", "tone": "muted"}, "Gotowe się nie liczą")
        self.call("MoveCard", "K-1", "archived", 0)
        self.assertEqual(json.loads(self.call("Badge")), {"text": "1", "tone": "muted"})

    def test_show_window_opens_board_view(self):
        from .fakebin import calls
        self.assertFalse(self.call("ShowWindow", "K-3"), "okno nie jest zainstalowane")
        self.assertEqual(self.shown(), [])
        os.makedirs(os.path.dirname(self.window))
        with open(self.window, "w", encoding="utf-8") as fh:
            fh.write("# atrapa okna Tide\n")
        self.assertTrue(self.call("ShowWindow", "k-3"))
        self.assertTrue(wait_until(lambda: self.shown(), timeout=5), calls(self.env))
        self.assertIn(f"{self.window} --view board --select K-3", self.shown()[0], "ID karty znormalizowane")
        self.assertTrue(self.call("ShowWindow", ""))
        self.assertTrue(self.call("ShowWindow", "--view sessions"), "nie-ID karty — samo okno, bez argumentu")
        self.assertTrue(wait_until(lambda: len(self.shown()) == 3, timeout=5), calls(self.env))
        for line in self.shown()[1:]:
            self.assertTrue(line.endswith(f"{self.window} --view board"), line)


@requires_dbus
class NoBoardTest(unittest.TestCase):
    def test_no_board_has_no_object(self):
        tmp = tempfile.mkdtemp(prefix="tide-noboard-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        service = PrivateBus("tide-noboard-test")
        self.addCleanup(service.stop)
        service.spawn_daemon("--claude-dir", os.path.join(tmp, "c"), "--codex-dir", os.path.join(tmp, "x"),
                             "--no-processes", "--workspace", tmp, "--no-board")
        reply = service.call(SERVICE, PATH, IFACE, "Snapshot")
        self.assertEqual(reply.type(), QDBusMessage.MessageType.ErrorMessage)


class PrintBoardTest(unittest.TestCase):
    def test_print_board_is_read_only(self):
        import sys
        from . import DAEMON_DIR
        tmp = tempfile.mkdtemp(prefix="tide-printboard-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        board = os.path.join(tmp, "tide-board")
        out = subprocess.run([sys.executable, os.path.join(DAEMON_DIR, "tide-daemon.py"), "--print-board",
                              "--board-dir", board, "--workspace", tmp, "--no-processes"],
                             capture_output=True, text=True, check=True, env=dict(os.environ, **git_config(tmp)))
        snap = json.loads(out.stdout)
        self.assertEqual((snap["boardDir"], snap["exists"], snap["cards"]), (board, False, []))
        self.assertEqual(snap["colors"]["teal"], "#6fd6c6")  # kolory z palety
        self.assertFalse(os.path.exists(board))


if __name__ == "__main__":
    unittest.main()
