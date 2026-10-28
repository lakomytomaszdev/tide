"""Lista „Praca” (PT-40, board/work.py): aktywne karty tablicy i żywe sesje bez karty — jedna prawda
dla kafelka Praca, liczona z tego samego kontekstu derive co kolumny kart.

Karty przechodzą przez prawdziwe derive (CardDeriver, jak w BoardMonitor._derived), sesje i koordynatorzy
to słowniki w kształcie /Agents i /Coordination. Na końcu BoardMonitor w procesie: Work() i Changed.
"""
import argparse
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from .gitrepos import commit, git
from .privatebus import app, wait_until
from .test_board_derive import NOW, REPO, FakeBranches, FakeSource, board, card, session, ticket
from tide_daemon.board import work
from tide_daemon.board.derive import CardDeriver

OTHER = "/srv/repos/sklep"  # repozytorium sesji bez karty


def compute(cards, sessions=(), boards=(), projects=()):
    """Jak BoardMonitor._derived + _apply: derived i boardColumn każdej karty, potem lista z tego kontekstu."""
    deriver = CardDeriver(lambda: list(sessions), lambda: list(boards), branches=FakeBranches(), clock=lambda: NOW)
    deriver.repo_of = lambda cwd: (os.path.realpath(REPO) if (cwd or "").startswith(REPO) else
                                   OTHER if (cwd or "").startswith(OTHER) else "")
    context = deriver.context([c.get("session") for c in cards if c.get("session")])
    shown = []
    for c in cards:
        derived = context(c)
        shown.append(dict(c, derived=derived, boardColumn=(derived or {}).get("column") or c.get("column")))
    return work.build(shown, context, projects)


def kinds(result):
    return [(i["group"], i["id"]) for i in result["items"]]


class WorkListTest(unittest.TestCase):
    def test_cards_and_sessions_in_groups(self):
        cards = [card(1, session="claude:czeka"), card(2, session="claude:pracuje"), card(3, column="todo"),
                 card(4, column="backlog"), card(5, column="done"), card(6, column="archived")]
        sessions = [session("claude:czeka", state="waiting", reason="question", waitingText="Czy taki podział?"),
                    session("claude:pracuje", state="working"),
                    session("codex:luzem", state="waiting", reason="reply", cwd=OTHER, waitingText="Mogę pushować?",
                            since=NOW - 900),
                    session("claude:luzem2", state="working", cwd=OTHER, branch="feat/pricing")]
        result = compute(cards, sessions)
        self.assertEqual(kinds(result), [("waiting", "K-1"), ("waiting", "codex:luzem"), ("progress", "K-2"),
                                         ("progress", "claude:luzem2"), ("todo", "K-3")])
        self.assertEqual(result["counts"], {"waiting": 2, "progress": 2, "review": 0, "todo": 1, "finished": 0,
                                            "total": 5, "sessions": 2})
        first = result["items"][0]
        self.assertEqual((first["kind"], first["title"], first["session"], first["question"]),
                         ("card", "Eksport do PDF", "claude:czeka", "Czy taki podział?"))
        loose = result["items"][1]
        self.assertEqual((loose["kind"], loose["card"], loose["question"], loose["nextStep"]),
                         ("session", "", "Mogę pushować?", "odpowiedz agentowi"))

    def test_one_session_driving_two_cards_gives_two_card_items(self):
        koord = board("claude:koord", [ticket("A-1", branch="feat/a"), ticket("A-2", branch="feat/b")],
                      state="working")
        cards = [card(1, branch="feat/a", session="claude:koord"), card(2, branch="feat/b", session="claude:koord")]
        sessions = [session("claude:koord", state="working", branch="master")]
        result = compute(cards, sessions, [koord])
        self.assertEqual(kinds(result), [("progress", "K-1"), ("progress", "K-2")],
                         "jedna sesja, dwie karty — bez dodatkowej pozycji sesji")
        self.assertEqual({i["session"] for i in result["items"]}, {"claude:koord"})
        self.assertEqual(result["counts"]["total"], 2)

    def test_link_found_by_derive_without_pinned_session_also_hides_the_session(self):
        # Karta bez `session`, połączona przez derive po gałęzi (reguła 5) — sesja nie wraca jako „bez karty”.
        cards = [card(7, branch="feat/pdf")]
        result = compute(cards, [session("claude:aaa", state="working", branch="feat/pdf")])
        self.assertEqual(kinds(result), [("progress", "K-7")])

    def test_session_pinned_to_a_done_card_is_not_loose(self):
        cards = [card(9, column="done", session="claude:stara")]
        result = compute(cards, [session("claude:stara", state="idle")])
        self.assertEqual(result["items"], [], "przypięta do karty w Gotowych — ma kartę")
        bare = compute([card(9, column="archived", session="stara")], [session("claude:stara", state="idle")])
        self.assertEqual(bare["items"], [], "samo id sesji w karcie też przypina")

    def test_session_without_card_gets_a_card_request(self):
        projects = [{"slug": "sklep", "name": "Sklep", "repo": OTHER}]
        result = compute([], [session("claude:luzem", state="waiting", reason="question", cwd=OTHER,
                                      title="Pricing: rabaty progowe", waitingText="Mogę?")], projects=projects)
        item = result["items"][0]
        self.assertEqual((item["kind"], item["project"], item["projectName"]), ("session", "sklep", "Sklep"))
        self.assertEqual(item["cardRequest"], {"title": "Pricing: rabaty progowe", "column": "todo",
                                               "session": "claude:luzem", "repo": OTHER})
        codex = compute([], [session("codex:x", state="working", cwd=OTHER, branch="feat/pricing")])["items"][0]
        self.assertEqual(codex["cardRequest"]["title"], "sklep · feat/pricing", "bez tytułu — projekt i gałąź")
        main = compute([], [session("codex:y", state="working", cwd="/tmp/x", branch="master")])["items"][0]
        self.assertEqual(main["cardRequest"], {"title": "x", "column": "todo", "session": "codex:y"},
                         "poza repozytorium: bez projektu, gałąź główna pomijana")
        orphan = compute([], [session("codex:pod", state="working", cwd=OTHER, subagent=True)])["items"][0]
        self.assertNotIn("cardRequest", orphan, "osierocony podagent nie dostaje „+ karta”")

    def test_done_and_idle_sessions_without_card_are_finished_and_not_counted(self):
        sessions = [session("claude:koniec", state="done", cwd=OTHER, since=NOW - 300),
                    session("claude:spi", state="idle", cwd=OTHER, since=NOW - 90_000),
                    session("claude:dawno", state="ended", cwd=OTHER)]
        result = compute([], sessions)
        self.assertEqual(kinds(result), [("finished", "claude:koniec"), ("finished", "claude:spi")],
                         "zakończonych nie ma, świeższa aktywność pierwsza")
        self.assertEqual((result["counts"]["finished"], result["counts"]["total"]), (2, 0))

    def test_waiting_sessions_longest_first(self):
        sessions = [session("claude:nowa", state="waiting", reason="approval", cwd=OTHER, since=NOW - 60),
                    session("claude:stara", state="waiting", reason="approval", cwd=OTHER, since=NOW - 3600)]
        self.assertEqual([i["id"] for i in compute([], sessions)["items"]], ["claude:stara", "claude:nowa"])

    def test_card_fields_for_tile_texts(self):
        cards = [card(17, session="claude:spi"), card(26, session="claude:koord2")]
        koord = board("claude:koord2", [ticket("B-1", "gotowe", branch="feat/raport")], state="idle",
                      branch="feat/raport", live=False, activity=NOW - 100)
        sessions = [session("claude:spi", state="idle", since=NOW - 90_000)]
        result = compute(cards, sessions, [koord])
        idle, review = result["items"]
        self.assertEqual((idle["group"], idle["agentState"], idle["sinceTs"]), ("progress", "idle", NOW - 90_000))
        self.assertEqual((review["group"], review["total"], review["done"]), ("review", 1, 1))
        self.assertIn("do scalenia: feat/raport", review["note"])
        self.assertEqual(review["sinceTs"], NOW - 100, "koordynator bez żywej sesji — czas z jego tablicy")
        todo = compute([card(3, column="todo")])["items"][0]
        self.assertEqual((todo["session"], todo["agentState"], todo["canActivate"]), ("", "", False))

    def test_without_context_cards_only(self):
        shown = [dict(card(1), boardColumn="todo", derived=None), dict(card(2), boardColumn="backlog", derived=None)]
        result = work.build(shown, None)
        self.assertEqual(kinds(result), [("todo", "K-1")])
        self.assertEqual(work.build([], None, exists=False), {
            "exists": False, "items": [],
            "counts": {"waiting": 0, "progress": 0, "review": 0, "todo": 0, "finished": 0, "total": 0, "sessions": 0}})


class WorkLiveTest(unittest.TestCase):
    """BoardMonitor w procesie: Work() idzie za sesjami, a zmiana samej sesji bez karty daje Changed."""

    def setUp(self):
        app()
        self.tmp = tempfile.mkdtemp(prefix="tide-work-live-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        config = os.path.join(self.tmp, "gitconfig")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write("[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": config, "GIT_CONFIG_NOSYSTEM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = os.path.join(self.tmp, "dom")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "master")
        commit(self.repo, "README.md")
        self.agents = FakeSource("sessions")
        self.coordination = FakeSource("boards")

    def start(self):
        from tide_daemon.app import start_board

        class Trees:
            def __init__(self, signal_owner):
                self.changed = signal_owner.changed
                self.scanner = mock.Mock(snapshot=lambda *a: [])
                self.launchers = None

        self.trees_source = FakeSource("items")
        monitor = start_board(None, argparse.Namespace(board_dir=os.path.join(self.tmp, "board")),
                              Trees(self.trees_source), self.agents, self.coordination, clock=lambda: NOW)
        monitor._rederive.setInterval(50)
        monitor._debounce.setInterval(60_000)
        self.addCleanup(monitor.stop)
        monitor.start()
        return monitor

    def test_loose_session_becomes_a_card_through_card_request(self):
        monitor = self.start()
        self.assertEqual(json.loads(monitor.work_json())["items"], [])
        changes = []
        monitor.changed.connect(lambda: changes.append(True))
        self.agents.sessions = [session("claude:luzem", state="waiting", reason="question", cwd=self.repo,
                                        title="Pricing", waitingText="Mogę?")]
        self.agents.changed.emit()
        self.assertTrue(wait_until(lambda: changes, timeout=3), "sesja bez karty zmienia listę — Changed")
        listed = json.loads(monitor.work_json())
        self.assertEqual(kinds(listed), [("waiting", "claude:luzem")])
        request = listed["items"][0]["cardRequest"]
        self.assertEqual(request["repo"], os.path.realpath(self.repo))

        reply = json.loads(monitor.create_card(json.dumps(request)))
        self.assertTrue(reply["ok"], reply)
        listed = json.loads(monitor.work_json())
        self.assertEqual(kinds(listed), [("waiting", reply["id"])], "po „+ karta” sesja jest kartą, nie luzem")
        self.assertEqual(listed["items"][0]["question"], "Mogę?")
        snap = json.loads(monitor.snapshot_json())
        self.assertEqual(snap["cards"][0]["boardColumn"], "waiting", "Tablica pokazuje to samo")
        self.assertEqual(snap["projects"][0]["repo"], os.path.realpath(self.repo), "projekt z repozytorium sesji")


if __name__ == "__main__":
    unittest.main()
