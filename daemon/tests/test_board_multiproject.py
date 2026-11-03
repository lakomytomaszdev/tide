"""Karty z kilkoma projektami i trwałe połączenie karta ↔ sesja agenta (PT-28).

Przypadek z życia (K-1 „Data ważności wiadomości”): agent uruchomiony z karty w worktree toolssuite
wszedł (`cd`) do katalogu dokumentów tablicy, żeby napisać plan — od tej chwili /Agents przypisywał sesję
„projektowi” tide-board na master, a karta wróciła do Do zrobienia, choć agent dalej pracował.

- katalog pracy sesji w tablicy (board/place.py): /Agents, KRunner, raport i koordynacja biorą projekt
  z katalogu startu (Claude Code — katalog dziennika, Codex — session_meta), a bez niego z projektu
  `docs/<slug>/`,
- połączenie po uruchomieniu z karty (derive: worktree karty = katalog startu, okno czasu, narzędzie),
  utrwalane w `session` karty (BoardMonitor → store.link_session) — bez nadpisywania ręcznego `session`,
- dodatkowe projekty karty (`projects`): zapis, migawka, RenderLaunch (polecenie, addDirs), /Tasks Start
  (`--add-dir` repozytoriów projektów tablicy), stan karty po gałęzi we wszystkich repozytoriach.

Wszystko na tymczasowych repozytoriach i syntetycznych dziennikach — prawdziwej tablicy, dzienników
ani agentów testy nie dotykają.
"""
import argparse
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest import mock

from .gitrepos import commit, git
from .privatebus import app, wait_until
from .test_board_derive import FakeBranches, FakeSource, board, ticket
from .test_tasks_core import fake_bin, quiet_git
from tide_daemon.agents import AgentScanner
from tide_daemon.agents.claude import escape_cwd
from tide_daemon.board import flows, place
from tide_daemon.board.derive import LAUNCH_WINDOW, MISSING, CardDeriver
from tide_daemon.board.model import BoardError
from tide_daemon.board.store import BoardStore
from tide_daemon.coordination.model import Coordinator
from tide_daemon.coordination.scanner import CoordinationScanner
from tide_daemon.krunner.index import SessionIndex
from tide_daemon.reports.sessions import read_session
from tide_daemon.agents.claude import ClaudeLog
from tide_daemon.tasks import Planner, TaskError

BRANCH = "claude/k-1-data-waznosci-wiadomosci"
SID = "4f1c0a2e-0000-4000-8000-00000000000k"


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class World:
    """Katalog projektów z toolssuite (worktree karty), mobileapp i legacyinfra, tablica z kartą K-1
    uruchomioną w trybie lekkim i katalogi dzienników Claude Code oraz Codexa."""

    def __init__(self, case: unittest.TestCase):
        self.tmp = tempfile.mkdtemp(prefix="tide-multi-")
        case.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        config = os.path.join(self.tmp, "gitconfig")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write("[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": config, "GIT_CONFIG_NOSYSTEM": "1",
                                               "TIDE_BOARD_DIR": os.path.join(self.tmp, "ws", "tide-board")})
        patcher.start()
        case.addCleanup(patcher.stop)
        place.clear_cache()
        case.addCleanup(place.clear_cache)
        self.ws = os.path.join(self.tmp, "ws")
        self.repos = {}
        for name in ("toolssuite", "mobileapp", "legacyinfra"):
            path = os.path.join(self.ws, name)
            os.makedirs(path)
            git(path, "init", "-q", "-b", "master")
            commit(path, "README.md", f"{name}\n")
            self.repos[name] = path
        self.repo = self.repos["toolssuite"]
        self.worktree = os.path.join(self.repo, ".claude", "worktrees", "k-1-data-waznosci-wiadomosci")
        git(self.repo, "worktree", "add", "-q", "-b", BRANCH, self.worktree)
        self.now = time.time()
        self.launched = self.now - 900
        self.store = BoardStore(os.path.join(self.ws, "tide-board"), clock=time.time)
        created = self.store.create_card({"title": "Data ważności wiadomości", "repo": self.repo, "column": "todo"})
        self.card_id = created["id"]
        self.store.update_card(self.card_id, {"worktree": self.worktree, "mode": "light", "tool": "claude",
                                              "launched": iso(self.launched)})
        self.docs = os.path.join(self.store.root, "docs", "toolssuite", "plans")
        os.makedirs(self.docs)
        self.claude = os.path.join(self.tmp, "claude")
        self.codex = os.path.join(self.tmp, "codex")
        os.makedirs(os.path.join(self.codex, "sessions"))

    # --- dzienniki ----------------------------------------------------------------------------

    def claude_log(self, sid=SID, launch_dir=None) -> str:
        folder = os.path.join(self.claude, "projects", escape_cwd(launch_dir or self.worktree))
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, f"{sid}.jsonl")

    def claude_write(self, path: str, ts: float, cwd: str, branch: str, tool_result=False) -> None:
        if tool_result:
            record = {"type": "user", "timestamp": iso(ts), "cwd": cwd, "gitBranch": branch,
                      "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1",
                                                                "content": "ok"}]}}
        else:
            record = {"type": "user", "timestamp": iso(ts), "cwd": cwd, "gitBranch": branch,
                      "message": {"role": "user", "content": "Karta K-1: Data ważności wiadomości"}}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def k1_session(self, moved: bool, path=None) -> str:
        """Dziennik K-1: start w worktree chwilę po uruchomieniu, a z `moved` — dalsza praca w katalogu
        planów tablicy (gitBranch master, bo to repozytorium tablicy)."""
        path = path or self.claude_log()
        self.claude_write(path, self.launched + 15, self.worktree, BRANCH)
        self.claude_write(path, self.launched + 60, self.worktree, BRANCH, tool_result=True)
        if moved:
            self.claude_write(path, self.now - 20, self.docs, "master", tool_result=True)
        return path

    def move_into_board(self, path: str) -> None:
        self.claude_write(path, self.now - 5, self.docs, "master", tool_result=True)

    def sessions(self) -> list[dict]:
        scanner = AgentScanner(self.claude, self.codex)
        scanner.discover(self.now)
        scanner.update()
        return scanner.sessions(self.now)


def card_of(snapshot_json: str, card_id: str) -> dict:
    return next(c for c in json.loads(snapshot_json)["cards"] if c["id"] == card_id)


class PlaceTest(unittest.TestCase):
    """Sesja w repozytorium tablicy należy do projektu z katalogu startu (albo z `docs/<slug>/`)."""

    def setUp(self):
        self.w = World(self)

    def test_work_cwd_rules(self):
        w = self.w
        self.assertEqual(place.board_root(w.docs), w.store.root)
        self.assertEqual(place.board_root(w.worktree), "")
        self.assertEqual(place.work_cwd(w.worktree, ""), w.worktree, "poza tablicą bez zmian")
        self.assertEqual(place.work_cwd(w.docs, w.worktree), w.worktree, "w tablicy — katalog startu")
        self.assertEqual(place.work_cwd(w.docs, ""), w.repo, "bez katalogu startu — repozytorium projektu docs/<slug>")
        self.assertEqual(place.work_cwd(w.docs, w.docs), w.repo, "start też w tablicy — projekt z docs/<slug>")
        inbox = os.path.join(w.store.root, "docs", "_inbox")
        os.makedirs(inbox)
        self.assertEqual(place.work_cwd(inbox, ""), inbox, "karta bez projektu — to naprawdę praca w tablicy")
        self.assertEqual(place.work_cwd(w.store.root, w.worktree), w.worktree)
        self.assertEqual(place.work_cwd("", w.worktree), w.worktree)

    def test_agents_attribute_board_docs_session_to_launch_project(self):
        w = self.w
        path = w.k1_session(moved=False)
        before = w.sessions()[0]
        w.move_into_board(path)
        after = w.sessions()[0]
        for s in (before, after):
            self.assertEqual((s["project"], s["branch"], s["cwd"]), ("toolssuite", BRANCH, w.worktree))
            self.assertEqual((s["launchCwd"], s["launchKey"]), (w.worktree, escape_cwd(w.worktree)))
            self.assertAlmostEqual(s["startedTs"], w.launched + 15, places=0)
        self.assertNotIn("tide-board", {s["project"] for s in w.sessions()})

    def test_claude_without_launch_record_falls_back_to_docs_project(self):
        w = self.w
        path = w.claude_log(sid="b0000000-0000-4000-8000-000000000001", launch_dir="/inny/katalog")
        w.claude_write(path, w.now - 30, w.docs, "master")
        s = w.sessions()[0]
        self.assertEqual(s["launchCwd"], "", "pierwszy cwd nie pasuje do katalogu dziennika")
        self.assertEqual((s["project"], s["cwd"], s["branch"]), ("toolssuite", w.repo, "master"))

    def test_codex_launch_dir_from_session_meta(self):
        w = self.w
        folder = os.path.join(w.codex, "sessions", "2026", "10", "27")
        os.makedirs(folder)
        tid = "0000000a-0000-4000-8000-000000000042"
        records = [
            {"type": "session_meta", "timestamp": iso(w.launched + 10),
             "payload": {"id": tid, "cwd": w.worktree, "git": {"branch": BRANCH}, "originator": "codex_cli_rs",
                         "timestamp": iso(w.launched + 10)}},
            {"type": "event_msg", "timestamp": iso(w.launched + 11), "payload": {"type": "task_started"}},
            {"type": "turn_context", "timestamp": iso(w.now - 10), "payload": {"cwd": w.docs}},
        ]
        with open(os.path.join(folder, f"rollout-2026-10-27T10-00-00-{tid}.jsonl"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(r) for r in records) + "\n")
        s = w.sessions()[0]
        self.assertEqual((s["tool"], s["project"], s["cwd"], s["branch"]), ("codex", "toolssuite", w.worktree, BRANCH))
        self.assertEqual((s["launchCwd"], s["launchKey"]), (w.worktree, escape_cwd(w.worktree)))

    def test_krunner_and_morning_report_use_launch_project(self):
        w = self.w
        path = w.k1_session(moved=True)
        index = SessionIndex(w.claude, w.codex, clock=lambda: w.now)
        index.refresh()
        entry = next(e for e in index.entries() if e["sessionId"] == SID)
        self.assertEqual((entry["project"], entry["cwd"], entry["branch"]), ("toolssuite", w.worktree, BRANCH))
        report = read_session(path, ClaudeLog, w.now - 86400, w.now)
        self.assertEqual((report.cwd, report.branch), (w.worktree, ""), "gałąź uzupełni katalog pracy, nie master tablicy")

    def test_coordinator_in_board_docs_keeps_repo_branch_and_card_plan(self):
        w = self.w
        with open(os.path.join(w.docs, "2026-10-27-data-waznosci.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Data ważności\ntracker: local\ntracker-url:\ntracker-project:\n"
                     "ticket-prefix: DPT\ncard: K-1\ncoordinator: claude\nstatus: active\n"
                     "updated: 2026-10-27T10:00:00+01:00\n---\n\n| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |\n"
                     "| --- | --- | --- | --- | --- | --- |\n| DPT-1 | Pole daty | w-toku | | | repo: toolssuite |\n")
        coord = Coordinator(id=f"claude:{SID}", tool="claude", session_id=SID, path=w.claude_log(), cwd=w.docs,
                            launch_cwd=w.worktree, branch="master", first_ts=w.launched + 15, last_ts=w.now - 5)
        scanner = CoordinationScanner(w.claude, w.codex, clock=lambda: w.now)
        result = scanner.board(coord, None, w.now)
        self.assertEqual((result["project"], result["cwd"], result["branch"]), ("toolssuite", w.worktree, BRANCH))
        self.assertEqual(os.path.realpath(result["repoPath"]), os.path.realpath(w.repo))
        self.assertEqual((result["plan"] or {}).get("card"), "K-1", "plan karty z katalogu projektu w tablicy")


def card(**kw) -> dict:
    out = {"id": "K-1", "title": "Data ważności wiadomości", "project": "toolssuite", "column": "todo",
           "session": "", "branch": "", "check": "auto", "createdTs": 1000.0, "movedTs": 1000.0,
           "repo": "/srv/dpt", "worktree": "/srv/dpt/.claude/worktrees/k-1", "tool": "claude", "launchedTs": 10_000.0}
    out.update(kw)
    return out


def sess(sid="claude:s1", started=10_020.0, launch="/srv/dpt/.claude/worktrees/k-1", state="working",
         cwd="/srv/board/docs/toolssuite/plans", branch="master", **kw) -> dict:
    tool, _, raw = sid.partition(":")
    out = {"id": sid, "tool": tool, "sessionId": raw, "project": "tide-board", "branch": branch, "cwd": cwd,
           "launchCwd": launch, "launchKey": escape_cwd(launch), "startedTs": started, "state": state, "reason": "",
           "sinceTs": started, "lastActivityTs": started + 60, "lastAction": "Write · plan.md", "subagent": False}
    out.update(kw)
    return out


def ctx(sessions=(), boards=(), claimed=(), branches=None, now=11_000.0):
    deriver = CardDeriver(lambda: list(sessions), lambda: list(boards), branches=branches or FakeBranches(),
                          clock=lambda: now)
    deriver.repo_of = lambda cwd: next((r for r in ("/srv/dpt", "/srv/maui", "/srv/infra") if (cwd or "").startswith(r)), "")
    return deriver.context(claimed)


class LaunchLinkTest(unittest.TestCase):
    """Reguła „uruchomienie z karty”: katalog startu, okno czasu, narzędzie, sesje innych kart."""

    def test_session_started_in_card_worktree_links_regardless_of_current_cwd(self):
        c = ctx([sess()])
        d = c(card())
        self.assertEqual((d["link"], d["session"], d["column"]), ("launch", "claude:s1", "progress"))
        self.assertEqual(c.sticky(card()), ("claude:s1", "launch", ""))

    def test_window_tool_and_place(self):
        for bad in (sess(started=10_000 + LAUNCH_WINDOW + 1), sess(started=10_000 - 200),
                    sess(sid="codex:s1"), sess(launch="/srv/dpt"), sess(subagent=True)):
            self.assertIsNone(ctx([bad])(card()), bad)
        self.assertEqual(ctx([sess(launch="/srv/dpt")])(card(worktree=""))["link"], "launch",
                         "bez worktree — katalog projektu")
        self.assertEqual(ctx([sess(sid="codex:s1")])(card(tool=""))["link"], "launch", "karta bez narzędzia — dowolne")

    def test_launch_key_when_launch_dir_unknown(self):
        s = sess(launchCwd="")
        self.assertEqual(ctx([s])(card())["link"], "launch")
        self.assertIsNone(ctx([dict(s, launchKey=escape_cwd("/srv/inny"))])(card()))

    def test_closest_start_wins_and_other_cards_sessions_are_skipped(self):
        early, close = sess("claude:a", started=10_200), sess("claude:b", started=10_010)
        self.assertEqual(ctx([early, close])(card())["session"], "claude:b")
        self.assertEqual(ctx([early, close], claimed=["claude:b"])(card())["session"], "claude:a",
                         "sesja przypięta do innej karty")
        self.assertIsNone(ctx([close], claimed=["b"])(card()), "przypięta po samym id też się liczy")

    def test_pinned_session_is_kept_after_cwd_project_and_branch_change(self):
        s = sess(cwd="/srv/board/docs/toolssuite/plans", branch="master", project="tide-board", launchCwd="",
                 launchKey="", startedTs=0)
        d = ctx([s])(card(session="claude:s1", worktree="", launchedTs=0))
        self.assertEqual((d["link"], d["session"], d["column"]), ("session", "claude:s1", "progress"))
        self.assertIsNone(ctx([s]).sticky(card(session="claude:s1")), "ustawionej sesji nie nadpisujemy")
        self.assertIsNone(ctx([s]).sticky(card(session="claude:ręcznie")))

    def test_ended_pinned_session_yields_to_newer_live_launch(self):
        old = sess("claude:stara", started=5000, launch="/inny", state="ended", lastActivityTs=6000)
        new = sess("claude:nowa", started=10_010)
        d = ctx([old, new], claimed=["claude:stara"])(card(session="claude:stara"))
        self.assertEqual((d["link"], d["session"]), ("launch", "claude:nowa"))
        live_old = dict(old, state="idle")
        self.assertEqual(ctx([live_old, new])(card(session="claude:stara"))["session"], "claude:stara",
                         "żywa przypięta sesja zostaje")

    def test_confident_branch_match_is_sticky_but_ambiguous_is_not(self):
        one = sess("claude:g", started=0, launch="/srv/dpt/.claude/worktrees/x", cwd="/srv/dpt/.claude/worktrees/x",
                   branch="claude/k-1-data")
        plain = card(launchedTs=0, worktree="")
        self.assertEqual(ctx([one])(plain)["link"], "card-branch")
        self.assertEqual(ctx([one]).sticky(plain), ("claude:g", "card-branch", ""))
        two = dict(one, id="claude:h", sessionId="h", lastActivityTs=1)
        self.assertIsNone(ctx([one, two]).sticky(plain), "dwa dopasowania — nie utrwalamy")
        coord = board("claude:k", [ticket("T-1", branch="claude/k-1-t1")], branch="claude/k-1-data", repo="/srv/dpt")
        self.assertEqual(ctx([], [coord]).sticky(plain), ("claude:k", "card-branch", ""),
                         "koordynator i jego tickety to jedno")

    def test_scoped_link_is_persisted_with_the_tickets_branch(self):
        """Przegląd PT-28: karta połączona z częścią ticketów koordynatora nie może po utrwaleniu przejąć
        całego koordynatora (cudze pytanie „Który format?” jako jej „czeka”)."""
        coord = board("claude:koord", [ticket("T-1", "gotowe", branch="feat/k-1-a"),
                                       ticket("T-2", "czeka", branch="feat/k-13-b", question="Który format?")],
                      repo="/srv/dpt")
        plain = card(launchedTs=0, worktree="")
        before = ctx([], [coord])(plain)
        self.assertEqual((before["link"], before["tickets"]), ("card-branch", ["T-1"]))
        sid, how, branch = ctx([], [coord]).sticky(plain)
        self.assertEqual((sid, how, branch), ("claude:koord", "card-branch", "feat/k-1-a"))
        after = ctx([], [coord], claimed=[sid])(card(launchedTs=0, worktree="", session=sid, branch=branch))
        self.assertEqual((after["link"], after["tickets"]), ("session", ["T-1"]))
        self.assertNotEqual(after["column"], "waiting", "pytanie ticketu innej karty nie jest jej „czeka”")
        two = board("claude:koord", [ticket("T-1", branch="feat/k-1-a"), ticket("T-3", branch="feat/k-1-c")],
                    repo="/srv/dpt")
        self.assertIsNone(ctx([], [two]).sticky(plain), "tickety z kilku gałęzi — nie utrwalamy")

    def test_no_persisting_right_after_launch(self):
        """Przegląd PT-28: świeże uruchomienie nie przypina obcej ani starej sesji."""
        fresh = ctx([sess(started=10_005)], now=10_010)
        self.assertEqual(fresh(card())["link"], "launch", "w pamięci karta już ją pokazuje")
        self.assertIsNone(fresh.sticky(card()), "przez PERSIST_DELAY po launched nic do pliku")
        self.assertEqual(ctx([sess(started=10_005)], now=10_040).sticky(card()), ("claude:s1", "launch", ""))
        earlier = sess(started=10_000 - 60, launch="/srv/dpt")  # inna sesja w katalogu repozytorium
        self.assertEqual(ctx([earlier])(card(worktree=""))["link"], "launch")
        self.assertIsNone(ctx([earlier]).sticky(card(worktree="")), "zaczęła się przed uruchomieniem — tylko w pamięci")
        old = sess("claude:stara", started=1000, launch="/srv/dpt/.claude/worktrees/x", state="ended",
                   cwd="/srv/dpt/.claude/worktrees/x", branch="claude/k-1-data", lastActivityTs=2000)
        relaunched = card(worktree="")  # po ponownym uruchomieniu (session: ""), nowa sesja jeszcze nie ruszyła
        self.assertEqual(ctx([old], now=10_100)(relaunched)["link"], "card-branch")
        self.assertIsNone(ctx([old], now=10_100).sticky(relaunched), "w oknie uruchomienia nie utrwalamy po gałęzi")
        self.assertEqual(ctx([old], now=10_000 + LAUNCH_WINDOW + 1).sticky(relaunched),
                         ("claude:stara", "card-branch", ""))


class StickyMonitorTest(unittest.TestCase):
    """BoardMonitor utrwala sesję w karcie (commit „K-1: link session …”) i trzyma kartę w W toku po `cd`."""

    def setUp(self):
        app()
        self.w = World(self)
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
        monitor = start_board(None, argparse.Namespace(board_dir=self.w.store.root), Trees(self.trees_source),
                              self.agents, self.coordination, clock=lambda: self.w.now)
        monitor._rederive.setInterval(30)
        monitor._debounce.setInterval(60_000)
        self.addCleanup(monitor.stop)
        monitor.start()
        return monitor

    def file_session(self):
        return next(c for c in self.w.store.snapshot()["cards"] if c["id"] == self.w.card_id)["session"]

    def last_subject(self):
        return git(self.w.store.root, "log", "-1", "--format=%s").strip()

    def test_k1_reproduction_stays_in_progress_after_cd_into_board_docs(self):
        w = self.w
        path = w.k1_session(moved=False)
        self.agents.sessions = w.sessions()
        monitor = self.start()
        self.assertTrue(wait_until(lambda: self.file_session() == f"claude:{SID}", timeout=5), self.file_session())
        self.assertEqual(self.last_subject(), f"K-1: link session claude:{SID} (launch)")
        self.assertTrue(wait_until(lambda: card_of(monitor.snapshot_json(), "K-1")["derived"]["link"] == "session",
                                   timeout=3))
        w.move_into_board(path)
        self.agents.sessions = w.sessions()
        self.assertEqual(self.agents.sessions[0]["project"], "toolssuite")
        self.agents.changed.emit()
        wait_until(lambda: False, timeout=0.2)
        c = card_of(monitor.snapshot_json(), "K-1")
        self.assertEqual((c["boardColumn"], c["derived"]["session"]), ("progress", f"claude:{SID}"))
        # Nawet gdyby /Agents nadal mówił „tide-board, master” (stara usługa) — przypięta sesja trzyma kartę.
        self.agents.sessions = [dict(self.agents.sessions[0], project="tide-board", branch="master", cwd=w.docs)]
        self.agents.changed.emit()
        wait_until(lambda: False, timeout=0.2)
        self.assertEqual(card_of(monitor.snapshot_json(), "K-1")["boardColumn"], "progress")

    def test_service_that_first_sees_the_session_after_cd_still_links_it(self):
        w = self.w
        w.k1_session(moved=True)
        self.agents.sessions = w.sessions()
        monitor = self.start()
        self.assertTrue(wait_until(lambda: self.file_session() == f"claude:{SID}", timeout=5))
        self.assertEqual(card_of(monitor.snapshot_json(), "K-1")["boardColumn"], "progress")

    def test_relaunch_clears_session_and_links_the_new_one(self):
        w = self.w
        w.k1_session(moved=True)
        self.agents.sessions = w.sessions()
        monitor = self.start()
        self.assertTrue(wait_until(lambda: self.file_session() == f"claude:{SID}", timeout=5))
        # Ponowne uruchomienie z okna (board.js launchMarkRequest): nowe `launched` i `session: ""`.
        relaunched = w.now - 60
        reply = json.loads(monitor.update_card("K-1", json.dumps({
            "branch": BRANCH, "worktree": w.worktree, "mode": "light", "tool": "claude", "check": "auto",
            "launched": iso(relaunched), "session": ""})))
        self.assertTrue(reply["ok"], reply)
        new = "9e2a0000-0000-4000-8000-000000000002"
        path = w.claude_log(sid=new)
        w.claude_write(path, relaunched + 10, w.worktree, BRANCH)
        w.claude_write(path, w.now - 5, w.docs, "master", tool_result=True)
        self.agents.sessions = w.sessions()
        self.agents.changed.emit()
        self.assertTrue(wait_until(lambda: self.file_session() == f"claude:{new}", timeout=5), self.file_session())
        self.assertEqual(self.last_subject(), f"K-1: link session claude:{new} (launch)")
        self.assertEqual(card_of(monitor.snapshot_json(), "K-1")["derived"]["session"], f"claude:{new}",
                         "stara sesja (start poza oknem nowego uruchomienia) pominięta")

    def test_manual_session_is_never_overwritten(self):
        w = self.w
        w.store.update_card(w.card_id, {"session": "claude:recznie"})
        w.k1_session(moved=False)
        self.agents.sessions = w.sessions()
        head = git(w.store.root, "rev-parse", "HEAD").strip()
        monitor = self.start()
        wait_until(lambda: False, timeout=0.5)
        self.assertEqual(self.file_session(), "claude:recznie")
        self.assertEqual(git(w.store.root, "rev-parse", "HEAD").strip(), head, "bez commita")
        self.assertIsNotNone(monitor)

    def test_link_session_store_method(self):
        store = self.w.store
        self.assertTrue(store.link_session("K-1", "claude:x", "card-branch", "feat/k-1-a")["changed"])
        self.assertEqual(self.file_session(), "claude:x")
        self.assertEqual(next(c for c in store.snapshot()["cards"] if c["id"] == "K-1")["branch"], "feat/k-1-a",
                         "gałąź ticketów, gdy karta jej nie miała")
        again = store.link_session("K-1", "claude:y")
        self.assertEqual((again["changed"], again["session"]), (False, "claude:x"))
        with self.assertRaises(BoardError):
            store.link_session("K-1", "")


class ExtraProjectsStoreTest(unittest.TestCase):
    """Pole `projects` karty: zapis, projekt zakładany z repozytorium, migawka, walidacja."""

    def setUp(self):
        self.w = World(self)
        self.store = self.w.store
        self.store.upsert_project({"repo": self.w.repos["mobileapp"]})

    def card(self, card_id="K-1"):
        return next(c for c in self.store.snapshot()["cards"] if c["id"] == card_id)

    def test_update_with_slug_and_repo_creates_project_and_writes_line(self):
        w = self.w
        result = self.store.update_card("K-1", {"projects": ["mobileapp", w.repos["legacyinfra"], "toolssuite",
                                                             "mobileapp"]})
        self.assertTrue(result["changed"])
        subjects = git(self.store.root, "log", "-2", "--format=%s").split("\n")
        self.assertEqual(subjects[:2], ["K-1: update projects", "project legacyinfra: create"])
        with open(os.path.join(self.store.root, "cards", "K-1.md"), encoding="utf-8") as fh:
            self.assertIn("\nprojects: mobileapp, legacyinfra\n", fh.read())
        c = self.card()
        self.assertEqual(c["projects"], ["mobileapp", "legacyinfra"], "bez głównego i bez powtórzeń")
        self.assertEqual([(p["slug"], p["repo"]) for p in c["extraProjects"]],
                         [("mobileapp", w.repos["mobileapp"]), ("legacyinfra", w.repos["legacyinfra"])])
        self.assertFalse(self.store.update_card("K-1", {"projects": "mobileapp, legacyinfra"})["changed"])
        self.store.update_card("K-1", {"project": "mobileapp"})
        self.assertEqual(self.card()["projects"], ["legacyinfra"], "nowy główny wypada z dodatkowych")
        self.store.update_card("K-1", {"projects": []})
        self.assertEqual(self.card()["projects"], [])

    def test_create_with_projects_and_without(self):
        made = self.store.create_card({"title": "Trzy repozytoria", "repo": self.w.repo,
                                       "projects": [self.w.repos["mobileapp"]]})
        self.assertEqual(self.card(made["id"])["projects"], ["mobileapp"])
        plain = self.store.create_card({"title": "Jedno"})
        with open(os.path.join(self.store.root, "cards", f"{plain['id']}.md"), encoding="utf-8") as fh:
            self.assertNotIn("projects:", fh.read(), "karta bez dodatkowych projektów nie ma klucza")

    def test_validation(self):
        for bad in (["nie-ma"], ["Zły slug"], ["/nie/ma/repo"], [self.store.root], 5,
                    [f"p{i}" for i in range(7)]):
            with self.assertRaises(BoardError, msg=bad):
                self.store.update_card("K-1", {"projects": bad})
        self.assertEqual(self.card()["projects"], [])

    def test_unknown_slug_in_file_is_an_error_not_a_crash(self):
        path = os.path.join(self.store.root, "cards", "K-1.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace("check: auto\n", "check: auto\nprojects: mobileapp, zniknal\n"))
        snap = self.store.snapshot()
        c = next(c for c in snap["cards"] if c["id"] == "K-1")
        self.assertEqual(c["projects"], ["mobileapp", "zniknal"])
        self.assertEqual([p["slug"] for p in c["extraProjects"]], ["mobileapp"])
        self.assertTrue(any("zniknal" in e["message"] for e in snap["errors"]))


class RenderLaunchTest(unittest.TestCase):
    """Polecenie dla agenta: dodatkowe repozytoria, gałąź w każdym, ścieżki bezwzględne, bez `cd` do tablicy."""

    def setUp(self):
        self.w = World(self)
        self.store = self.w.store

    def test_single_project_board_docs_note_says_absolute_paths_no_cd(self):
        flow = self.store.render_launch("K-1", {"mode": "light"})
        self.assertIn("po ścieżkach bezwzględnych — nie wchodź do niego przez `cd`", flow["text"])
        self.assertEqual(flow["addDirs"], [flow["docsDir"]])
        self.assertEqual((flow["projects"], flow["extraRepos"]), ([], []))
        self.assertNotIn("Dodatkowe repozytoria", flow["text"])
        self.assertNotIn("{projects}", flow["text"])

    def test_multi_project_text_and_add_dirs(self):
        w = self.w
        self.store.update_card("K-1", {"projects": [w.repos["mobileapp"], w.repos["legacyinfra"]]})
        for mode in ("full", "light"):
            flow = self.store.render_launch("K-1", {"mode": mode, "branch": BRANCH})
            self.assertEqual(flow["addDirs"], [flow["docsDir"], w.repos["mobileapp"], w.repos["legacyinfra"]])
            self.assertEqual(flow["projects"], ["mobileapp", "legacyinfra"])
            text = flow["text"]
            self.assertIn(f"- mobileapp: {w.repos['mobileapp']}\n- legacyinfra: {w.repos['legacyinfra']}", text)
            self.assertIn(f"załóż gałąź o tej samej nazwie {BRANCH}", text)
            self.assertIn(f"plan i specyfikacja są tylko w {flow['docsDir']}", text)
            self.assertIn("`git -C <ścieżka> …` — nie przechodź do nich przez `cd`", text)
            self.assertIn("które repozytorium zmienia (np. „repo: mobileapp”)", text)
            self.assertIn("Bez push", text)
            self.assertLess(text.index("Dodatkowe repozytoria"), text.index("Opis karty:"), "w nagłówku polecenia")
        spec = self.store.render_launch("K-1", {"mode": "spec"})["text"]
        self.assertIn("w tym trybie tylko do odczytu", spec)
        self.assertNotIn("załóż gałąź", spec)

    def test_missing_repo_is_not_given_to_the_agent(self):
        w = self.w
        self.store.update_card("K-1", {"projects": [w.repos["mobileapp"]]})
        shutil.rmtree(w.repos["mobileapp"])
        flow = self.store.render_launch("K-1", {})
        self.assertEqual(flow["addDirs"], [flow["docsDir"]])
        self.assertNotIn("Dodatkowe repozytoria", flow["text"])

    def test_custom_template_without_field_gets_the_list_appended(self):
        w = self.w
        self.store.update_card("K-1", {"projects": [w.repos["mobileapp"]]})
        own = os.path.join(self.store.root, "flows", "toolssuite")
        os.makedirs(own)
        with open(os.path.join(own, "light.md"), "w", encoding="utf-8") as fh:
            fh.write("Mój szablon dla {card_id}.\n")
        text = self.store.render_launch("K-1", {"mode": "light"})["text"]
        self.assertTrue(text.startswith("Mój szablon dla K-1.\n"))
        self.assertIn(f"- mobileapp: {w.repos['mobileapp']}", text)

    def test_previous_seed_templates_migrate(self):
        for mode, text in flows.SEED_FLOWS.items():
            self.assertIn("{projects}", text)
            previous = text.replace("{projects}", "").replace("tide-flow: 3\n", "tide-flow: 2\n") \
                .replace("tide-flow: 4\n", "tide-flow: 2\n").replace("entry: spec", "mode: spec")
            self.assertTrue(flows.outdated(mode, previous), f"{mode}: szablon sprzed PT-28 zostanie podmieniony")
            self.assertFalse(flows.outdated(mode, text))


class AddDirsTest(unittest.TestCase):
    """/Tasks Start: dodatkowe repozytoria w addDirs — tylko repozytoria z katalogu projektów, które są
    projektami tablicy; `--add-dir` dla obu narzędzi."""

    def setUp(self):
        self.w = World(self)
        self.w.store.upsert_project({"repo": self.w.repos["mobileapp"]})
        self.w.store.upsert_project({"repo": self.w.repos["legacyinfra"]})
        self.outside = os.path.join(self.w.tmp, "poza", "obce")
        os.makedirs(self.outside)
        git(self.outside, "init", "-q", "-b", "master")
        commit(self.outside, "a.txt")
        self.loose = os.path.join(self.w.ws, "luzne")  # w katalogu projektów, ale nie projekt tablicy
        os.makedirs(self.loose)
        git(self.loose, "init", "-q", "-b", "master")
        commit(self.loose, "b.txt")
        home = os.path.join(self.w.tmp, "home")
        os.makedirs(home)
        self.env = {"PATH": fake_bin(os.path.join(self.w.tmp, "bin")), "HOME": home,
                    "XDG_RUNTIME_DIR": os.path.join(self.w.tmp, "run"), "XDG_DATA_HOME": os.path.join(self.w.tmp, "data"),
                    "XDG_STATE_HOME": os.path.join(self.w.tmp, "state")}
        self.planner = Planner(self.w.ws, git=quiet_git(), env=self.env, board_root=self.w.store.root)
        self.docs = os.path.dirname(self.w.docs)

    def request(self, **kw):
        out = {"repoPath": self.w.repo, "tool": "claude", "prompt": "Karta K-1: data ważności", "newWorktree": False,
               "branch": "", "images": []}
        out.update(kw)
        return out

    def test_board_project_repos_go_to_add_dir_for_both_tools(self):
        maui, infra = self.w.repos["mobileapp"], self.w.repos["legacyinfra"]
        link = os.path.join(self.w.tmp, "skrot-maui")
        os.symlink(maui, link)
        for tool in ("claude", "codex"):
            plan = self.planner.prepare(self.request(tool=tool, addDirs=[self.docs, maui, infra, link, self.w.repo]))
            self.assertEqual(plan.argv[1:7], ["--add-dir", os.path.realpath(self.docs), "--add-dir", os.path.realpath(maui),
                                              "--add-dir", os.path.realpath(infra)], tool)
            self.assertEqual(plan.argv[7], "--", "repozytorium zadania i dowiązanie do już podanego — pominięte")

    def test_refusals(self):
        for bad in ([self.outside], [self.loose], [self.w.store.root], [os.path.join(self.w.store.root, "cards")],
                    [self.w.worktree], [self.docs] * 7, [os.path.join(self.w.ws, "nie-ma")]):
            with self.assertRaises(TaskError, msg=bad) as ctx:
                self.planner.prepare(self.request(addDirs=bad))
            self.assertEqual(ctx.exception.code, "bad-add-dir", bad)


class DeriveAcrossReposTest(unittest.TestCase):
    """Skończona praca karty z kilkoma projektami: gałąź karty we wszystkich repozytoriach, w których jest."""

    def finished(self, merged_map):
        class Branches(FakeBranches):
            def merged(self, repo, branch):
                return merged_map.get((repo, branch), MISSING)

        coord = board("claude:k", [ticket("T-1", "gotowe")], state="ended", live=False, branch=BRANCH, repo="/srv/dpt")
        c = card(session="claude:k", branch=BRANCH, launchedTs=0, extraRepos=["/srv/maui", "/srv/infra"])
        return ctx([], [coord], branches=Branches())(c)

    def test_unmerged_branch_in_additional_repo_is_review(self):
        d = self.finished({("/srv/dpt", BRANCH): True, ("/srv/maui", BRANCH): False})
        self.assertEqual((d["column"], d["note"]), ("review", f"do scalenia: maui {BRANCH}"))
        self.assertEqual(d["nextStep"], f"scal gałąź maui {BRANCH}")

    def test_branch_missing_in_additional_repo_is_not_needed(self):
        d = self.finished({("/srv/dpt", BRANCH): True})
        self.assertEqual((d["column"], d["note"]), ("done", "✓ gotowe i scalone"))

    def test_both_unmerged_lists_primary_first(self):
        d = self.finished({("/srv/dpt", BRANCH): False, ("/srv/infra", BRANCH): False})
        self.assertEqual(d["note"], f"do scalenia: dpt {BRANCH} (i 1 innych)")

    def test_card_branch_found_in_additional_repo(self):
        s = sess("claude:m", started=0, launch="/srv/maui", cwd="/srv/maui", branch=BRANCH)
        c = card(launchedTs=0, worktree="", extraRepos=["/srv/maui"])
        self.assertEqual(ctx([s])(c)["link"], "card-branch")
        self.assertIsNone(ctx([s])(card(launchedTs=0, worktree="")), "bez dodatkowego projektu — obce repozytorium")


if __name__ == "__main__":
    unittest.main()
