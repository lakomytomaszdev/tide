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

    def test_waiting_session_of_a_done_card_stays_in_waiting(self):
        """Review M1 (b): sesję karty z Gotowych wznowiono i prosi o zgodę — czeka, więc jest w „Czeka”."""
        cards = [card(9, column="done", session="claude:stara")]
        result = compute(cards, [session("claude:stara", state="waiting", reason="approval",
                                         lastAction="Bash · git push", waitingText="Bash · git push")])
        self.assertEqual(kinds(result), [("waiting", "claude:stara")])
        item = result["items"][0]
        self.assertEqual((item["card"], item["note"], item["question"]), ("K-9", "karta K-9 w Gotowych", "Bash · git push"))
        self.assertNotIn("cardRequest", item, "ma kartę — bez „+ karta”")
        self.assertEqual((result["counts"]["waiting"], result["counts"]["sessions"]), (1, 0))

    def test_coordinator_asking_outside_its_card_stays_in_waiting(self):
        """Review M1 (a): koordynator pyta o ticket bez karty — karta K-1 jest W toku, a sesja i tak „czeka”."""
        koord = board("claude:koord", [ticket("A-1", branch="feat/a"), ticket("A-2", "czeka", branch="feat/b",
                                                                          question="Który format?")], state="waiting")
        cards = [card(1, branch="feat/a", session="claude:koord")]
        sessions = [session("claude:koord", state="waiting", reason="question", branch="master",
                            waitingText="Który format?")]
        result = compute(cards, sessions, [koord])
        self.assertEqual(kinds(result), [("waiting", "claude:koord"), ("progress", "K-1")])
        self.assertEqual((result["items"][0]["card"], result["items"][0]["note"]), ("K-1", "karta K-1 w toku"))

    def test_every_waiting_session_is_in_the_waiting_group(self):
        """Niezmiennik: każda czekająca sesja z /Agents jest w grupie „Czeka” — kartą albo sama."""
        koord = board("claude:koord", [ticket("A-1", branch="feat/a"), ticket("A-2", "czeka", branch="feat/b",
                                                                          question="Który?")], state="waiting")
        scenarios = [
            ([card(1, session="claude:a")], [session("claude:a", state="waiting", reason="question")], []),
            ([card(1, column="done", session="claude:a")], [session("claude:a", state="waiting", reason="approval")], []),
            ([card(1, column="backlog", session="claude:a")], [session("claude:a", state="waiting", reason="reply")], []),
            ([card(1, column="archived", session="a")], [session("claude:a", state="waiting", reason="reply")], []),
            ([card(1, branch="feat/a", session="claude:koord"), card(2, branch="feat/b", session="claude:koord")],
             [session("claude:koord", state="waiting", reason="question", branch="master")], [koord]),
            ([card(1, branch="feat/a", session="claude:koord")],
             [session("claude:koord", state="waiting", reason="question", branch="master")], [koord]),
            ([], [session("codex:x", state="waiting", reason="approval", cwd=OTHER),
                  session("claude:y", state="working", cwd=OTHER)], []),
        ]
        for cards, sessions, boards in scenarios:
            with self.subTest(cards=[(c["id"], c["column"]) for c in cards]):
                result = compute(cards, sessions, boards)
                shown = {i["session"] for i in result["items"] if i["group"] == "waiting"}
                waiting = {s["id"] for s in sessions if s["state"] == "waiting"}
                self.assertLessEqual(waiting, shown)
                self.assertGreaterEqual(result["counts"]["waiting"], len(waiting))

    def test_question_belongs_to_the_card(self):
        """Review m4: dwie karty jednego koordynatora — pytanie tylko przy karcie, której ticket pyta."""
        koord = board("claude:koord", [ticket("A-1", "czeka", branch="feat/a", question="Format daty?"),
                                       ticket("A-2", "czeka", branch="feat/b", planNote="decyzja o nazwie")],
                      state="idle")
        cards = [card(1, branch="feat/a", session="claude:koord"), card(2, branch="feat/b", session="claude:koord")]
        result = compute(cards, [], [koord])
        by_id = {i["id"]: i for i in result["items"]}
        self.assertEqual((by_id["K-1"]["group"], by_id["K-1"]["question"]), ("waiting", "Format daty?"))
        self.assertEqual((by_id["K-2"]["group"], by_id["K-2"]["question"]), ("waiting", ""))
        self.assertIn("według planu", by_id["K-2"]["note"])

    def test_session_without_card_gets_a_card_request(self):
        projects = [{"slug": "sklep", "name": "Sklep", "repo": OTHER}]
        result = compute([], [session("claude:luzem", state="waiting", reason="question", cwd=OTHER,
                                      title="Pricing: rabaty progowe", waitingText="Mogę?")], projects=projects)
        item = result["items"][0]
        self.assertEqual((item["kind"], item["project"], item["projectName"]), ("session", "sklep", "Sklep"))
        self.assertEqual(item["cardRequest"], {"title": "Pricing: rabaty progowe", "column": "todo",
                                               "session": "claude:luzem", "project": "sklep"})
        unknown = compute([], [session("claude:luzem", state="working", cwd=OTHER, title="X")])["items"][0]
        self.assertEqual(unknown["cardRequest"], {"title": "X", "column": "todo", "session": "claude:luzem"},
                         "review m2: repozytorium bez projektu — karta bez projektu, bez cichego zakładania")
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

    def test_flow_card_is_one_row_with_running_step_count(self):
        shown = dict(card(12, column="progress"), boardColumn="progress", derived={
            "column": "progress", "note": "PT-12 · sprawdzanie zmian", "total": 2, "done": 0,
            "flow": {"steps": [
                {"id": "a", "result": "", "mode": "background"},
                {"id": "b", "result": "", "mode": "background"},
                {"id": "c", "result": "done", "mode": "background"},
            ]},
        })
        result = work.build([shown])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["runningSteps"], 2)
        self.assertIn("2 kroki w toku", result["items"][0]["note"])

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
        monitor._work_later.setInterval(200)
        self.assertEqual(json.loads(monitor.work_json())["items"], [])
        changes, work_changes = [], []
        monitor.changed.connect(lambda: changes.append(True))
        monitor.work_changed.connect(lambda: work_changes.append(True))
        self.agents.sessions = [session("claude:luzem", state="waiting", reason="question", cwd=self.repo,
                                        title="Pricing", waitingText="Mogę?")]
        self.agents.changed.emit()
        self.assertTrue(wait_until(lambda: work_changes, timeout=3), "sesja bez karty zmienia listę — WorkChanged")
        self.assertEqual(changes, [], "migawka tablicy się nie zmieniła — bez Changed (review M2)")
        listed = json.loads(monitor.work_json())
        self.assertEqual(kinds(listed), [("waiting", "claude:luzem")])
        request = listed["items"][0]["cardRequest"]
        self.assertEqual(request, {"title": "Pricing", "column": "todo", "session": "claude:luzem"},
                         "repozytorium nie jest projektem — karta bez projektu (review m2)")

        reply = json.loads(monitor.create_card(json.dumps(request)))
        self.assertTrue(reply["ok"], reply)
        again = json.loads(monitor.create_card(json.dumps(request)))
        self.assertEqual((again["id"], again.get("existing")), (reply["id"], True),
                         "drugie „+ karta” oddaje istniejącą kartę (review m1)")
        listed = json.loads(monitor.work_json())
        self.assertEqual(kinds(listed), [("waiting", reply["id"])], "po „+ karta” sesja jest kartą, nie luzem")
        self.assertEqual(listed["items"][0]["question"], "Mogę?")
        snap = json.loads(monitor.snapshot_json())
        self.assertEqual(len(snap["cards"]), 1)
        self.assertEqual((snap["cards"][0]["boardColumn"], snap["cards"][0]["project"]), ("waiting", ""))

    def test_last_action_alone_is_signalled_later(self):
        """Review M2: sama ostatnia czynność agenta nie daje WorkChanged od razu — najwyżej co WORK_VOLATILE_MS."""
        monitor = self.start()
        monitor._work_later.setInterval(1000)
        self.agents.sessions = [session("claude:luzem", state="working", cwd=self.repo, lastAction="Edit · a.py")]
        self.agents.changed.emit()
        self.assertTrue(wait_until(lambda: kinds(json.loads(monitor.work_json())) == [("progress", "claude:luzem")],
                                   timeout=3))
        work_changes = []
        monitor.work_changed.connect(lambda: work_changes.append(True))
        for name in ("b.py", "c.py", "d.py"):
            self.agents.sessions = [session("claude:luzem", state="working", cwd=self.repo, lastAction=f"Edit · {name}")]
            self.agents.changed.emit()
            wait_until(lambda: False, timeout=0.1)
        self.assertEqual(work_changes, [], "w oknie zmian ostatniej czynności — bez sygnału")
        self.assertTrue(wait_until(lambda: json.loads(monitor.work_json())["items"][0]["note"] == "Edit · d.py",
                                   timeout=0.3), "Work() zawsze świeże, zanim przyjdzie sygnał")
        self.assertTrue(wait_until(lambda: work_changes, timeout=3))
        self.assertEqual(len(work_changes), 1, "jeden zbiorczy sygnał")

    def test_work_failure_never_breaks_the_board(self):
        """Review m3: wyjątek w liście „Praca” — tablica działa, Work() to pusta lista."""
        monitor = self.start()
        with mock.patch("tide_daemon.board.work.build", side_effect=RuntimeError("x")):
            monitor.create_card(json.dumps({"title": "PDF", "column": "todo"}))
            self.assertEqual(len(json.loads(monitor.snapshot_json())["cards"]), 1)
            self.assertEqual(json.loads(monitor.work_json())["items"], [])


if __name__ == "__main__":
    unittest.main()
