"""Stan kart tablicy z pracy agentów (PT-5, board/derive.py): połączenie karty z pracą, kolumny
W toku / Czeka na mnie / Do sprawdzenia / Gotowe, pole `check` karty i przeliczanie na żywo.

Sesje i tablice koordynatorów to słowniki w kształcie /Agents i /Coordination (jak z --print i
--print-coordination); repozytoria git — tymczasowe (gitrepos). Prawdziwej tablicy nie dotykamy.
"""
import argparse
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from . import gitrepos
from .gitrepos import commit, git
from .privatebus import app, wait_until
from tide_daemon import attention
from tide_daemon.agents import state as agent_state
from tide_daemon.agents.gitinfo import main_root
from tide_daemon.board.derive import MISSING, PENDING, BranchState, CardDeriver
from tide_daemon.board.model import BoardError
from tide_daemon.board.store import BoardStore

NOW = 1_793_100_000.0  # 2026-10-27
REPO = "/srv/repos/dom"   # repozytorium projektu karty (tylko ścieżka — git udaje FakeBranches)


def hhmm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


class FakeBranches:
    """Zamiast gita: gałąź główna i wynik „scalona?” dla (repozytorium, gałąź)."""

    def __init__(self, merged=None, main="master"):
        self.merged_map = dict(merged or {})
        self.main = main
        self.asked = []

    def main_branch(self, repo):
        return self.main if repo else ""

    def merged(self, repo, branch):
        self.asked.append(branch)
        return self.merged_map.get(branch, False)


def card(num=12, column="todo", **kw) -> dict:
    out = {"id": f"K-{num}", "title": "Eksport do PDF", "project": "dom", "column": column, "session": "",
           "branch": "", "check": "auto", "createdTs": NOW - 9000, "updatedTs": NOW - 7200,
           "movedTs": NOW - 7200, "repo": REPO}
    out.update(kw)
    return out


def session(sid="claude:aaa", state="working", reason="", cwd=REPO, branch="feat/pdf", since=NOW - 60, **kw) -> dict:
    tool, _, raw = sid.partition(":")
    out = {"id": sid, "tool": tool, "sessionId": raw, "project": os.path.basename(cwd), "branch": branch, "cwd": cwd,
           "state": state, "reason": reason, "sinceTs": since, "lastActivityTs": since, "lastAction": "Edit · pdf.py"}
    out.update(kw)
    return out


def ticket(key, state="w-toku", branch="", question="", activity=NOW - 600, **kw) -> dict:
    out = {"key": key, "id": key, "state": state, "branch": branch, "now": f"{key}: trwa implementacja", "note": "",
           "question": {"text": question} if question else None, "lastActivityTs": activity, "running": 0}
    out.update(kw)
    return out


def board(bid="claude:koord", tickets=(), state="working", branch="master", plan=None, live=True,
          activity=NOW - 30, question="", repo=REPO, **kw) -> dict:
    tool, _, raw = bid.partition(":")
    out = {"id": bid, "tool": tool, "sessionId": raw, "project": os.path.basename(repo), "repoPath": repo,
           "cwd": repo, "branch": branch, "state": state, "reason": "", "live": live, "lastActivityTs": activity,
           "question": {"text": question} if question else None, "plan": plan, "tickets": list(tickets)}
    states = [t["state"] for t in out["tickets"]]
    out["counts"] = {"total": sum(s != "odrzucone" for s in states), "done": states.count("gotowe")}
    out.update(kw)
    return out


def derive(c, sessions=(), boards=(), branches=None, repo_of=None):
    deriver = CardDeriver(lambda: list(sessions), lambda: list(boards), branches=branches or FakeBranches(),
                          clock=lambda: NOW)
    if repo_of is not None:
        deriver.repo_of = repo_of
    else:
        deriver.repo_of = lambda cwd: os.path.realpath(REPO) if (cwd or "").startswith(REPO) else ""
    return deriver(c)


class LinkTest(unittest.TestCase):
    def test_explicit_session_links_coordinator_by_prefixed_and_bare_id(self):
        b = board("claude:f31b1066", [ticket("LEGAL-6")])
        for value in ("claude:f31b1066", "f31b1066"):
            d = derive(card(session=value), boards=[b])
            self.assertEqual((d["link"], d["coordinator"], d["session"]), ("session", "claude:f31b1066", "claude:f31b1066"))
            self.assertEqual(d["column"], "progress")

    def test_explicit_session_links_plain_agent_session(self):
        d = derive(card(session="codex:01a1"), sessions=[session("codex:01a1", state="working")])
        self.assertEqual((d["link"], d["coordinator"], d["session"], d["tool"]), ("session", "", "codex:01a1", "codex"))
        self.assertEqual(d["column"], "progress")
        self.assertEqual(d["note"], "agent pracuje: Edit · pdf.py")

    def test_unknown_session_falls_through_to_branch(self):
        d = derive(card(session="claude:stara", branch="feat/pdf"), sessions=[session("claude:inna", branch="feat/pdf")])
        self.assertEqual((d["link"], d["session"]), ("branch", "claude:inna"))

    def test_plan_card_key_links_coordinator(self):
        plan = {"path": "/srv/plan.md", "status": "active", "card": "k-12"}
        d = derive(card(), boards=[board("codex:plan", [ticket("T-1")], plan=plan),
                                   board("claude:obcy", [ticket("T-2")], plan={"path": "/x", "card": "K-13"})])
        self.assertEqual((d["link"], d["coordinator"]), ("plan", "codex:plan"))

    def test_card_id_in_branch_links_work(self):
        d = derive(card(), boards=[board("claude:k", [ticket("T-1")], branch="claude/k-12-eksport-pdf")])
        self.assertEqual((d["link"], d["coordinator"]), ("card-branch", "claude:k"))
        self.assertIsNone(derive(card(), boards=[board("claude:k", [ticket("T-1")], branch="claude/k-120-cos")]))
        d = derive(card(), sessions=[session("codex:s", branch="codex/K-12")])
        self.assertEqual((d["link"], d["session"]), ("card-branch", "codex:s"))

    def test_card_branch_matches_session_in_project_worktree(self):
        s = session("claude:wt", branch="feat/pdf", cwd=REPO + "-worktrees/pdf")
        roots = {REPO + "-worktrees/pdf": os.path.realpath(REPO)}
        d = derive(card(branch="feat/pdf"), sessions=[s], repo_of=lambda cwd: roots.get(cwd, ""))
        self.assertEqual((d["link"], d["session"], d["branch"]), ("branch", "claude:wt", "feat/pdf"))

    def test_branch_match_needs_same_repo_and_not_main_branch(self):
        other = session("claude:obcy", branch="feat/pdf", cwd="/srv/repos/inne")
        self.assertIsNone(derive(card(branch="feat/pdf"), sessions=[other]))
        self.assertIsNone(derive(card(branch="master"), sessions=[session(branch="master")]))
        self.assertIsNone(derive(card(branch="feat/pdf", repo=""), sessions=[session(branch="feat/pdf")]))

    def test_card_branch_matches_ticket_branch_and_narrows_scope(self):
        b = board("claude:koord", [ticket("PT-4", branch="feat/pt-4"), ticket("PT-6", "gotowe", branch="feat/pt-4"),
                                   ticket("PT-5", branch="feat/pt-5"), ticket("PT-1", "gotowe")])
        d = derive(card(branch="feat/pt-4"), boards=[b])
        self.assertEqual((d["link"], d["tickets"], d["done"], d["total"]), ("branch", ["PT-4", "PT-6"], 1, 2))
        self.assertTrue(d["note"].startswith("PT-4: "), d["note"])

    def test_explicit_session_with_ticket_branch_narrows_but_coordinator_branch_does_not(self):
        b = board("claude:koord", [ticket("PT-4", branch="feat/pt-4 (8d20e9d2)"), ticket("PT-5", branch="feat/pt-5")])
        d = derive(card(session="claude:koord", branch="feat/pt-4"), boards=[b])
        self.assertEqual(d["tickets"], ["PT-4"])
        whole = board("claude:koord", [ticket("A-1", branch="feat/x"), ticket("A-2", "gotowe")], branch="feat/x")
        d = derive(card(session="claude:koord", branch="feat/x"), boards=[whole])
        self.assertEqual((d["tickets"], d["done"], d["total"]), ([], 1, 2))

    def test_precedence_session_then_plan_then_branch(self):
        explicit = board("claude:jawny", [ticket("A-1")], activity=NOW - 9000)
        planned = board("claude:plan", [ticket("B-1")], plan={"path": "/p.md", "card": "K-12"}, activity=NOW - 10)
        branched = board("claude:gałąź", [ticket("C-1")], branch="feat/pdf", activity=NOW - 5)
        boards = [explicit, planned, branched]
        self.assertEqual(derive(card(session="claude:jawny", branch="feat/pdf"), boards=boards)["coordinator"],
                         "claude:jawny")
        self.assertEqual(derive(card(branch="feat/pdf"), boards=boards)["coordinator"], "claude:plan")
        self.assertEqual(derive(card(branch="feat/pdf"), boards=[explicit, branched])["coordinator"], "claude:gałąź")

    def test_most_recent_branch_match_wins(self):
        old = board("claude:stary", [ticket("A-1")], branch="feat/pdf", state="ended", live=False, activity=NOW - 86400)
        new = board("codex:nowy", [ticket("A-2")], branch="feat/pdf", activity=NOW - 100)
        self.assertEqual(derive(card(branch="feat/pdf"), boards=[old, new])["coordinator"], "codex:nowy")

    def test_backlog_only_with_explicit_link(self):
        b = board("claude:koord", [ticket("A-1")], branch="feat/pdf", plan={"path": "/p.md", "card": "K-12"})
        self.assertIsNone(derive(card(column="backlog", branch="feat/pdf"), boards=[board("claude:x", [ticket("A")],
                                                                                          branch="feat/pdf")]))
        self.assertEqual(derive(card(column="backlog"), boards=[b])["link"], "plan")
        self.assertEqual(derive(card(column="backlog", session="claude:koord"), boards=[b])["column"], "progress")

    def test_done_and_archived_are_never_touched(self):
        b = board("claude:koord", [ticket("A-1")])
        for column in ("done", "archived"):
            self.assertIsNone(derive(card(column=column, session="claude:koord"), boards=[b]))

    def test_unlinked_card_stays_in_manual_column(self):
        self.assertIsNone(derive(card(), sessions=[session(branch="feat/inna")], boards=[board()]))

    def test_ended_coordinator_follows_live_successor_with_same_plan(self):
        plan = {"path": "/p.md", "status": "active"}
        old = board("claude:stary", [ticket("A-1")], state="ended", live=False, plan=plan, activity=NOW - 9000)
        new = board("claude:nowy", [ticket("A-1"), ticket("A-2")], plan=plan, activity=NOW - 20)
        d = derive(card(session="claude:stary"), boards=[old, new])
        self.assertEqual((d["coordinator"], d["total"]), ("claude:nowy", 2))
        alive = board("claude:stary", [ticket("A-1")], state="idle", live=True, plan=plan, activity=NOW - 9000)
        self.assertEqual(derive(card(session="claude:stary"), boards=[alive, new])["coordinator"], "claude:stary")


class StateTest(unittest.TestCase):
    def test_waiting_session_means_waiting(self):
        b = board("claude:koord", [ticket("A-1")], question="Scalić teraz?")
        s = session("claude:koord", state="waiting", reason="question")
        d = derive(card(session="claude:koord"), sessions=[s], boards=[b])
        self.assertEqual(d["column"], "waiting")
        self.assertEqual(d["note"], "czeka na odpowiedź: „Scalić teraz?”")
        self.assertEqual(d["nextStep"], "odpowiedz agentowi")
        d = derive(card(session="claude:s"), sessions=[session("claude:s", state="waiting", reason="approval")])
        self.assertEqual((d["column"], d["note"]), ("waiting", "czeka na zgodę: Edit · pdf.py"))

    def test_waiting_definition_is_the_agents_widget_definition(self):
        """Tylko stan `waiting` (agents/state.py, widżet, Meta+A); koniec tury z pytaniem bez odpowiedzi to
        `waiting/reply` (PT-29)."""
        for state in (agent_state.WORKING, agent_state.WAITING, agent_state.DONE, agent_state.IDLE, agent_state.ENDED):
            s = session("claude:s", state=state, reason="question" if state == agent_state.WAITING else "",
                        canActivate=True)
            d = derive(card(session="claude:s"), sessions=[s])
            self.assertEqual(d["column"] == "waiting", state == agent_state.WAITING, state)
            self.assertEqual(agent_state.waits_for_user(s), attention.longest_waiting([s]) is not None, state)
        # PT-29: tura skończona pytaniem w tekście, bez odpowiedzi — to też czekanie (z pytaniem w notatce).
        reply = session("claude:s", state="waiting", reason="reply", waitingText="Pytanie 10: skąd czat?")
        self.assertTrue(agent_state.waits_for_user(reply))
        d = derive(card(session="claude:s"), sessions=[reply])
        self.assertEqual((d["column"], d["note"], d["nextStep"]),
                         ("waiting", "czeka na odpowiedź: „Pytanie 10: skąd czat?”", "odpowiedz agentowi"))

    def test_ticket_waiting_with_question(self):
        b = board("claude:koord", [ticket("LEGAL-14", "czeka", question="Który wariant C007?"), ticket("LEGAL-6")],
                  state="idle")
        d = derive(card(session="claude:koord"), boards=[b])
        self.assertEqual((d["column"], d["note"]), ("waiting", "LEGAL-14: „Który wariant C007?”"))

    def test_plan_only_waiting_ticket_waits_when_agent_is_not_working(self):
        tickets = [ticket("LEGAL-14", "czeka", planNote="Wybierz wariant C007 (A albo B)."), ticket("LEGAL-6")]
        for state in ("idle", "done", "ended"):
            b = board("claude:koord", tickets, state=state, live=state != "ended", activity=NOW - 3600)
            sessions = [] if state == "ended" else [session("claude:koord", state=state, since=NOW - 3600)]
            d = derive(card(session="claude:koord"), sessions=sessions, boards=[b])
            self.assertEqual((d["column"], d["note"], d["nextStep"]),
                             ("waiting", "czeka na twoją decyzję według planu: Wybierz wariant C007 (A albo B).",
                              "odpowiedz agentowi"), state)
        b = board("claude:koord", [ticket("LEGAL-14", "czeka"), ticket("LEGAL-6")], state="idle")
        d = derive(card(session="claude:koord"), boards=[b])
        self.assertEqual(d["note"], "czeka na twoją decyzję według planu (LEGAL-14)")
        b = board("claude:koord", tickets)  # agent pracuje — dalej W toku, z dopiskiem
        d = derive(card(session="claude:koord"), sessions=[session("claude:koord")], boards=[b])
        self.assertEqual(d["column"], "progress")
        self.assertIn("LEGAL-14 czeka według planu", d["note"])
        narrow = board("claude:koord", [ticket("PT-4", branch="feat/pt-4"), ticket("PT-23", "czeka")], state="idle")
        d = derive(card(session="claude:koord", branch="feat/pt-4"), boards=[narrow])
        self.assertEqual(d["column"], "progress", "czeka inny ticket koordynatora, nie ten z karty")

    def test_question_about_other_ticket_does_not_make_narrow_card_wait(self):
        b = board("claude:koord", [ticket("PT-4", branch="feat/pt-4"), ticket("PT-23", "czeka", question="Push?")])
        s = session("claude:koord", state="waiting", reason="question")
        d = derive(card(session="claude:koord", branch="feat/pt-4"), sessions=[s], boards=[b])
        self.assertEqual(d["column"], "progress")

    def test_ai_review_rounds_are_progress(self):
        b = board("claude:koord", [ticket("A-1", "gotowe"), ticket("A-2", "review", now="Trwa ponowne review.")],
                  state="idle", activity=NOW - 4000)
        d = derive(card(session="claude:koord"), boards=[b])
        self.assertEqual((d["column"], d["done"], d["total"]), ("progress", 1, 2))
        self.assertIn("w toku: A-2", d["note"])

    def test_working_coordinator_note_says_what_happens_now(self):
        b = board("claude:koord", [ticket("A-1", "gotowe"), ticket("A-2", now="Trwa implementacja. Testy zielone."),
                                   ticket("A-3", "todo")])
        d = derive(card(session="claude:koord"), sessions=[session("claude:koord")], boards=[b])
        self.assertEqual((d["column"], d["note"], d["done"], d["total"], d["tool"]),
                         ("progress", "A-2: Trwa implementacja (+1)", 1, 3, "claude"))

    def test_idle_done_and_ended_agents_show_since_when_work_stands(self):
        b = board("claude:koord", [ticket("A-1")], state="ended", live=False, activity=NOW - 7200)
        self.assertEqual(derive(card(session="claude:koord"), boards=[b])["note"], "sesja zakończona, w toku: A-1")
        for state, phrase in (("idle", "agent bezczynny od"), ("done", "agent skończył turę o")):
            d = derive(card(session="claude:s"), sessions=[session("claude:s", state=state, since=NOW - 1800)])
            self.assertEqual((d["column"], d["note"], d["agentState"]), ("progress", f"{phrase} {hhmm(NOW - 1800)}", state))
        d = derive(card(session="claude:s"), sessions=[session("claude:s", state="ended")])
        self.assertEqual(d["note"], "sesja zakończona")

    def test_finished_and_merged_with_auto_check_is_done(self):
        b = board("claude:koord", [ticket("A-1", "gotowe", branch="feat/a"), ticket("A-2", "odrzucone")],
                  state="done")
        d = derive(card(session="claude:koord"), boards=[b], branches=FakeBranches({"feat/a": True}))
        self.assertEqual((d["column"], d["note"], d["nextStep"], d["done"], d["total"]),
                         ("done", "✓ gotowe i scalone", "przenieś do Gotowe", 1, 1))

    def test_finished_with_unmerged_branch_is_review(self):
        b = board("claude:koord", [ticket("A-1", "gotowe", branch="feat/a"), ticket("A-2", "gotowe", branch="feat/b")],
                  state="ended", live=False)
        d = derive(card(session="claude:koord"), boards=[b], branches=FakeBranches({"feat/a": True}))
        self.assertEqual((d["column"], d["note"], d["nextStep"], d["branch"]),
                         ("review", "do scalenia: feat/b", "scal gałąź feat/b", "feat/b"))

    def test_card_branch_is_the_only_branch_that_must_be_merged(self):
        b = board("claude:koord", [ticket("A-1", "gotowe", branch="feat/pdf"), ticket("A-2", "gotowe", branch="x/old")],
                  state="done", branch="feat/pdf")
        branches = FakeBranches({"feat/pdf": True})
        self.assertEqual(derive(card(session="claude:koord", branch="feat/pdf"), boards=[b], branches=branches)["column"],
                         "done")
        self.assertNotIn("x/old", branches.asked)

    def test_manual_check_keeps_finished_card_in_review(self):
        b = board("claude:koord", [ticket("A-1", "gotowe")], state="done")
        d = derive(card(session="claude:koord", check="manual"), boards=[b])
        self.assertEqual((d["column"], d["note"], d["nextStep"], d["check"]),
                         ("review", "do sprawdzenia ręcznie", "przetestuj ręcznie", "manual"))
        b = board("claude:koord", [ticket("A-1", "gotowe", branch="feat/a")], state="done")
        d = derive(card(session="claude:koord", check="manual"), boards=[b])
        self.assertEqual((d["note"], d["nextStep"]), ("do sprawdzenia ręcznie; do scalenia: feat/a",
                                                      "przetestuj ręcznie, potem scal gałąź feat/a"))

    def test_finished_but_coordinator_still_working_is_progress(self):
        b = board("claude:koord", [ticket("A-1", "gotowe")])
        d = derive(card(session="claude:koord"), sessions=[session("claude:koord")], boards=[b])
        self.assertEqual(d["column"], "progress")
        self.assertTrue(d["note"].startswith("koordynator kończy"), d["note"])

    def test_plan_done_without_tickets_is_finished(self):
        b = board("claude:koord", [], state="ended", live=False, plan={"path": "/p.md", "status": "done"})
        self.assertEqual(derive(card(session="claude:koord"), boards=[b])["column"], "done")
        b["plan"]["status"] = "active"
        self.assertEqual(derive(card(session="claude:koord"), boards=[b])["column"], "progress")

    def test_card_moved_by_hand_after_finished_work_stays_until_agent_works_again(self):
        b = board("claude:koord", [ticket("A-1", "gotowe", branch="feat/a", activity=NOW - 3000)], state="done",
                  activity=NOW - 3000)
        d = derive(card(session="claude:koord", movedTs=NOW - 100, movedFrom="review"), boards=[b])
        self.assertEqual(d["column"], "")  # migawka: boardColumn = ręczna kolumna
        self.assertIn("wróci do W toku", d["note"])
        # PT-29: liczy się tylko przeniesienie z Do sprawdzenia albo Gotowe, nie z innej kolumny.
        other = derive(card(session="claude:koord", movedTs=NOW - 100, movedFrom="backlog"), boards=[b])
        self.assertEqual(other["column"], "review")
        edited = derive(card(session="claude:koord", updatedTs=NOW - 100, movedTs=NOW - 5000), boards=[b])
        self.assertEqual(edited["column"], "review", "edycja notatki to nie przeniesienie")
        old = card(session="claude:koord", createdTs=NOW - 100)  # karta założona po pracy (sprzed klucza moved)
        old.pop("movedTs")
        self.assertEqual(derive(old, boards=[b])["column"], "review", "założenie karty to nie przeniesienie (PT-29)")
        b["tickets"][0].update(state="w-toku", lastActivityTs=NOW - 10)  # agent znów nad nią pracuje
        b["counts"]["done"] = 0
        self.assertEqual(derive(card(session="claude:koord", movedTs=NOW - 100, movedFrom="review"), boards=[b])["column"],
                         "progress")


class BranchStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-derive-git-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repos = gitrepos.build(self.tmp)

    def test_merged_unmerged_and_deleted_branches(self):
        clock = [NOW]
        branches = BranchState(clock=lambda: clock[0])
        alpha = self.repos["alpha"]
        self.assertEqual(branches.main_branch(alpha), "master")
        self.assertTrue(branches.merged(alpha, "feat/merged"))
        self.assertFalse(branches.merged(alpha, "feat/unmerged"))
        git(alpha, "checkout", "-q", "-b", "feat/old")
        commit(alpha, "old.txt")
        git(alpha, "checkout", "-q", "master")
        git(alpha, "merge", "-q", "--no-ff", "-m", "Merge branch 'feat/old'", "feat/old")
        git(alpha, "branch", "-q", "-d", "feat/old")
        self.assertTrue(branches.merged(alpha, "feat/old"), "usunięta po scaleniu — widać commit scalenia")
        # Nigdy jej nie było: dla głównego repozytorium karty „nie wiadomo” (≠ True), dla dodatkowego —
        # gałąź tam niepotrzebna (PT-28).
        self.assertEqual(branches.merged(alpha, "feat/nigdy"), MISSING)
        self.assertIsNone(branches.merged(alpha, "-x"))
        self.assertIsNone(branches.merged(self.repos["beta"], "topic"))  # bez gałęzi głównej
        git(alpha, "merge", "-q", "--no-ff", "-m", "merge", "feat/unmerged")
        self.assertFalse(branches.merged(alpha, "feat/unmerged"), "wynik z pamięci")
        clock[0] += 60
        self.assertTrue(branches.merged(alpha, "feat/unmerged"), "po czasie pamięci — świeży odczyt")
        calls = []
        real_run = branches.git.run
        branches.git.run = lambda *a, **k: calls.append(a) or real_run(*a, **k)
        clock[0] += 120  # gałęzi bez śladu nie sprawdzamy co MERGE_TTL, tylko rzadziej
        branches.merged(alpha, "feat/nigdy")
        self.assertFalse([c for c in calls if "feat/nigdy" in " ".join(c[1:])], calls)

    def test_background_checks_answer_from_memory_and_report_results(self):
        alpha = self.repos["alpha"]
        results = []
        branches = BranchState(clock=lambda: NOW, background=True, on_result=lambda: results.append(True))
        self.addCleanup(branches.stop)
        self.assertEqual(branches.merged(alpha, "feat/unmerged"), PENDING)
        self.assertTrue(wait_until(lambda: results, timeout=5))
        self.assertIs(branches.merged(alpha, "feat/unmerged"), False)
        self.assertEqual(branches.main_branch(alpha), "master")
        deriver = CardDeriver(lambda: [], lambda: [board("claude:k", [ticket("A-1", "gotowe")], state="done",
                                                         repo=alpha)], branches=branches, clock=lambda: NOW)
        pending = deriver(card(session="claude:k", repo=alpha, branch="feat/merged"))
        self.assertEqual(pending["column"], "review", "wynik w drodze — na razie „do scalenia”")
        self.assertTrue(wait_until(lambda: len(results) >= 2, timeout=5))
        self.assertEqual(deriver(card(session="claude:k", repo=alpha, branch="feat/merged"))["column"], "done")

    def test_main_root_of_worktree(self):
        self.assertEqual(main_root(self.repos["unmerged"]), self.repos["alpha"])
        self.assertEqual(main_root(os.path.join(self.repos["agent"], "src")), self.repos["alpha"])
        self.assertEqual(main_root(self.repos["alpha"]), self.repos["alpha"])

    def test_review_versus_done_on_real_repository(self):
        alpha = self.repos["alpha"]
        deriver = CardDeriver(lambda: [], lambda: [board("claude:k", [ticket("A-1", "gotowe")], state="done",
                                                         branch="feat/unmerged", repo=alpha)], clock=lambda: NOW)
        self.assertEqual(deriver(card(session="claude:k", repo=alpha, branch="feat/unmerged"))["column"], "review")
        self.assertEqual(deriver(card(session="claude:k", repo=alpha, branch="feat/merged"))["column"], "done")


class CheckFieldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-derive-board-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        config = os.path.join(self.tmp, "gitconfig")
        with open(config, "w", encoding="utf-8") as fh:
            fh.write("[user]\n\tname = Tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = master\n")
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": config, "GIT_CONFIG_NOSYSTEM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = BoardStore(os.path.join(self.tmp, "board"), clock=lambda: NOW)

    def cards(self, **kw):
        return {c["id"]: c for c in self.store.snapshot(**kw)["cards"]}

    def test_check_is_validated_saved_and_in_snapshot(self):
        self.store.create_card({"title": "a"})
        self.store.create_card({"title": "b", "check": "manual"})
        self.assertEqual({k: c["check"] for k, c in self.cards().items()}, {"K-1": "auto", "K-2": "manual"})
        result = self.store.update_card("K-1", {"check": "MANUAL"})
        self.assertEqual(result["changed"], True)
        self.assertEqual(self.cards()["K-1"]["check"], "manual")
        self.assertFalse(self.store.update_card("K-1", {"check": "manual"})["changed"])
        for bad in ("ręcznie", 1, None):
            with self.assertRaises(BoardError):
                self.store.update_card("K-1", {"check": bad})
        with self.assertRaises(BoardError):
            self.store.create_card({"title": "c", "check": "zawsze"})

    def test_missing_or_bad_check_in_file_reads_as_auto(self):
        self.store.create_card({"title": "a"})
        path = os.path.join(self.store.root, "cards", "K-1.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace("check: auto\n", "check: kiedyś\n"))
        snap = self.store.snapshot()
        self.assertEqual(snap["cards"][0]["check"], "auto")
        self.assertTrue(any("sprawdzenie" in e["message"] for e in snap["errors"]))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace("check: auto\n", ""))
        self.assertEqual(self.store.snapshot()["cards"][0]["check"], "auto")

    def test_moved_is_set_by_every_move_and_not_by_edits(self):
        clock = [NOW]
        self.store.clock = lambda: clock[0]
        self.store.create_card({"title": "a"})
        self.store.create_card({"title": "b"})
        created = self.cards()["K-1"]
        self.assertEqual((created["moved"], created["movedTs"]), (created["created"], NOW))
        clock[0] = NOW + 60
        self.store.update_card("K-1", {"note": "do poprawy: brak testu"})
        self.assertEqual(self.cards()["K-1"]["movedTs"], NOW, "edycja nie przenosi")
        clock[0] = NOW + 90
        self.assertTrue(self.store.move_card("K-1", "backlog", 1)["changed"])  # to samo miejsce: jawny ruch
        self.assertEqual(self.cards()["K-1"]["movedTs"], NOW + 90)
        clock[0] = NOW + 120
        self.store.move_card("K-1", "todo", 0)
        self.assertEqual(self.cards()["K-1"]["movedTs"], NOW + 120)
        path = os.path.join(self.store.root, "cards", "K-2.md")  # stara karta bez klucza moved
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(line for line in text.splitlines(True) if not line.startswith("moved:")))
        card = self.cards()["K-2"]
        self.assertEqual((card["moved"], card["movedTs"]), ("", card["createdTs"]))

    def test_finished_card_moved_back_to_todo_leaves_review(self):
        """„Do poprawy”: karta pokazana w Do sprawdzenia ma zapisane todo — MoveCard do todo też ją stempluje."""
        clock = [NOW]
        self.store.clock = lambda: clock[0]
        self.store.create_card({"title": "a", "column": "todo", "check": "manual", "session": "claude:k"})
        b = board("claude:k", [ticket("A-1", "gotowe", activity=NOW + 100)], state="ended", live=False,
                  activity=NOW + 100)
        deriver = CardDeriver(lambda: [], lambda: [b], branches=FakeBranches(), clock=lambda: NOW + 200)
        first = self.cards(derive=deriver)["K-1"]
        self.assertEqual((first["column"], first["boardColumn"]), ("todo", "review"))
        clock[0] = NOW + 300
        # BoardMonitor podaje kolumnę wyświetlaną (boardColumn) — zapisane jest todo, a widać Do sprawdzenia.
        self.assertEqual(self.store.move_card("K-1", "todo", 0, "review")["changed"], True)
        self.assertEqual(self.cards()["K-1"]["movedFrom"], "review")
        after = self.cards(derive=deriver)["K-1"]
        self.assertEqual(after["boardColumn"], "todo")
        self.assertIn("wróci do W toku", after["derived"]["note"])

    def test_derive_gets_project_repo(self):
        repo = os.path.join(self.tmp, "dom")
        os.makedirs(repo)
        git(repo, "init", "-q", "-b", "master")
        self.store.create_card({"title": "a", "repo": repo, "column": "todo", "session": "claude:k"})
        seen = []
        self.store.snapshot(derive=lambda c: seen.append(c) or None)
        self.assertEqual(seen[0]["repo"], repo)


class SpeedTest(unittest.TestCase):
    def test_rederive_of_a_large_board_is_fast(self):
        """80 kart bez połączenia, 60 koordynatorów × 15 ticketów i 40 sesji — przeliczenie idzie co
        500 ms w wątku głównym, więc musi zostać daleko poniżej tego (próg hojny, zwykle kilka ms)."""
        boards = [board(f"claude:b{i}", [ticket(f"PT-{j}", "gotowe", branch=f"feat/pt-{j}-x") for j in range(15)],
                        state="ended", live=False, branch=f"feat/x-{i}", activity=1000 + i) for i in range(60)]
        sessions = [session(f"claude:s{i}", state="idle", branch=f"feat/s-{i}") for i in range(40)]
        cards = [card(n, branch=f"feat/none-{n}") for n in range(1, 81)]
        cards[0]["branch"] = "feat/pt-3-x"  # jedna karta pasuje do ticketów wszystkich koordynatorów
        deriver = CardDeriver(lambda: sessions, lambda: boards, branches=FakeBranches(), clock=lambda: NOW)
        deriver.repo_of = lambda cwd: os.path.realpath(REPO) if (cwd or "").startswith(REPO) else ""
        best = None
        for _ in range(3):
            start = time.perf_counter()
            ctx = deriver.context()
            results = [ctx(c) for c in cards]
            elapsed = time.perf_counter() - start
            best = elapsed if best is None else min(best, elapsed)
        self.assertEqual(results[0]["coordinator"], "claude:b59")
        self.assertEqual(sum(r is not None for r in results), 1)
        self.assertLess(best, 0.1, f"przeliczenie trwało {best * 1000:.0f} ms")


class FakeSource:
    """Obszar z sygnałem changed (AgentMonitor, CoordinationMonitor) i danymi w atrybucie."""

    def __new__(cls, attr):
        from PyQt6.QtCore import QObject, pyqtSignal

        class Source(QObject):
            changed = pyqtSignal()

        source = Source()
        setattr(source, attr, [])
        return source


class LiveTest(unittest.TestCase):
    """BoardMonitor w procesie: zmiana sesji albo koordynatora → przeliczenie kart i Changed,
    bez ponownego czytania repozytorium tablicy."""

    def setUp(self):
        app()
        self.tmp = tempfile.mkdtemp(prefix="tide-derive-live-")
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

        class Trees:  # obszar Worktrees: lista repozytoriów i sygnał zmiany
            def __init__(self, signal_owner):
                self.changed = signal_owner.changed
                self.scanner = mock.Mock(snapshot=lambda *a: [])
                self.launchers = None

        args = argparse.Namespace(board_dir=os.path.join(self.tmp, "board"))
        self.trees_source = FakeSource("items")  # trzymamy referencję — sygnał żyje z obiektem
        monitor = start_board(None, args, Trees(self.trees_source), self.agents, self.coordination,
                              clock=lambda: NOW)
        monitor._rederive.setInterval(50)
        monitor._debounce.setInterval(60_000)  # zmiany z zewnątrz (nasz własny zapis) nie mieszają w liczeniu odczytów
        self.addCleanup(monitor.stop)
        monitor.start()
        return monitor

    def column(self, monitor, card_id="K-1"):
        snap = json.loads(monitor.snapshot_json())
        return next(c["boardColumn"] for c in snap["cards"] if c["id"] == card_id)

    def test_agents_and_coordination_changes_move_card_live(self):
        monitor = self.start()
        monitor.create_card(json.dumps({"title": "PDF", "repo": self.repo, "column": "todo",
                                        "session": "claude:koord"}))
        self.assertEqual(self.column(monitor), "todo")
        changes = []
        monitor.changed.connect(lambda: changes.append(True))
        with mock.patch.object(monitor.store, "snapshot", wraps=monitor.store.snapshot) as reads:
            self.coordination.boards = [board("claude:koord", [ticket("A-1")], state="idle", repo=self.repo)]
            self.coordination.changed.emit()
            self.assertTrue(wait_until(lambda: changes, timeout=3))
            self.assertEqual(self.column(monitor), "progress")
            changes.clear()
            self.agents.sessions = [session("claude:koord", state="waiting", reason="approval", cwd=self.repo)]
            for _ in range(5):  # seria zmian — jedno przeliczenie
                self.agents.changed.emit()
            self.assertTrue(wait_until(lambda: changes, timeout=3))
            self.assertEqual(self.column(monitor), "waiting")
            self.assertEqual(json.loads(monitor.badge_json()), {"text": "● 1", "tone": "waiting"})
            self.assertEqual(reads.call_count, 0, "przeliczenie bez czytania plików tablicy")
            changes.clear()
            self.agents.changed.emit()  # nic się nie zmieniło — bez Changed
            wait_until(lambda: False, timeout=0.3)
            self.assertEqual(changes, [])
        self.agents.sessions = []
        self.coordination.boards = [board("claude:koord", [ticket("A-1", "gotowe")], state="ended", live=False,
                                          repo=self.repo, activity=time.time() + 60)]  # po zapisie karty
        self.coordination.changed.emit()
        self.assertTrue(wait_until(lambda: self.column(monitor) == "done", timeout=3))
        self.assertEqual(json.loads(monitor.badge_json())["text"], "")
        monitor.move_card("K-1", "done", 0)  # potwierdzenie: ręczna kolumna wygrywa
        snap = json.loads(monitor.snapshot_json())
        self.assertIsNone(snap["cards"][0]["derived"])


if __name__ == "__main__":
    unittest.main()
