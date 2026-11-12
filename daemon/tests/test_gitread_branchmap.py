"""Mapa gałęzi jako dane (`Git.BranchMap`, K-99, PT-387): projekcja dzienników kart i Tablicy bez gita.

Dzienniki mają kształt zdarzeń zapisywanych przez silnik przepływu (`card.started` z bazą, pary zamiar/wynik
`card-branch`, `ticket-branch`, `merge`, `ticket.rejected`, `card.finished`, `card.merged`, `card.main-merged`,
`card.base-changed`); test przez D-Bus z prawdziwym startem „Od razu” jest w `test_git_dbus.py`."""
import json
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from tide_daemon.cardflow.journal import Journal
from tide_daemon.gitread import branchmap

REPO = "/srv/repo/proj"
SHA = {name: f"{n:040x}" for n, name in enumerate(
    ("master", "main", "develop", "origin-develop", "origin-main", "card", "ticket", "merge", "result", "other"), 1)}
T0 = datetime(2026, 11, 12, 9, 0, tzinfo=timezone.utc).timestamp()


class Card:
    """Dziennik jednej karty zapisywany jednym plikiem kopii usługi (kształt zdarzeń jak w `engine.py`)."""

    def __init__(self, root: Path, card: str, clock):
        self.root, self.card, self.clock, self.events = root, card, clock, []

    def add(self, event_type: str, /, **data) -> "Card":
        self.events.append({"v": 1, "seq": len(self.events) + 1, "ts": self.clock(), "card": self.card,
                            "type": event_type, **data})
        return self

    def start(self, base_ref="refs/heads/master", sha=SHA["master"], *, repo=REPO, project="proj",
              branch=None, known=True) -> "Card":
        branch = branch or f"card/{self.card.lower()}-x"
        prefix = "refs/remotes/" if base_ref.startswith("refs/remotes/") else "refs/heads/"
        base = {"baseRef": base_ref, "baseBranch": base_ref[len(prefix):], "baseCommit": sha} if known else {}
        self.add("card.started", entry="now", executor="Sonnet", repo=repo, project=project, column="todo",
                 baseVersion=1, **base)
        self.add("intent", action="card-branch", key=branch, repo=repo, branch=branch, worktree=f"{repo}-wt/{branch}",
                 base=sha if known else "", **({"baseRef": base_ref, "baseBranch": base["baseBranch"]} if known else {}))
        self.add("result", action="card-branch", key=branch, ok=True, branch=branch, worktree=f"{repo}-wt/{branch}",
                 base=sha, existing=False)
        self.branch = branch
        return self

    def ticket(self, ticket: str, title="", *, reserve=True) -> "Card":
        branch = f"ticket/{ticket.lower()}-x"
        if reserve:
            self.add("intent", action="reserve", key=f"launch-{ticket}", repo=REPO, title=title)
            self.add("result", action="reserve", key=f"launch-{ticket}", ok=True, ticket=ticket, title=title)
        self.add("intent", action="ticket-branch", key=ticket, repo=REPO, branch=branch,
                 worktree=f"{REPO}-wt/{branch}", base=f"refs/heads/{self.branch}", ticket=ticket)
        self.add("result", action="ticket-branch", key=ticket, ok=True, branch=branch, worktree=f"{REPO}-wt/{branch}",
                 base=SHA["ticket"], existing=False, ticket=ticket)
        self.add("step.started", step=f"{self.card}-{ticket}-1", kind="ticket", ticket=ticket, executor="Sonnet",
                 branch=branch)
        self.add("step.finished", step=f"{self.card}-{ticket}-1", result="done", notes=["note.md"])
        return self

    def merge_ticket(self, ticket: str) -> "Card":
        branch = f"ticket/{ticket.lower()}-x"
        self.add("intent", action="merge", key=ticket, branch=branch, into=self.branch, expectedTip=SHA["ticket"],
                 cardTip=SHA["card"])
        self.add("result", action="merge", key=ticket, ok=True, commit=SHA["merge"], already=False)
        return self

    def reject(self, ticket: str) -> "Card":
        return self.add("ticket.rejected", ticket=ticket)

    def finish(self) -> "Card":
        return self.add("card.finished", column="done")

    def merged(self, target="refs/heads/master") -> "Card":
        return self.add("card.merged", operationId="op-1", targetRef=target, targetBranch=target.split("/", 2)[2],
                        sourceCommit=SHA["card"], targetCommitBefore=SHA["master"], resultCommit=SHA["result"],
                        baseVersion=1)

    def save(self) -> "Card":
        mirror = self.root / "state"
        mirror.mkdir(parents=True, exist_ok=True)
        (mirror / f"{self.card}.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self.events), encoding="utf-8")
        return self


class BranchMapCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.journal = Journal(str(self.root / "board"), mirror=str(self.root / "state"))
        self.now = T0
        self.board = {"projects": [{"slug": "proj", "name": "Projekt", "repo": REPO},
                                   {"slug": "inny", "name": "Inny", "repo": "/srv/repo/inny"},
                                   {"slug": "notatki", "name": "Notatki", "repo": ""}],
                      "cards": {}}

    def clock(self) -> str:
        self.now += 60
        return datetime.fromtimestamp(self.now, timezone.utc).astimezone().isoformat(timespec="microseconds")

    def card(self, card: str, title="", column="todo", project="proj") -> Card:
        self.board["cards"][card] = {"project": project, "title": title or f"Karta {card}", "column": column}
        return Card(self.root, card, self.clock)

    def ask(self, **request) -> dict:
        return branchmap.request(self.journal, self.board, json.dumps(request))

    def node(self, data, node_id) -> dict:
        found = [n for n in data["nodes"] if n["id"] == node_id]
        self.assertEqual(len(found), 1, f"{node_id}: {[n['id'] for n in data['nodes']]}")
        return found[0]

    def parent(self, data, node_id, kind="start") -> str:
        found = [e["from"] for e in data["edges"] if e["to"] == node_id and e["kind"] == kind]
        self.assertEqual(len(found), 1, f"{node_id}: {data['edges']}")
        return found[0]

    def target(self, data, node_id, kind="merge") -> str:
        found = [e["to"] for e in data["edges"] if e["from"] == node_id and e["kind"] == kind]
        self.assertEqual(len(found), 1, f"{node_id}: {data['edges']}")
        return found[0]


class ProjectsTest(BranchMapCase):
    def test_empty_request_lists_board_projects_with_repository(self):
        self.card("K-1")
        self.card("K-2")
        self.card("K-3", project="inny")
        reply = self.ask()
        self.assertEqual(reply, {"ok": True, "projects": [
            {"slug": "inny", "name": "Inny", "repo": "/srv/repo/inny", "cards": 1},
            {"slug": "proj", "name": "Projekt", "repo": REPO, "cards": 2}]})

    def test_board_view_copies_projects_and_cards_from_the_board(self):
        from tide_daemon.board.model import Card as BoardCard, Project
        from tide_daemon.board.store import Board
        board = Board()
        board.projects["proj"] = Project(slug="proj", name="Projekt", repo=REPO)
        board.cards[7] = BoardCard(num=7, title="Mapa", project="proj", column="todo")
        self.assertEqual(branchmap.board_view(board), {
            "projects": [{"slug": "proj", "name": "Projekt", "repo": REPO}],
            "cards": {"K-7": {"project": "proj", "title": "Mapa", "column": "todo"}}})


class ShapeTest(BranchMapCase):
    def build(self):
        # K-1 w toku: ticket w pracy i ticket scalony do gałęzi karty.
        self.card("K-1", "Mapa gałęzi").start().ticket("PT-11", "Pierwszy").merge_ticket("PT-11") \
            .ticket("PT-12", "Drugi").save()
        # K-2 domknięta (card.finished), ticket scalony.
        self.card("K-2", column="done").start().ticket("PT-21").merge_ticket("PT-21").finish().save()
        # K-3 scalona do master.
        self.card("K-3").start().ticket("PT-31").merge_ticket("PT-31").finish().merged().save()
        # K-4 z odrzuconym ticketem.
        self.card("K-4").start().ticket("PT-41").reject("PT-41").save()
        return self.ask(project="proj")

    def test_nodes_and_edges_of_cards_in_different_states(self):
        data = self.build()
        self.assertTrue(data["ok"], data)
        self.assertEqual((data["project"], data["repo"], data["total"], data["truncated"], data["warnings"]),
                         ("proj", REPO, 9, False, []))
        self.assertNotIn("mainRef", data)
        card = self.node(data, "card:K-1")
        self.assertEqual(card, {
            "id": "card:K-1", "kind": "card", "ref": "refs/heads/card/k-1-x", "branch": "card/k-1-x", "card": "K-1",
            "title": "Mapa gałęzi", "column": "todo", "state": "active",
            "started": {"ref": "refs/heads/master", "sha": SHA["master"], "date": card["started"]["date"]}})
        self.assertTrue(card["started"]["date"].startswith("2026-11-12"))
        merged = self.node(data, "ticket:K-1:PT-11")
        self.assertEqual({k: merged[k] for k in ("kind", "ref", "branch", "card", "ticket", "title", "state")},
                         {"kind": "ticket", "ref": "refs/heads/ticket/pt-11-x", "branch": "ticket/pt-11-x",
                          "card": "K-1", "ticket": "PT-11", "title": "Pierwszy", "state": "merged"})
        self.assertEqual(merged["started"]["ref"], "refs/heads/card/k-1-x")
        self.assertEqual(merged["started"]["sha"], SHA["ticket"])
        self.assertEqual((merged["merged"]["targetRef"], merged["merged"]["sha"]),
                         ("refs/heads/card/k-1-x", SHA["merge"]))
        self.assertEqual(self.node(data, "ticket:K-1:PT-12")["state"], "active")
        self.assertNotIn("merged", self.node(data, "ticket:K-1:PT-12"))
        self.assertEqual(self.node(data, "card:K-2")["state"], "closed")
        self.assertEqual(self.node(data, "ticket:K-2:PT-21")["state"], "merged")
        done = self.node(data, "card:K-3")
        self.assertEqual(done["state"], "merged")
        self.assertEqual((done["merged"]["targetRef"], done["merged"]["sha"]), ("refs/heads/master", SHA["result"]))
        self.assertEqual(self.node(data, "ticket:K-4:PT-41")["state"], "rejected")
        self.assertEqual(self.node(data, "base:refs/heads/master"),
                         {"id": "base:refs/heads/master", "kind": "base", "ref": "refs/heads/master",
                          "branch": "master", "state": "active"})
        for card in ("K-1", "K-2", "K-3", "K-4"):
            self.assertEqual(self.parent(data, f"card:{card}"), "base:refs/heads/master")
        self.assertEqual(self.parent(data, "ticket:K-1:PT-12"), "card:K-1")
        self.assertEqual(self.target(data, "ticket:K-1:PT-11"), "card:K-1")
        self.assertEqual(self.target(data, "card:K-3"), "base:refs/heads/master")
        self.assertEqual(self.parent(data, "base:refs/heads/master", "merge"), "card:K-3")
        self.assertEqual([e for e in data["edges"] if e["from"] == "ticket:K-4:PT-41"], [])
        kinds = {n["kind"] for n in data["nodes"]}
        self.assertEqual(kinds, {"base", "card", "ticket"})
        ids = [n["id"] for n in data["nodes"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(e["from"] in ids and e["to"] in ids for e in data["edges"]))

    def test_cards_of_other_projects_and_without_branch_are_not_on_the_map(self):
        self.card("K-5", project="inny").start(repo="/srv/repo/inny", project="inny").save()
        c = self.card("K-6")
        c.add("card.started", entry="now", repo=REPO, project="proj").save()  # bez wyniku `card-branch`
        self.card("K-7").add("card.moved", **{"from": "backlog", "to": "todo"}).save()  # bez zlecenia
        data = self.ask(project="proj")
        self.assertEqual((data["nodes"], data["edges"], data["total"]), ([], [], 0))
        self.assertEqual([n["id"] for n in self.ask(project="inny")["nodes"]],
                         ["base:refs/heads/master", "card:K-5"])

    def test_card_journal_without_board_card_has_no_title_and_column(self):
        Card(self.root, "K-8", self.clock).start().save()
        node = self.node(self.ask(project="proj"), "card:K-8")
        self.assertNotIn("title", node)
        self.assertNotIn("column", node)

    def test_old_main_merged_event_is_read_through_merge_of(self):
        self.card("K-9").start(base_ref="refs/heads/main", sha=SHA["main"]).finish() \
            .add("card.main-merged", main="main", commit=SHA["result"]).save()
        data = self.ask(project="proj")
        node = self.node(data, "card:K-9")
        self.assertEqual((node["state"], node["merged"]["targetRef"], node["merged"]["sha"]),
                         ("merged", "refs/heads/main", SHA["result"]))
        self.assertEqual(self.parent(data, "base:refs/heads/main", "merge"), "card:K-9")


class TreeTest(BranchMapCase):
    def test_card_started_from_another_card_branch_hangs_under_that_card(self):
        parent = self.card("K-1").start().save()
        self.card("K-2").start(base_ref=f"refs/heads/{parent.branch}", sha=SHA["card"]).save()
        data = self.ask(project="proj")
        self.assertEqual(self.parent(data, "card:K-2"), "card:K-1")
        self.assertEqual([n["id"] for n in data["nodes"] if n["kind"] == "base"], ["base:refs/heads/master"])

    def test_card_after_base_change_hangs_under_new_base_with_initial_in_started(self):
        c = self.card("K-1").start()
        c.add("card.base-changed", previousBaseVersion=1, baseVersion=2,
              base={"baseRef": "refs/heads/develop", "baseBranch": "develop", "baseCommit": SHA["develop"]}).save()
        data = self.ask(project="proj")
        node = self.node(data, "card:K-1")
        self.assertEqual((node["started"]["ref"], node["started"]["sha"]), ("refs/heads/develop", SHA["develop"]))
        self.assertEqual(node["started"]["initial"], {"ref": "refs/heads/master", "sha": SHA["master"]})
        self.assertEqual(self.parent(data, "card:K-1"), "base:refs/heads/develop")
        self.assertEqual([n["ref"] for n in data["nodes"] if n["kind"] == "base"], ["refs/heads/develop"])

    def test_ineffective_base_change_keeps_the_card_under_its_base(self):
        c = self.card("K-1").start()
        c.add("card.base-changed", previousBaseVersion=5, baseVersion=6,  # zła wersja: fold go nie przyjmuje
              base={"baseRef": "refs/heads/develop", "baseBranch": "develop", "baseCommit": SHA["develop"]}).save()
        node = self.node(self.ask(project="proj"), "card:K-1")
        self.assertEqual(node["started"]["ref"], "refs/heads/master")
        self.assertNotIn("initial", node["started"])

    def test_card_with_unknown_base_is_in_unknown_base_group(self):
        self.card("K-1").start(known=False).save()
        self.card("K-2").start(known=False).save()
        data = self.ask(project="proj")
        group = self.node(data, "unknown-base")
        self.assertEqual((group["kind"], group["ref"]), ("unknown-base", ""))
        self.assertEqual(self.parent(data, "card:K-1"), "unknown-base")
        self.assertEqual(self.parent(data, "card:K-2"), "unknown-base")
        self.assertEqual(self.node(data, "card:K-1")["started"]["ref"], "")
        self.assertEqual([n for n in data["nodes"] if n["kind"] == "base"], [])

    def test_merged_ticket_has_merge_edge_without_its_branch_in_git(self):
        # Moduł nie pyta gita: gałąź ticketu mogła zostać usunięta po scaleniu, krawędź pochodzi z dziennika.
        self.card("K-1").start().ticket("PT-1").merge_ticket("PT-1").save()
        data = self.ask(project="proj")
        self.assertEqual(self.target(data, "ticket:K-1:PT-1"), "card:K-1")
        self.assertEqual(data["edges"].count({"kind": "merge", "from": "ticket:K-1:PT-1", "to": "card:K-1"}), 1)

    def test_bases_main_develop_and_remote_are_nodes_and_absent_master_is_not(self):
        self.card("K-1").start(base_ref="refs/heads/main", sha=SHA["main"]).save()
        self.card("K-2").start(base_ref="refs/heads/develop", sha=SHA["develop"]).save()
        self.card("K-3").start(base_ref="refs/remotes/origin/develop", sha=SHA["origin-develop"]).save()
        self.card("K-4").start(base_ref="refs/heads/develop", sha=SHA["develop"]) \
            .merged("refs/remotes/origin/main").save()
        data = self.ask(project="proj")
        bases = {n["ref"]: n for n in data["nodes"] if n["kind"] == "base"}
        self.assertEqual(sorted(bases), ["refs/heads/develop", "refs/heads/main", "refs/remotes/origin/develop",
                                         "refs/remotes/origin/main"])
        self.assertEqual(bases["refs/remotes/origin/develop"]["branch"], "origin/develop")
        self.assertEqual(self.parent(data, "card:K-1"), "base:refs/heads/main")
        self.assertEqual(self.parent(data, "card:K-2"), "base:refs/heads/develop")
        self.assertEqual(self.parent(data, "card:K-3"), "base:refs/remotes/origin/develop")
        self.assertEqual(self.parent(data, "base:refs/remotes/origin/main", "merge"), "card:K-4")
        self.assertFalse(any("master" in n["ref"] for n in data["nodes"]))
        self.assertNotIn("mainRef", data)
        self.assertNotIn("mainRef", json.dumps(data))


class LimitTest(BranchMapCase):
    def big_card(self, card: str, tickets: int) -> Card:
        c = self.card(card).start()
        for n in range(tickets):
            c.ticket(f"PT-{card_num(card)}{n:03d}", reserve=False)
        return c.save()

    def test_more_branches_than_limit_return_newest_and_full_total(self):
        for n in range(1, 6):
            self.card(f"K-{n}").start().ticket(f"PT-{n}0").save()  # K-5 najnowsza
        data = self.ask(project="proj", limit=5)
        self.assertEqual((data["total"], data["truncated"]), (10, True))
        branches = [n["id"] for n in data["nodes"] if n["kind"] in ("card", "ticket")]
        self.assertEqual(branches, ["card:K-5", "ticket:K-5:PT-50", "card:K-4", "ticket:K-4:PT-40", "card:K-3"])
        ids = {n["id"] for n in data["nodes"]}
        self.assertTrue(all(e["from"] in ids and e["to"] in ids for e in data["edges"]))
        full = self.ask(project="proj", limit=10)
        self.assertEqual((full["total"], full["truncated"]), (10, False))

    def test_newer_ticket_moves_its_card_to_the_front(self):
        old = self.card("K-1").start()
        self.card("K-2").start().save()
        old.ticket("PT-1").save()  # zdarzenie ticketu po starcie K-2
        data = self.ask(project="proj", limit=2)
        self.assertEqual([n["id"] for n in data["nodes"] if n["kind"] != "base"], ["card:K-1", "ticket:K-1:PT-1"])

    def test_default_limit_is_200_and_limit_over_500_is_cut_to_500(self):
        self.big_card("K-1", 260)
        self.big_card("K-2", 260)
        default = self.ask(project="proj")
        self.assertEqual((default["total"], default["truncated"]), (522, True))
        self.assertEqual(sum(n["kind"] in ("card", "ticket") for n in default["nodes"]), 200)
        capped = self.ask(project="proj", limit=10000)
        self.assertEqual(sum(n["kind"] in ("card", "ticket") for n in capped["nodes"]), 500)
        self.assertTrue(capped["truncated"])
        self.assertEqual(branchmap.DEFAULT_LIMIT, 200)
        self.assertEqual(branchmap.MAX_LIMIT, 500)


def card_num(card: str) -> int:
    return int(card.split("-")[1])


class ErrorTest(BranchMapCase):
    def test_bad_requests_unknown_project_and_project_without_repo(self):
        for text in ("nie json", "[]", "null", json.dumps({"project": 5}), json.dumps({"project": ""}),
                     json.dumps({"project": "proj", "limit": 0}), json.dumps({"project": "proj", "limit": "5"}),
                     json.dumps({"project": "proj", "limit": True})):
            reply = branchmap.request(self.journal, self.board, text)
            self.assertEqual((reply["ok"], reply["error"]), (False, "bad-request"), text)
            self.assertTrue(reply["message"])
        self.assertEqual(self.ask(project="brak")["error"], "unknown-project")
        self.assertEqual(self.ask(project="notatki")["error"], "no-repo")
        self.assertEqual(branchmap.request(self.journal, None, "{}")["error"], "unavailable")
        self.assertEqual(branchmap.request(None, self.board, json.dumps({"project": "proj"}))["error"], "unavailable")

    def test_unexpected_exception_is_a_generic_failure_without_its_text(self):
        with mock.patch.object(branchmap, "branch_map", side_effect=RuntimeError("tajne /ścieżka")), \
                mock.patch("builtins.print"):
            reply = self.ask(project="proj")
        self.assertEqual((reply["ok"], reply["error"]), (False, "failed"))
        self.assertNotIn("tajne", reply["message"])
        self.assertNotIn("RuntimeError", reply["message"])

    def test_broken_journal_of_one_card_is_a_warning(self):
        self.card("K-1").start().save()
        self.card("K-2").start().save()
        real = self.journal.state

        def state(card):
            if card == "K-2":
                raise ValueError("zepsuty")
            return real(card)

        with mock.patch.object(self.journal, "state", side_effect=state):
            data = self.ask(project="proj")
        self.assertEqual([n["id"] for n in data["nodes"] if n["kind"] == "card"], ["card:K-1"])
        self.assertEqual(len(data["warnings"]), 1)
        self.assertIn("K-2", data["warnings"][0])


class ReadOnlyTest(BranchMapCase):
    def test_projection_writes_neither_journal_nor_board(self):
        self.card("K-1").start().ticket("PT-1").merge_ticket("PT-1").save()
        files = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        board = json.dumps(self.board, sort_keys=True)
        with mock.patch.object(Journal, "append", side_effect=AssertionError("zapis dziennika")), \
                mock.patch.object(Journal, "write_file", side_effect=AssertionError("zapis pliku karty")):
            self.assertTrue(self.ask(project="proj")["ok"])
            self.assertTrue(self.ask()["ok"])
        self.assertEqual({p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, files)
        self.assertEqual(json.dumps(self.board, sort_keys=True), board)


class RecordingReader:
    """Atrapa runnera odczytu: pula wątków jak `GitReader.submit`, każde polecenie gita jest zapisywane."""

    def __init__(self):
        self.calls = []
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tide-gitread")

    def submit(self, fn, *args, **kwargs):
        return self._pool.submit(fn, self, *args, **kwargs)

    def run(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("mapa gałęzi nie może uruchamiać gita")

    def close(self):
        self._pool.shutdown(wait=True, cancel_futures=True)


def wait_for(replies, count=1, timeout=3.0):
    from .privatebus import app
    deadline = time.monotonic() + timeout
    while len(replies) < count and time.monotonic() < deadline:
        app().processEvents()
        time.sleep(.01)


class MonitorTest(BranchMapCase):
    def setUp(self):
        super().setUp()
        from .privatebus import app
        app()

    def test_reply_comes_later_from_the_reader_pool(self):
        from tide_daemon.git_monitor import GitMonitor
        entered, release = threading.Event(), threading.Event()
        seen, boards = [], []

        def slow(reader, journal, board, text):
            seen.append((threading.current_thread().name, journal, board, text))
            entered.set()
            release.wait(5)
            return {"ok": True, "echo": text}

        def board_source():
            boards.append(threading.current_thread() is threading.main_thread())
            return {"tablica": 1}

        monitor = GitMonitor(lambda: "dziennik", board_source=board_source, map_compute=slow)
        self.addCleanup(monitor.stop)
        replies = []
        began = time.monotonic()
        monitor.branch_map('{"project":"proj"}', lambda data: replies.append(json.loads(data)))
        self.assertLess(time.monotonic() - began, 1.0)
        self.assertTrue(entered.wait(2))
        from .privatebus import app
        app().processEvents()
        self.assertEqual(replies, [], "odpowiedź przed końcem pracy w tle")
        release.set()
        wait_for(replies)
        self.assertEqual(replies, [{"ok": True, "echo": '{"project":"proj"}'}])
        self.assertTrue(seen[0][0].startswith("tide-gitread"))
        self.assertEqual(seen[0][1:3], ("dziennik", {"tablica": 1}))
        self.assertEqual(boards, [True], "źródło Tablicy w wątku głównym")

    def test_real_projection_runs_no_git_and_failures_do_not_leak(self):
        from tide_daemon.git_monitor import GitMonitor
        self.card("K-1").start().ticket("PT-1").merge_ticket("PT-1").save()
        reader = RecordingReader()
        monitor = GitMonitor(lambda: self.journal, reader=reader, board_source=lambda: self.board)
        self.addCleanup(monitor.stop)
        replies = []
        monitor.branch_map(json.dumps({"project": "proj"}), lambda data: replies.append(json.loads(data)))
        monitor.branch_map("{}", lambda data: replies.append(json.loads(data)))
        monitor.branch_map("zły", lambda data: replies.append(json.loads(data)))
        monitor.branch_map(json.dumps({"project": "brak"}), lambda data: replies.append(json.loads(data)))
        wait_for(replies, 4)
        self.assertEqual(len(replies), 4)
        by_kind = {("nodes" in r, "projects" in r, r.get("error")) for r in replies}
        self.assertEqual(by_kind, {(True, False, None), (False, True, None), (False, False, "bad-request"),
                                   (False, False, "unknown-project")})
        self.assertEqual(reader.calls, [])

        def broken(reader, journal, board, text):
            raise RuntimeError("tajne")

        failing = GitMonitor(board_source=lambda: self.board, map_compute=broken)
        replies = []
        with mock.patch("builtins.print"):
            failing.branch_map("{}", lambda data: replies.append(json.loads(data)))
            wait_for(replies)
        self.assertEqual((replies[0]["ok"], replies[0]["error"]), (False, "failed"))
        self.assertNotIn("tajne", replies[0]["message"])
        failing.stop()
        failing.branch_map("{}", lambda data: replies.append(json.loads(data)))
        self.assertEqual(replies[1]["error"], "cancelled")

    def test_board_source_failure_is_unavailable(self):
        from tide_daemon.git_monitor import GitMonitor

        def broken():
            raise OSError("tablica")

        monitor = GitMonitor(lambda: self.journal, board_source=broken)
        self.addCleanup(monitor.stop)
        replies = []
        with mock.patch("builtins.print"):
            monitor.branch_map("{}", lambda data: replies.append(json.loads(data)))
            wait_for(replies)
        self.assertEqual(replies[0]["error"], "unavailable")


if __name__ == "__main__":
    unittest.main()
