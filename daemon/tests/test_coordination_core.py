"""Koordynacja bez Qt: dzienniki koordynatorów (Claude Code i Codex), plany, ID ticketów, review,
stany ticketów, YouTrack na atrapie serwera HTTP, akcje i plik dla raportu.

Dzienniki to syntetyczne fixtures o kształcie prawdziwych (coordfix.py) — bez treści prawdziwych rozmów.
"""
import json
import os
import shutil
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import coordfix as F
from tide_daemon.coordination import actions, board as board_mod, describe, ids, plan as plan_mod, review, youtrack
from tide_daemon.coordination.claude import ClaudeCoordinatorLog, excerpt, final_report
from tide_daemon.coordination.codex import first_message_title
from tide_daemon.coordination.model import Coordinator, Question, Run
from tide_daemon.coordination.scanner import CoordinationScanner, plan_owners, repo_roots

LIVE_CLAUDE = {"id": "claude:" + F.CLAUDE_COORD, "state": "working", "reason": "", "canActivate": True}
LIVE_CODEX = {"id": "codex:" + F.CODEX_ROOT, "state": "waiting", "reason": "question", "canActivate": True}


def tickets(board):
    return {t["id"] or t["key"]: t for t in board["tickets"]}


class World(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-coord-")
        self.addCleanup(shutil.rmtree, self.tmp)


class ClaudeLogTest(World):
    def test_runs_rounds_notifications_peer_reports_and_questions(self):
        world = F.build_world(self.tmp)
        log = ClaudeCoordinatorLog(world["log"].path)
        self.assertTrue(log.update())
        coord = log.coord
        self.assertEqual(coord.id, "claude:" + F.CLAUDE_COORD)
        self.assertEqual(coord.branch, "feat/tom-20-release")
        self.assertEqual(coord.title, "Release candidate TOM-20")
        self.assertEqual(coord.model, "claude-opus-5-5")
        self.assertEqual(len(coord.runs), 12)  # wpis podagenta (isSidechain) w dzienniku głównym nie jest zleceniem

        impl = coord.runs["a140impl0000000001"]
        self.assertEqual(impl.agent_type, "implementer")
        self.assertTrue(impl.background)
        self.assertEqual([r.kind for r in impl.rounds], ["launch", "continue"])
        self.assertEqual(impl.rounds[0].status, "completed")
        self.assertEqual(impl.rounds[0].result, "Gotowe: guard i 6 testów.")  # powiadomienie w wiadomości user
        self.assertEqual(impl.rounds[1].status, "running")
        self.assertEqual(impl.rounds[1].note, "poprawki")
        self.assertIn("tom-140-guard", impl.prompt_head)

        rev = coord.runs["a140rev00000000001"]
        self.assertEqual(rev.state, "completed")
        self.assertIn("MAJOR-1", rev.current.result, "raport z SubagentHandback (peer), nie zaślepka z powiadomienia")
        self.assertNotIn("delivered to you", rev.current.result)
        self.assertEqual(len(rev.rounds), 1, "kolejka, kopia dla API i wiadomość nie dają drugiej rundy")

        fg = coord.runs["a143impl0000000001"]
        self.assertFalse(fg.background)
        self.assertEqual(fg.state, "completed")
        self.assertEqual(fg.current.result, "Retry dodany, 1 commit.")  # bez dopisku agentId
        self.assertEqual(coord.runs["a144impl0000000001"].state, "killed")
        self.assertEqual(coord.runs["a099impl0000000001"].state, "failed")
        self.assertEqual(coord.runs["a142rev00000000001"].state, "running")
        self.assertNotIn("bshell001", coord.runs, "zadanie powłoki w tle nie jest podagentem")

        self.assertEqual(list(coord.questions), ["toolu_ask"])
        self.assertIn("TOM-141", coord.questions["toolu_ask"].text)

    def test_incremental_update_reads_only_appended_lines(self):
        world = F.build_world(self.tmp)
        log = ClaudeCoordinatorLog(world["log"].path)
        log.update()
        offset = log.offset
        self.assertFalse(log.update(), "bez dopisanych bajtów nic się nie zmienia")
        extra = F.ClaudeLog(world["claude"], F.CLAUDE_COORD, world["repo"], "feat/tom-20-release")
        extra.notification("a142rev00000000001", "completed", 0.2, "Review finished",
                           "Verdict: APPROVE. BLOCKER: none, MAJOR: none.")
        extra.tool_result("toolu_ask", "User answered: Tak", 0.1, extra={"answers": {}})
        world["log"].append(extra.lines)
        self.assertTrue(log.update())
        self.assertGreater(log.offset, offset)
        self.assertEqual(log.coord.runs["a142rev00000000001"].state, "completed")
        self.assertEqual(log.coord.questions, {}, "odpowiedź zdejmuje pytanie")

    def test_subagent_transcript_report_and_excerpt(self):
        world = F.build_world(self.tmp)
        log = ClaudeCoordinatorLog(world["log"].path)
        log.update()
        rev = log.coord.runs["a140rev00000000001"]
        self.assertIn("MAJOR-1", final_report(rev.transcript))
        steps = excerpt(log.coord.runs["a140impl0000000001"].transcript)
        self.assertEqual([s["kind"] for s in steps], ["text", "tool"])
        self.assertEqual(steps[1]["text"], "Read · DistributionGuard.cs")


class CodexTest(World):
    def test_root_with_children_grandchild_and_guardian(self):
        world = F.build_world(self.tmp)
        scanner = CoordinationScanner(world["claude"], world["codex"], clock=lambda: F.NOW)
        coords = scanner.read()
        coord = coords["codex:" + F.CODEX_ROOT]
        self.assertEqual(set(coord.runs), {F.CODEX_IMPL, F.CODEX_REVIEW, F.CODEX_PROBE},
                         "wnuk należy do koordynatora, recenzent zgód nie")
        impl = coord.runs[F.CODEX_IMPL]
        self.assertEqual((impl.nickname, impl.agent_type, impl.description), ("Euler", "worker", "tom150 impl"))
        self.assertEqual(impl.state, "completed")
        self.assertEqual(impl.current.result, "Eksport gotowy, 3 commity.")
        self.assertIn("TOM-150", impl.prompt_head)
        self.assertEqual(coord.runs[F.CODEX_PROBE].state, "running")
        self.assertEqual(list(coord.questions), ["call_q"])
        self.assertEqual(coord.title, "Zrób TOM-150 i TOM-151.", "wątek bez nazwy — pierwsza wiadomość, nie gałąź")
        for key in coords:
            self.assertNotIn(F.CODEX_IMPL, key, "podagent nie jest osobnym koordynatorem")
        self.assertNotIn("claude:" + F.CLAUDE_SINGLE, coords, "sesja bez podagentów nie jest koordynatorem")


    def test_first_message_title(self):
        self.assertEqual(first_message_title("## Zrób eksport\nszczegóły"), "Zrób eksport")
        self.assertEqual(first_message_title("<environment_context>x</environment_context>"), "")
        long = first_message_title("Przejrzyj " + "bardzo długie zadanie " * 10)
        self.assertLessEqual(len(long), 80)
        self.assertTrue(long.endswith("…"))


class PlanTest(unittest.TestCase):
    def test_standard_12_columns_and_legacy_table(self):
        head = "---\ntide-plan: 1\ntitle: X\ntracker: local\nticket-prefix: PT\n---\n"
        modern = ("| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                  "| #1 | Pierwszy | todo | | Sonnet | uzasadnienie | standard | Opus | |\n"
                  "| #2 | Drugi | todo | | Opus | trudny | mocny | Sonnet | #1, PT-4 |\n")
        parsed = plan_mod.parse(head + modern)
        self.assertEqual(parsed.errors, [])
        self.assertEqual(parsed.tables, 1)
        self.assertEqual((parsed.rows[1].work_class, parsed.rows[1].reviewer, parsed.rows[1].blockers),
                         ("mocny", "Sonnet", ("#1", "PT-4")))
        legacy = plan_mod.parse(head + "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |\n"
                                "| --- | --- | --- | --- | --- | --- |\n| PT-1 | Stary | todo | | | |\n")
        self.assertEqual((legacy.errors, legacy.tables, legacy.rows[0].blockers), ([], 1, ()))

    def test_standard_13_project_column(self):
        head = "---\ntide-plan: 1\ntitle: X\ntracker: local\nticket-prefix: PT\n---\n"
        full = ("| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery | Projekt |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| PT-1 | Pierwszy | todo | | | | standard | | | Plasma-Tide |\n"
                "| PT-2 | Drugi | todo | | | | | | PT-1 | |\n"
                "| PT-3 | Trzeci | todo | | | | | | | nieznany-slug |\n")
        parsed = plan_mod.parse(head + full)
        self.assertEqual((parsed.errors, parsed.tables), ([], 1))
        self.assertEqual([r.project for r in parsed.rows], ["plasma-tide", "", "nieznany-slug"])
        self.assertEqual(parsed.rows[1].blockers, ("PT-1",))
        # 9 kolumn z pustą ostatnią komórką to nie kolumna Projekt
        nine = plan_mod.parse(head + "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi | Klasa | Review | Blokery |\n"
                              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                              "| PT-1 | A | todo | | | | | | |\n")
        self.assertEqual((nine.errors, nine.rows[0].project), ([], ""))
        for count in (7, 8):
            cols = ["Ticket", "Tytuł", "Stan", "Gałąź", "Agent", "Uwagi", "Klasa", "Review"][:count]
            plan = plan_mod.parse(head + "| " + " | ".join(cols) + " |\n| " + " | ".join(["---"] * count)
                                  + " |\n| PT-1 | A | todo |" + " |" * (count - 3) + "\n")
            self.assertEqual((plan.errors, plan.rows[0].project), ([], ""))

    def test_valid_plan(self):
        text = F.PLAN.format(url="http://10.20.1.159:8111/", session="abc", updated="2026-10-26T10:00:00Z")
        plan = plan_mod.parse(text, "/r/docs/agents/plans/2026-10-26-release.md")
        self.assertEqual(plan.errors, [])
        self.assertEqual((plan.title, plan.tracker, plan.tracker_url, plan.tracker_project, plan.prefix),
                         ("Release candidate TOM-20", "youtrack", "http://10.20.1.159:8111", "LOG", "TOM"))
        self.assertEqual((plan.coordinator, plan.session, plan.status), ("claude", "abc", "active"))
        self.assertEqual(plan.updated_ts, F.NOW - 2 * 3600)
        self.assertEqual([r.ticket for r in plan.rows], ["TOM-139", "TOM-140", "TOM-141", "TOM-145", "TOM-146",
                                                         "TOM-147"])
        row = plan.rows[1]
        self.assertEqual((row.state, row.branch, row.agent, row.note), ("w-toku", "tom-140-guard", "implementer",
                                                                         "po review: 1 MAJOR"))

    def test_not_a_plan_and_invalid_values(self):
        self.assertIsNone(plan_mod.parse("# Zwykły dokument\n| Ticket | Tytuł | Stan |\n"))
        self.assertIsNone(plan_mod.parse("---\ntitle: bez wersji\n---\n"))
        self.assertIsNone(plan_mod.parse("---\ntide-plan: 2\n---\n"))
        text = """---
tide-plan: 1
title: "Zły plan"
tracker: jira
tracker-url: ftp://x
ticket-prefix: tom 1
coordinator: gemini
status: someday
updated: wczoraj
---
| Ticket | Tytuł | Stan |
| --- | --- | --- |
| A-1 | Pierwszy | w toku |
| A-2 | Drugi | zaraz |
|  | pusty wiersz | todo |
"""
        plan = plan_mod.parse(text, "/x/2026-10-26-zly.md", mtime=123.0)
        self.assertEqual(plan.title, "Zły plan")
        self.assertEqual((plan.tracker, plan.tracker_url, plan.prefix, plan.coordinator, plan.status),
                         ("none", "", "", "", "active"))
        self.assertEqual(plan.updated_ts, 123.0, "zły czas → mtime pliku")
        self.assertEqual([(r.ticket, r.state) for r in plan.rows], [("A-1", "w-toku"), ("A-2", "")])
        joined = " | ".join(plan.errors)
        for fragment in ("tracker „jira”", "tracker-url", "ticket-prefix", "coordinator „gemini”", "status „someday”",
                         "updated", "nagłówek tabeli", "A-2: nieznany stan"):
            self.assertIn(fragment, joined)

    def test_goal_paragraph(self):
        head = "---\ntide-plan: 1\ntitle: X\n---\n"
        table = "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |\n| --- | --- | --- | --- | --- | --- |\n| A-1 | a | todo | | | |\n"
        with_goal = plan_mod.parse(head + table + "\n## Notatki\n\nNotatka.\n\n## Cel i bramki\n\nC007 to **pierwszy**\n"
                                   "kazus z `KW`.\n\nDruga część.\n")
        self.assertEqual(with_goal.goal, "C007 to pierwszy kazus z KW.", "akapit pod „Cel…”, bez znaczników")
        first = plan_mod.parse(head + "# Plan\n\nWydanie z eksportem do CSV.\n\n" + table)
        self.assertEqual(first.goal, "Wydanie z eksportem do CSV.", "bez nagłówka „Cel” — pierwszy akapit")
        self.assertEqual(plan_mod.parse(head + table).goal, "")
        self.assertLessEqual(len(plan_mod.parse(head + "## Cel\n\n" + "słowo " * 500).goal), plan_mod.GOAL_MAX)

    def test_youtrack_without_url_is_an_error_and_pick_prefers_session(self):
        plan = plan_mod.parse("---\ntide-plan: 1\ntitle: X\ntracker: youtrack\n---\n")
        self.assertIn("tracker youtrack wymaga tracker-url i tracker-project", plan.errors)
        a = plan_mod.Plan(path="a", session="s1", updated_ts=1)
        b = plan_mod.Plan(path="b", updated_ts=5, coordinator="claude")
        c = plan_mod.Plan(path="c", updated_ts=9, coordinator="codex")
        d = plan_mod.Plan(path="d", updated_ts=20, status="done")
        self.assertIs(plan_mod.pick([a, b, c, d], "s1", "claude"), a)
        self.assertIs(plan_mod.pick([a, b, c, d], "s2", "claude"), b)
        self.assertIs(plan_mod.pick([a, b, c, d], "s2", "codex"), c)
        self.assertIsNone(plan_mod.pick([a, d], "s2", "claude"))

    def test_pick_two_sessionless_plans_by_who_wrote_them(self):
        # PT-24: dwa aktywne plany bez `session` w jednym repozytorium i dwóch koordynatorów Claude —
        # dawniej obaj dostawali najświeższy plan, a raport podpisywał pytanie jednego tytułem planu drugiego.
        older = plan_mod.Plan(path="/r/docs/agents/plans/2026-10-27-local-models.md", title="Lokalne modele",
                              updated_ts=100)
        fresher = plan_mod.Plan(path="/r/docs/agents/plans/2026-10-27-board-and-docker.md",
                                title="Tablica pracy i Docker", updated_ts=200)
        plans = [fresher, older]
        self.assertIs(plan_mod.pick(plans, "a", "claude"), fresher, "bez dowodów — jak dotąd najświeższy")
        mine_a, mine_b = {"2026-10-27-local-models.md": (1, 50.0)}, {"2026-10-27-board-and-docker.md": (1, 40.0)}
        self.assertIs(plan_mod.pick(plans, "a", "claude", written=mine_a, taken=set(mine_b)), older,
                      "plan, który koordynator sam zapisał, wygrywa ze świeższym planem innego")
        self.assertIs(plan_mod.pick(plans, "b", "claude", written=mine_b, taken=set(mine_a)), fresher)
        self.assertIs(plan_mod.pick(plans, "c", "claude", taken=set(mine_a) | set(mine_b)), None,
                      "trzeci koordynator bez planu nie dostaje cudzego")
        self.assertIs(plan_mod.pick(plans, "c", "claude", taken=set(mine_b)), older)
        claimed = plan_mod.Plan(path=older.path, title="Lokalne modele", session="b", updated_ts=100)
        self.assertIs(plan_mod.pick([fresher, claimed], "b", "claude", written=mine_b), claimed,
                      "`session` w planie wygrywa ze wszystkim")

    def test_plan_file_names_from_writes_and_patches(self):
        self.assertEqual(plan_mod.plan_file_name("/r/docs/agents/plans/2026-10-27-local-models.md"),
                         "2026-10-27-local-models.md")
        self.assertEqual(plan_mod.plan_file_name("/board/docs/demo/plans/2026-10-27-x.md"), "2026-10-27-x.md")
        self.assertEqual(plan_mod.plan_file_name("/r/docs/agents/plans/README.md"), "")
        self.assertEqual(plan_mod.plan_file_name("/r/src/plans.py"), "")
        # Łatka apply_patch jako napis JS w kodzie narzędzia `exec` (nowe wiersze jako `\n`) i zwykła.
        js = 'const patch = "*** Begin Patch\\n*** Update File: /r/docs/agents/plans/2026-10-27-a.md\\n@@\\n' \
             '*** Add File: src/x.py\\n*** End Patch";'
        self.assertEqual(plan_mod.patch_plan_files(js), ["2026-10-27-a.md"])
        self.assertEqual(plan_mod.patch_plan_files("*** Begin Patch\n*** Add File: docs/agents/plans/2026-10-28-b.md\n+x\n"),
                         ["2026-10-28-b.md"])


class PlanWritesTest(World):
    """Kto zapisał plan: Coordinator.plan_writes z dziennika Claude (Write/Edit) i Codexa (apply_patch)."""

    def test_claude_write_and_edit_of_plan_files(self):
        repo = os.path.join(self.tmp, "repo")
        log = F.ClaudeLog(os.path.join(self.tmp, "claude"), F.CLAUDE_COORD, repo, "master")
        log.spawn("toolu_a", "aimpl0000000000001", "PT-1: implement", "implementer", 50)
        plan_path = os.path.join(repo, "docs", "agents", "plans", "2026-10-27-local-models.md")
        log.tool_use("toolu_w", "Write", {"file_path": plan_path, "content": "---\ntide-plan: 1\n---\n"}, 40)
        log.tool_use("toolu_e", "Edit", {"file_path": plan_path, "old_string": "a", "new_string": "b"}, 20)
        log.tool_use("toolu_x", "Edit", {"file_path": os.path.join(repo, "src", "plans.py"), "old_string": "a",
                                         "new_string": "b"}, 10)
        log.lines.append({"isSidechain": True, "type": "assistant", "timestamp": F.at(5), "message": {"content": [
            {"type": "tool_use", "id": "s", "name": "Write",
             "input": {"file_path": os.path.join(repo, "docs/agents/plans/2026-10-28-cudzy.md")}}]}})
        log.write()
        reader = ClaudeCoordinatorLog(log.path)
        reader.update()
        self.assertEqual(reader.coord.plan_writes, {"2026-10-27-local-models.md": (2, F.NOW - 20 * 60)},
                         "dwa zapisy planu, czas ostatniego; zwykły plik i wpis podagenta się nie liczą")

    def test_codex_patch_in_exec_tool(self):
        from tide_daemon.coordination.codex import CodexThreadLog
        repo = os.path.join(self.tmp, "repo")
        code = ('const patch = "*** Begin Patch\\n*** Update File: ' + repo
                + '/docs/agents/plans/2026-10-27-board.md\\n@@\\n-a\\n+b\\n*** End Patch";')
        path = F.codex_thread(os.path.join(self.tmp, "codex"), F.CODEX_ROOT, repo, "master", 30, events=[
            F.ev(20, "response_item", {"type": "custom_tool_call", "call_id": "c1", "name": "exec", "input": code}),
            F.ev(15, "response_item", {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "*** Update File: docs/agents/plans/2026-10-28-x.md"}]})])
        log = CodexThreadLog(path)
        log.read_meta()
        log.update()
        self.assertEqual(log.plan_writes, {"2026-10-27-board.md": (1, F.NOW - 20 * 60)}, "tylko wywołanie narzędzia")


SESSIONLESS_PLAN = """---
tide-plan: 1
title: {title}
tracker: local
ticket-prefix: PT
coordinator: claude
status: active
updated: {updated}
---

| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |
| --- | --- | --- | --- | --- | --- |
| PT-1 | {title} | w-toku | | implementer | |
"""


class PlanOwnersTest(World):
    """Czyj jest plan bez `session` (scanner.plan_owners + plan.pick): kto zapisywał go najczęściej, potem
    kto ostatni; plan, który koordynator sam zapisał, nigdy nie jest dla niego cudzy, a cudzym czyni go tylko
    działający koordynator. X — plan starszy, Y — świeższy (bez dowodów wygrywałby u wszystkich)."""

    X, Y = "2026-10-27-local-models.md", "2026-10-27-board-and-docker.md"

    def world(self, writes: dict, live: set, plans=("X", "Y")) -> dict:
        """writes: {sesja: [(„X”|„Y”, minut temu), …]}; live: sesje działające → {sesja: tytuł planu albo None}."""
        repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(repo, ".git"), exist_ok=True)
        with open(os.path.join(repo, ".git", "HEAD"), "w", encoding="utf-8") as fh:
            fh.write("ref: refs/heads/master\n")
        folder = os.path.join(repo, "docs", "agents", "plans")
        os.makedirs(folder, exist_ok=True)
        paths = {"X": os.path.join(folder, self.X), "Y": os.path.join(folder, self.Y)}
        for key, title, ago in (("X", "Plan X", 300), ("Y", "Plan Y", 5)):
            if key in plans:
                with open(paths[key], "w", encoding="utf-8") as fh:
                    fh.write(SESSIONLESS_PLAN.format(title=title, updated=F.at(ago)))
        claude = os.path.join(self.tmp, "claude")
        ids = {}
        for n, (name, items) in enumerate(writes.items()):
            sid = f"c0000000-0000-4000-8000-0000000001{n:02d}"
            ids[name] = sid
            log = F.ClaudeLog(claude, sid, repo, "master")
            log.user_text("Poprowadź plan.", 400)
            log.spawn(f"s{n}", f"a{n:017d}", "PT-1: implement", "implementer", 390)
            log.subagent(f"a{n:017d}", "implementer", "PT-1: implement", f"s{n}", "master", minutes_ago=2)
            for i, (key, ago) in enumerate(items):
                log.tool_use(f"w{n}-{i}", "Edit" if i else "Write", {"file_path": paths[key]}, ago)
            log.write()
        sessions = [{"id": "claude:" + ids[name], "state": "working"} for name in live]
        scanner = CoordinationScanner(claude, os.path.join(self.tmp, "codex"), clock=lambda: F.NOW,
                                      registry=os.path.join(self.tmp, "ids.json"))
        boards = {b["id"]: b for b in scanner.refresh(sessions)}
        return {name: (boards["claude:" + sid]["plan"] or {}).get("title") for name, sid in ids.items()}

    def test_one_edit_of_anothers_plan_does_not_steal_it(self):
        # B poprawił raz plan A (ostatni zapis X jest B), ale A pisał X trzy razy, a B swój Y trzy razy.
        got = self.world({"A": [("X", 200), ("X", 150), ("X", 100)],
                          "B": [("Y", 190), ("Y", 140), ("Y", 90), ("X", 10)]}, live={"A", "B"})
        self.assertEqual(got, {"A": "Plan X", "B": "Plan Y"})

    def test_handoff_goes_to_the_coordinator_that_wrote_more(self):
        coords = [Coordinator(id="claude:a", tool="claude", session_id="a", path="", plan_writes={self.X: (1, 50.0)}),
                  Coordinator(id="claude:c", tool="claude", session_id="c", path="", plan_writes={self.X: (2, 40.0)})]
        owners = plan_owners(coords, {"claude:a": "/r", "claude:c": "/r"})
        self.assertEqual(owners[("/r", self.X)], ((2, 40.0), "claude:c"))
        coords[0].plan_writes[self.X] = (2, 60.0)
        self.assertEqual(plan_owners(coords, {"claude:a": "/r", "claude:c": "/r"})[("/r", self.X)][1], "claude:a",
                         "remis liczby zapisów — ostatni")
        # Następca przejął plan (zapisał go więcej razy) — trzeci koordynator go nie dostaje, następca tak.
        got = self.world({"A": [("X", 200)], "C": [("X", 100), ("X", 50)], "D": []}, live={"A", "C", "D"},
                         plans=("X",))
        self.assertEqual(got, {"A": "Plan X", "C": "Plan X", "D": None})

    def test_only_a_running_coordinator_makes_a_plan_taken(self):
        writes = {"A": [("X", 200), ("X", 100)], "N": []}
        self.assertEqual(self.world(writes, live={"N"}, plans=("X",)), {"A": "Plan X", "N": "Plan X"},
                         "zakończony koordynator nie blokuje planu nowemu")
        shutil.rmtree(self.tmp)
        os.makedirs(self.tmp)
        self.assertEqual(self.world(writes, live={"A", "N"}, plans=("X",)), {"A": "Plan X", "N": None})


class IdsTest(World):
    def test_prefix_generic_hash_and_ignore(self):
        m = ids.TicketMatcher(["LA"])
        self.assertEqual(m.find("Review LA-035 code part"), "LA-35")
        self.assertEqual(m.find("la029_adapter"), "LA-29")
        self.assertEqual(m.show("LA-29"), "LA-029")
        self.assertEqual(m.find("codex/la-006-coverage"), "LA-6")
        self.assertEqual(m.find("nic tu nie ma"), "")
        g = ids.TicketMatcher()
        self.assertEqual(g.find("OA-5 correction rounds"), "OA-5")
        self.assertEqual(g.find("New agent task launcher (#22)"), "#22")
        self.assertEqual(g.find("UTF-8 and SHA-256 cleanup"), "")
        self.assertEqual(g.find("Implement ticket 01 log value"), "")
        self.assertEqual(ids.TicketMatcher(ignore={"TOM-20"}).find("TOM-20 RC, potem TOM-21"), "TOM-21")
        self.assertEqual(g.strip("LA-028: tax tails"), "tax tails")

    def test_document_ids_are_not_tickets(self):
        g = ids.TicketMatcher()
        for text in ("Zgodnie z ADR-0016", "Niezmiennik INV-001", "Wymaganie REQ-12", "NFR-3 wydajność", "AC-2 spełnione"):
            self.assertEqual(g.find(text), "", text)
        g.learn(["Implement ADR-0016 rule", "Check INV-001"])
        self.assertEqual(g.prefixes, [], "skróty dokumentów nie są prefiksem ticketów")
        self.assertEqual(ids.TicketMatcher(["ADR"]).find("ADR-0016"), "ADR-16", "prefiks z planu wygrywa")

    def test_learn_prefix_from_descriptions_and_task_names(self):
        m = ids.TicketMatcher()
        m.learn(["Review LA-035 code part", "LA-027 A+B", "Repair failures", "UTF-8 fix"])
        self.assertEqual(m.prefixes, ["LA"])
        self.assertEqual(m.find("la039_review"), "LA-39")
        n = ids.TicketMatcher()
        n.learn(["tom142_implementation", "phase1_probe", "step 3 cleanup"])
        self.assertEqual(n.prefixes, ["TOM"])

    def test_migration_map(self):
        folder = os.path.join(self.tmp, "repo", "docs", "agents", "youtrack-migration")
        os.makedirs(folder)
        with open(os.path.join(folder, "map.csv"), "w", encoding="utf-8") as fh:
            fh.write("source_id,youtrack_id,youtrack_url,kind\nLA-001,LEGAL-1,u,ticket\nLA-E01,LEGAL-90,u,theme\n")
        aliases = ids.load_aliases([os.path.join(self.tmp, "nie-ma"), os.path.join(self.tmp, "repo")])
        self.assertEqual(aliases, {"LA-1": "LEGAL-1"})
        m = ids.TicketMatcher(["LA"], aliases)
        self.assertEqual(m.find("LA-001 working library"), "LEGAL-1")
        self.assertEqual(m.show("LEGAL-1"), "LEGAL-1")


class ReviewTest(unittest.TestCase):
    def test_formats(self):
        cases = [
            ("1 MAJOR, 2 NIT", (0, 1, 2, False)),
            ("**BLOCKER:** brak.\n**MAJOR:** brak.\n**NIT:**\n- a\n- b", (0, 0, 2, True)),
            (F.REVIEW_MAJOR, (0, 1, 2, False)),
            (F.REVIEW_OK, (0, 0, 0, True)),
            ("## Werdykt: ACCEPT WITH NITS\n### NIT-1: x\n### NIT-2: y", (0, 0, 2, True)),
            ("Werdykt: REQUEST CHANGES. Nie ma blockerów, są 4 problemy MAJOR.", (0, 4, 0, False)),
            ("Verdict: APPROVE. MAJOR 1–4 z poprzedniego review są zamknięte.", (0, 0, 0, True)),
            ("### Major\n1. **Brak testu** dla pustego manifestu\n2. **Wyciek** szczegółu\n### Nits\n", (0, 2, 0, False)),
            ("Nothing to report.", (0, 0, 0, True)),
        ]
        for text, (b, m, n, ok) in cases:
            result = review.parse(text)
            self.assertEqual((result["blocker"], result["major"], result["nit"], result["ok"]), (b, m, n, ok), text)
        self.assertEqual(review.parse(F.REVIEW_MAJOR)["top"],
                         "MAJOR: guard nie obejmuje ścieżki z pustym manifestem — brak testu")
        self.assertEqual(review.summary_text(review.parse("1 MAJOR, 2 NIT")), "1 MAJOR, 2 NIT")

    def test_who_is_a_reviewer(self):
        self.assertTrue(review.is_review("code-reviewer", "Anything"))
        self.assertTrue(review.is_review("general-purpose", "Review TOM-140 guard"))
        self.assertTrue(review.is_review("general-purpose", "TOM-140: review"))
        self.assertTrue(review.is_review("general-purpose", "Privacy review of VIN mask"))
        self.assertFalse(review.is_review("renewal-implementer", "LA-028 review fix round"))
        self.assertFalse(review.is_review("general-purpose", "Apply post-review fixes"))
        self.assertFalse(review.is_review("general-purpose", "Implement guard"))


def coordinator(runs, questions=(), branch="feat/x"):
    coord = Coordinator(id="claude:s", tool="claude", session_id="s", path="/p", cwd="/w", branch=branch)
    for run in runs:
        coord.runs[run.id] = run
    for q in questions:
        coord.questions[q.id] = q
    return coord


def run(run_id, description, kind="implementer", start=100, end=None, status="completed", result=""):
    r = Run(id=run_id, tool="claude", description=description, agent_type=kind)
    r.begin(F.NOW - start * 60)
    if end is not None:
        r.finish(F.NOW - end * 60, status, result=result)
    r.last_ts = F.NOW - (end if end is not None else start) * 60
    return r


class BoardRulesTest(unittest.TestCase):
    def build(self, runs, questions=(), plan=None, live=True, issues=None):
        return board_mod.build(coordinator(runs, questions), plan=plan, live={"state": "working"} if live else None,
                               now=F.NOW, issues=issues)

    def state(self, board, key):
        return tickets(board)[key]["state"]

    def test_states_from_runs(self):
        b = self.build([
            run("a1", "AB-1: implement", end=None),
            run("a2", "AB-2: implement", end=50), run("r2", "AB-2: review", "code-reviewer", start=40, end=None),
            run("a3", "AB-3: implement", end=50), run("r3", "AB-3: review", "code-reviewer", 40, 30,
                                                       result="Verdict: APPROVE"),
            run("a4", "AB-4: implement", end=50), run("r4", "AB-4: review", "code-reviewer", 40, 30,
                                                       result="1 MAJOR: brak testu"),
            run("a5", "AB-5: implement", end=50), run("r5", "AB-5: review", "code-reviewer", 40, 30, result="2 MAJOR"),
            run("f5", "AB-5: fix round", start=20, end=10),
            run("a6", "AB-6: implement", end=10),
            run("a7", "AB-7: implement", end=None),
        ], questions=[Question("q", F.NOW - 300, "AB-7: czy mogę usunąć tabelę?")])
        self.assertEqual(self.state(b, "AB-1"), "w-toku")
        self.assertEqual(self.state(b, "AB-2"), "review")
        self.assertEqual(self.state(b, "AB-3"), "gotowe")
        self.assertEqual(tickets(b)["AB-3"]["note"], "review OK")
        self.assertEqual(self.state(b, "AB-4"), "w-toku")
        self.assertIn("1 MAJOR", tickets(b)["AB-4"]["note"])
        self.assertEqual(tickets(b)["AB-4"]["review"]["major"], 1)
        self.assertEqual(self.state(b, "AB-5"), "gotowe", "poprawki po review skończone")
        self.assertEqual(self.state(b, "AB-6"), "review", "koordynator działa i zleca review — czeka na review")
        self.assertEqual(tickets(b)["AB-6"]["note"], "gotowe, czeka na review")
        self.assertEqual(self.state(b, "AB-7"), "czeka")
        self.assertIn("czy mogę usunąć tabelę", tickets(b)["AB-7"]["note"])
        self.assertEqual(b["counts"]["review"], 2)
        self.assertEqual(b["progress"]["text"], "2/7")

    def test_ended_coordinator_interrupts_runs_and_closes_unreviewed_work(self):
        b = self.build([run("a1", "AB-1: implement", end=None), run("a2", "AB-2: implement", end=50),
                        run("r9", "AB-9: review", "code-reviewer", 40, 30, result="ok")], live=False)
        self.assertEqual(self.state(b, "AB-1"), "w-toku")
        self.assertEqual(tickets(b)["AB-1"]["note"], "przerwany — koordynator już nie działa")
        self.assertEqual(self.state(b, "AB-2"), "gotowe")
        self.assertEqual(tickets(b)["AB-2"]["note"], "bez review")

    def test_plan_wins_for_rejected_untouched_and_newer(self):
        text = """---
tide-plan: 1
title: Plan
ticket-prefix: AB
updated: {updated}
---
| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |
| --- | --- | --- | --- | --- | --- |
| AB-1 | Pierwszy | odrzucone | | | |
| AB-2 | Drugi | todo | `a-2-x` | | |
| AB-3 | Trzeci | gotowe | | | ręcznie |
| AB-4 | Czwarty | review | | | |
"""
        old = plan_mod.parse(text.format(updated="2026-10-26T09:00:00Z"))
        runs = [run("a1", "AB-1: implement", end=None), run("a3", "AB-3: implement", end=None),
                run("a4", "AB-4: implement", start=250, end=200)]
        b = self.build(runs, plan=old)
        self.assertEqual(self.state(b, "AB-1"), "odrzucone")
        self.assertEqual(tickets(b)["AB-1"]["column"], "gotowe")
        self.assertEqual(self.state(b, "AB-2"), "todo")
        self.assertEqual(tickets(b)["AB-2"]["branch"], "a-2-x")
        self.assertEqual(self.state(b, "AB-3"), "w-toku", "plan starszy niż praca podagenta")
        self.assertEqual(self.state(b, "AB-4"), "review", "plan nowszy niż ostatni przebieg")
        self.assertEqual(tickets(b)["AB-4"]["source"], "plan")
        self.assertEqual(b["counts"]["total"], 3, "odrzucone nie liczą się do postępu")
        self.assertEqual([t["id"] for t in b["tickets"][:4]], ["AB-1", "AB-2", "AB-3", "AB-4"], "kolejność planu")

    def test_youtrack_fills_titles_and_closed_states(self):
        issues = {"AB-1": {"id": "AB-1", "summary": "Z YouTracka", "state": "Fixed", "url": "u/A-1",
                           "description": "Opis\n\nz   YouTracka " + "x" * 900},
                  "AB-2": {"id": "AB-2", "summary": "Drugi", "state": "In Progress", "url": "u/A-2"}}
        first = run("a1", "AB-1: implement", end=50, status="failed")
        first.prompt_head = "AB-1: dodaj   eksport\n" + "y" * 600
        b = self.build([first], issues=issues)
        t = tickets(b)["AB-1"]
        self.assertEqual((t["title"], t["state"], t["source"], t["url"]), ("Z YouTracka", "gotowe", "tracker", "u/A-1"))
        self.assertTrue(t["description"].startswith("Opis\n\nz   YouTracka x"), "początek opisu do raportu porannego")
        self.assertLessEqual(len(t["description"]), 600)
        self.assertTrue(t["brief"].startswith("AB-1: dodaj eksport y"), "początek polecenia podagenta")
        self.assertLessEqual(len(t["brief"]), 400)
        hidden = run("a2", "AB-2: implement", end=40)
        hidden.prompt_head = "gAAAAABq37fPgp-FOPkSz62gzXvO0jATx5k68KvKAbiGab1weHwrmdN4QFJeUeAY0ztxIAl3u5j5F93Ectmou"
        b = self.build([hidden], issues=issues)
        self.assertEqual(tickets(b)["AB-2"]["brief"], "", "zaszyfrowane polecenie Codexa to nie treść")

    def test_ids_from_prompt_body_only_with_plan_prefix(self):
        first = run("i1", "Implement ticket 01 log value", end=50)
        first.prompt_head = "Jesteś wykonawcą zadania 01. Zgodnie z ADR-0016 i niezmiennikiem INV-001 dodaj LOG-5."
        second = run("i2", "Implement ticket 02 log value", end=40)
        second.prompt_head = "Implementujesz zadanie 02. INV-001 musi zostać. Zobacz LOG-5."
        rev = run("r2", "Review ticket 02 log value", "code-reviewer", 30, 20, result="Verdict: APPROVE")
        b = self.build([first, second, rev])
        self.assertEqual([(t["key"], t["id"]) for t in b["tickets"]],
                         [("~ticket 01 log value", ""), ("~ticket 02 log value", "")],
                         "ID z treści polecenia (ADR, INV, a bez planu żadne) nie grupują ticketów")
        plan = plan_mod.parse("---\ntide-plan: 1\ntitle: P\nticket-prefix: AB\nupdated: 2026-10-26T09:00:00Z\n---\n"
                              "| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |\n| --- | --- | --- | --- | --- | --- |\n"
                              "| AB-9 | Eksport | todo | | | |\n")
        work = run("i9", "Implement export", end=10)
        work.prompt_head = "Zadanie AB-9: eksport do CSV."
        b = self.build([work], plan=plan)
        self.assertEqual([t["key"] for t in b["tickets"]], ["AB-9"], "z prefiksem z planu polecenie wskazuje ticket")

    def test_research_waiting_and_skipped_review(self):
        b = self.build([
            run("e1", "Map condition path", "Explore", 100, 90, result="Ścieżka warunku: A → B."),
            run("a2", "AB-2: implement", start=300, end=250),
            run("r2", "AB-2: review", "code-reviewer", 150, 140, result="Verdict: APPROVE"),
            run("a1", "AB-1: implement", start=260, end=200),
            run("a3", "AB-3: implement", start=80, end=60),
        ])
        t = tickets(b)
        self.assertEqual((t["~map condition path"]["state"], t["~map condition path"]["note"]),
                         ("gotowe", "rozpoznanie skończone"), "samo rozpoznanie nie czeka na review")
        self.assertEqual((t["AB-1"]["state"], t["AB-1"]["note"]), ("gotowe", "bez review"),
                         "po końcu pracy koordynator zlecał review innym, a minęły ponad 2 h — review pominięte")
        self.assertEqual((t["AB-3"]["state"], t["AB-3"]["note"]), ("review", "gotowe, czeka na review"))
        events = t["~map condition path"]["events"]
        self.assertEqual([e["title"] for e in events], ["Koordynator zlecił rozpoznanie", "Rozpoznanie skończone"])
        self.assertEqual(events[1]["text"], "Ścieżka warunku: A → B.")

    def test_fallback_grouping_and_coordinator_id(self):
        b = board_mod.build(coordinator([
            run("s1", "Implement anon key sync tool", end=50),
            run("s2", "Review anon key sync change", "code-reviewer", 40, 30, result="Verdict: APPROVE"),
            run("t1", "Implement ticket 01 log value", end=20), run("t2", "Implement ticket 02 log value", end=10),
            run("x1", "TOM-20: release notes", end=5), run("x2", "TOM-21: export", end=5),
        ], branch="codex/tom-20-release"), live=None, now=F.NOW)
        keys = [t["key"] for t in b["tickets"]]
        self.assertEqual(keys.count("~anon key sync tool"), 1)
        self.assertEqual(tickets(b)["~anon key sync tool"]["state"], "gotowe")
        self.assertIn("~ticket 01 log value", keys)
        self.assertIn("~ticket 02 log value", keys)
        self.assertIn("TOM-21", keys)
        self.assertNotIn("TOM-20", keys, "ID samego koordynatora (z gałęzi) nie jest ticketem")
        self.assertEqual(b["label"], "TOM-20")


class FakeYouTrack(BaseHTTPRequestHandler):
    calls: list = []
    status = 200

    def do_GET(self):
        FakeYouTrack.calls.append((self.command, self.path, self.headers.get("Authorization")))
        if FakeYouTrack.status != 200:
            self.send_response(FakeYouTrack.status)
            self.end_headers()
            return
        body = json.dumps([
            {"idReadable": "LOG-7", "summary": "Stary klucz", "customFields": [
                {"name": "Priority", "value": {"name": "Normal"}}, {"name": "State", "value": {"name": "Fixed"}}]},
            {"idReadable": "TOM-146", "summary": "CSV z YouTracka", "description": "Eksport wyników do CSV. " * 100,
             "customFields": [
                {"name": "State", "value": {"name": "Submitted"}}]},
            {"idReadable": "TOM-141", "summary": "Migracja", "customFields": [{"name": "State", "value": None}]},
        ]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def _refuse(self):
        FakeYouTrack.calls.append((self.command, self.path, None))
        self.send_response(405)
        self.end_headers()

    do_POST = do_PUT = do_DELETE = do_PATCH = _refuse

    def log_message(self, *args):
        pass


class YouTrackTest(World):
    def setUp(self):
        super().setUp()
        FakeYouTrack.calls = []
        FakeYouTrack.status = 200
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeYouTrack)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.token_file = os.path.join(self.tmp, "token")
        with open(self.token_file, "w", encoding="utf-8") as fh:
            fh.write(F.TOKEN_SENTINEL)
        self.config = {"url": self.url, "tokenFile": self.token_file, "tokenVar": ""}

    def cache(self, clock=lambda: F.NOW, **kwargs):
        return youtrack.YouTrackCache(config_loader=lambda: dict(self.config), clock=clock, **kwargs)

    def test_get_only_token_in_header_and_ttl(self):
        now = [F.NOW]
        cache = self.cache(clock=lambda: now[0])
        issues, info = cache.get(self.url + "/", "LOG")
        self.assertTrue(info["available"])
        self.assertEqual(issues["LOG-7"], {"id": "LOG-7", "summary": "Stary klucz", "state": "Fixed",
                                           "url": self.url + "/issue/LOG-7", "description": ""})
        self.assertEqual(issues["TOM-141"]["state"], "")
        self.assertEqual(len(issues["TOM-146"]["description"]), youtrack.DESCRIPTION_MAX, "początek opisu")
        self.assertEqual(len(FakeYouTrack.calls), 1)
        method, path, auth = FakeYouTrack.calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(path.startswith("/api/issues?"))
        self.assertIn("query=project%3A+LOG", path)
        self.assertIn("fields=idReadable%2Csummary%2Cdescription%2CcustomFields%28name%2Cvalue%28name%29%29", path)
        self.assertNotIn(F.TOKEN_SENTINEL, path)
        self.assertEqual(auth, "Bearer " + F.TOKEN_SENTINEL)
        cache.get(self.url, "LOG")
        self.assertEqual(len(FakeYouTrack.calls), 1, "pamięć podręczna przez 5 min")
        now[0] += youtrack.TTL + 1
        cache.get(self.url, "LOG")
        self.assertEqual(len(FakeYouTrack.calls), 2)
        cache.get(self.url, "LOG", force=True)
        self.assertEqual(len(FakeYouTrack.calls), 3)
        self.assertNotIn(F.TOKEN_SENTINEL, json.dumps(info))

    def test_errors_never_contain_the_token_and_keep_last_data(self):
        now = [F.NOW]
        cache = self.cache(clock=lambda: now[0])
        cache.get(self.url, "LOG")
        FakeYouTrack.status = 401
        now[0] += youtrack.TTL + 1
        issues, info = cache.get(self.url, "LOG")
        self.assertEqual(info["error"], "YouTrack odpowiedział 401")
        self.assertFalse(info["available"])
        self.assertIn("LOG-7", issues, "ostatnie dobre dane zostają")
        self.server.shutdown()
        self.server.server_close()
        now[0] += youtrack.TTL + 1
        issues, info = cache.get(self.url, "LOG")
        self.assertEqual(info["error"], "YouTrack nie odpowiada")
        self.assertNotIn(F.TOKEN_SENTINEL, json.dumps(info))
        with self.assertRaises(youtrack.YouTrackError) as caught:
            youtrack.fetch_issues(self.url, "LOG", F.TOKEN_SENTINEL, timeout=0.5)
        self.assertNotIn(F.TOKEN_SENTINEL, str(caught.exception))
        self.assertNotIn(F.TOKEN_SENTINEL, repr(caught.exception))

    def test_other_server_and_missing_token_are_not_asked(self):
        cache = self.cache()
        _issues, info = cache.get("http://example.invalid:8111", "LOG")
        self.assertIn("inny serwer", info["error"])
        self.config["tokenFile"] = os.path.join(self.tmp, "brak")
        _issues, info = self.cache().get(self.url, "LOG")
        self.assertTrue(info["error"].startswith("brak tokenu YouTracka (sprawdzone: "), info["error"])
        self.assertIn(self.config["tokenFile"], info["error"], "komunikat podaje sprawdzone ścieżki")
        self.assertEqual(FakeYouTrack.calls, [])

    def test_token_from_env_file_variable_and_config_defaults(self):
        env_file = os.path.join(self.tmp, "logviewer.env")
        with open(env_file, "w", encoding="utf-8") as fh:
            fh.write("# komentarz\nOTHER=1\nexport YOUTRACK_TOKEN=\"" + F.TOKEN_SENTINEL + "\"\n")
        self.assertEqual(youtrack.read_token({"tokenFile": env_file, "tokenVar": "YOUTRACK_TOKEN"}), F.TOKEN_SENTINEL)
        self.assertEqual(youtrack.read_token({"tokenFile": env_file, "tokenVar": "MISSING"}), "")
        home = os.path.join(self.tmp, "home")
        config = youtrack.load_config(os.path.join(self.tmp, "nie-ma.json"), env={"HOME": home})
        self.assertEqual(config["url"], youtrack.DEFAULT_URL)
        self.assertEqual(config["tokenFile"], os.path.join(home, ".config/youtrack/token"), "bez żadnego pliku — pierwsze miejsce")
        self.assertEqual(config["tokenFiles"], [[os.path.join(home, ".config/youtrack/token"), ""],
                                                [os.path.join(home, ".config/logviewer/youtrack-mcp-token"), ""]])
        self.assertEqual(youtrack.read_token(config), "")
        path = os.path.join(self.tmp, "youtrack.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"url": "http://yt:8111/", "tokenFile": "/t", "tokenVar": "V"}, fh)
        config = youtrack.load_config(path, env={"HOME": home})
        self.assertEqual((config["url"], config["tokenFile"], config["tokenVar"]), ("http://yt:8111", "/t", "V"),
                         "plik z ustawień pierwszy (nawet gdy go nie ma)")
        self.assertEqual(config["tokenFiles"][0], ["/t", "V"])

    def test_token_discovery_order_without_config_file(self):
        home = os.path.join(self.tmp, "home")
        legacy = os.path.join(home, ".config", "logviewer", "youtrack-mcp-token")
        mcp = os.path.join(home, ".config", "youtrack", "token")
        os.makedirs(os.path.dirname(legacy))
        with open(legacy, "w", encoding="utf-8") as fh:
            fh.write("legacy-" + F.TOKEN_SENTINEL + "\n")
        none = os.path.join(self.tmp, "brak.json")
        config = youtrack.load_config(none, env={"HOME": home})
        self.assertEqual(config["tokenFile"], legacy, "tylko dawne miejsce LogViewera")
        self.assertEqual(youtrack.read_token(config), "legacy-" + F.TOKEN_SENTINEL)
        os.makedirs(os.path.dirname(mcp))
        with open(mcp, "w", encoding="utf-8") as fh:
            fh.write(F.TOKEN_SENTINEL + "\n")
        config = youtrack.load_config(none, env={"HOME": home})
        self.assertEqual((config["tokenFile"], youtrack.read_token(config)), (mcp, F.TOKEN_SENTINEL),
                         "~/.config/youtrack/token przed LogViewerem")
        self.assertEqual(config["url"], "http://10.20.1.159:8111")
        settings = os.path.join(self.tmp, "youtrack.json")
        own = os.path.join(self.tmp, "own-token")
        with open(settings, "w", encoding="utf-8") as fh:
            json.dump({"tokenFile": own}, fh)
        config = youtrack.load_config(settings, env={"HOME": home})
        self.assertEqual(youtrack.read_token(config), F.TOKEN_SENTINEL, "brak pliku z ustawień — następne miejsce")
        with open(own, "w", encoding="utf-8") as fh:
            fh.write("own-token-value")
        self.assertEqual(youtrack.read_token(youtrack.load_config(settings, env={"HOME": home})), "own-token-value")
        # Pamięć podręczna koordynacji bierze token tak samo — tylko w nagłówku, nigdy w komunikatach.
        empty = os.path.join(self.tmp, "empty-home")
        cache = youtrack.YouTrackCache(config_loader=lambda: dict(youtrack.load_config(none, env={"HOME": empty}),
                                                                  url=self.url), clock=lambda: F.NOW)
        _issues, info = cache.get(self.url, "LOG")
        self.assertIn(os.path.join(empty, ".config/youtrack/token"), info["error"])
        self.assertEqual(FakeYouTrack.calls, [])
        cache = youtrack.YouTrackCache(config_loader=lambda: dict(youtrack.load_config(none, env={"HOME": home}),
                                                                  url=self.url), clock=lambda: F.NOW)
        issues, info = cache.get(self.url, "LOG")
        self.assertTrue(info["available"])
        self.assertEqual(FakeYouTrack.calls[-1][2], "Bearer " + F.TOKEN_SENTINEL)
        self.assertNotIn(F.TOKEN_SENTINEL, json.dumps(info))

    def test_scanner_board_with_tracker(self):
        world = F.build_world(self.tmp, plan_url=self.url)
        scanner = CoordinationScanner(world["claude"], world["codex"], clock=lambda: F.NOW, tracker=self.cache())
        boards = {b["id"]: b for b in scanner.refresh([LIVE_CLAUDE])}
        b = boards["claude:" + F.CLAUDE_COORD]
        self.assertTrue(b["tracker"]["available"])
        t = tickets(b)
        self.assertEqual(t["LOG-7"]["state"], "gotowe", "zamknięty w YouTracku, nikt nad nim nie pracuje")
        self.assertEqual(t["LOG-7"]["url"], self.url + "/issue/LOG-7")
        self.assertEqual(t["TOM-146"]["title"], "Eksport raportu do CSV z filtrami", "tytuł z planu wygrywa")
        self.assertEqual(t["TOM-146"]["url"], self.url + "/issue/TOM-146")
        self.assertNotIn(F.TOKEN_SENTINEL, json.dumps(boards))


class ScannerTest(World):
    def test_codex_coordinator_started_before_the_window_is_found_by_id(self):
        # Codex zapisuje wątek w katalogu dnia założenia: koordynator sprzed dwóch tygodni,
        # który dziś wciąż zleca, leży poza oknem 7 dni, a jego podagenci — w oknie.
        world = F.build_world(self.tmp)
        day = os.path.join(world["codex"], "sessions", "2026", "10", "26")
        old = os.path.join(world["codex"], "sessions", "2026", "10", "10")
        os.makedirs(old)
        name = next(n for n in os.listdir(day) if n.endswith(F.CODEX_ROOT + ".jsonl"))
        src, dst = os.path.join(day, name), os.path.join(old, name)
        st = os.stat(src)
        os.rename(src, dst)
        os.utime(dst, (st.st_atime, st.st_mtime))
        scanner = CoordinationScanner(world["claude"], world["codex"], clock=lambda: F.NOW)
        boards = scanner.refresh([LIVE_CLAUDE, LIVE_CODEX])
        codex = next((x for x in boards if x["id"] == "codex:" + F.CODEX_ROOT), None)
        self.assertIsNotNone(codex, "rodzic spoza okna dni musi zostać znaleziony")
        self.assertEqual({k: v["state"] for k, v in tickets(codex).items()}, {"TOM-150": "gotowe", "TOM-151": "czeka"})
        # Archiwum też się liczy.
        archived = os.path.join(world["codex"], "archived_sessions")
        os.makedirs(archived)
        os.rename(dst, os.path.join(archived, name))
        fresh = CoordinationScanner(world["claude"], world["codex"], clock=lambda: F.NOW)
        self.assertIn("codex:" + F.CODEX_ROOT, [b["id"] for b in fresh.refresh([LIVE_CLAUDE, LIVE_CODEX])])

    def test_boards_from_fixtures(self):
        world = F.build_world(self.tmp)
        scanner = CoordinationScanner(world["claude"], world["codex"], clock=lambda: F.NOW)
        boards = scanner.refresh([LIVE_CLAUDE, LIVE_CODEX])
        self.assertEqual([b["id"] for b in boards][:1], ["claude:" + F.CLAUDE_COORD])
        b = boards[0]
        self.assertEqual((b["title"], b["label"], b["project"], b["live"], b["model"]),
                         ("Release candidate TOM-20", "TOM-20", "demo", True, "Opus"))
        self.assertEqual(b["repoPath"], world["repo"])
        self.assertEqual(b["plan"]["path"], os.path.join(world["repo"], "docs/agents/plans/2026-10-26-release.md"))
        t = tickets(b)
        expected = {"TOM-139": "gotowe", "TOM-140": "w-toku", "TOM-141": "czeka", "TOM-142": "review",
                    "TOM-143": "review", "TOM-144": "w-toku", "TOM-145": "odrzucone", "TOM-146": "todo",
                    "TOM-147": "todo", "LOG-7": "w-toku", "~survey test suites": "gotowe"}
        self.assertEqual({k: v["state"] for k, v in t.items()}, expected)
        self.assertEqual(t["TOM-140"]["branch"], "tom-140-guard")
        agents = t["TOM-140"]["agents"]
        self.assertEqual([(a["label"], a["state"], a["note"]) for a in agents],
                         [("implementer", "completed", ""), ("code-reviewer", "completed", ""),
                          ("implementer", "running", "poprawki")], "wznowienie to osobny wiersz na karcie")
        self.assertEqual(agents[0]["endTs"] - agents[0]["startTs"], 31 * 60)
        events = t["TOM-140"]["events"]
        self.assertEqual([e["kind"] for e in events], ["launch", "finished", "launch", "finished", "continue", "running"])
        # Oś czasu po polsku: rola słowem, uwagi review słowami, angielski opis zlecenia w drobnym dopisku.
        self.assertEqual([e["title"] for e in events],
                         ["Koordynator zlecił implementację", "Implementacja skończona", "Koordynator zlecił review",
                          "Review: 1 poważna uwaga i 2 drobne", "Koordynator zlecił poprawki", "Trwają poprawki"])
        self.assertEqual(events[3]["text"], "do poprawy")
        self.assertEqual(events[3]["review"]["major"], 1)
        self.assertEqual(events[0]["meta"], "„TOM-140: implement distribution guard” · implementer · Sonnet · tło")
        self.assertEqual([a["word"] for a in agents], ["implementacja", "review", "poprawki"])
        self.assertEqual(events[3]["quote"], "MAJOR: guard nie obejmuje ścieżki z pustym manifestem — brak testu")
        self.assertEqual(events[-1]["text"], "Read · DistributionGuard.cs")
        self.assertEqual(t["TOM-141"]["question"]["text"], "TOM-141: przenieść lokalny backlog do osobnego projektu?")
        self.assertEqual(b["counts"], {"todo": 2, "w-toku": 3, "czeka": 1, "review": 2, "gotowe": 2, "odrzucone": 1,
                                       "total": 10, "done": 2})
        self.assertEqual(b["progress"]["text"], "TOM-20 · 2/10")
        codex = next(x for x in boards if x["id"] == "codex:" + F.CODEX_ROOT)
        self.assertEqual({k: v["state"] for k, v in tickets(codex).items()}, {"TOM-150": "gotowe", "TOM-151": "czeka"})
        self.assertIsNone(codex["plan"], "plan wskazuje sesję Claude")

        detail = scanner.run_detail("claude:" + F.CLAUDE_COORD, "a140rev00000000001")
        self.assertIn("MAJOR-1", detail["result"])
        self.assertEqual(detail["label"], "code-reviewer")
        self.assertIsNone(scanner.run_detail("claude:nie-ma", "x"))
        summary = board_mod.summary(b)
        self.assertNotIn("tickets", summary)
        self.assertEqual(summary["planPath"], b["plan"]["path"])

    def test_backlog_titles_and_missing_coordinator_directory(self):
        world = F.build_world(self.tmp)
        backlog = os.path.join(world["repo"], "docs", "agents", "backlog")
        os.makedirs(backlog)
        with open(os.path.join(backlog, "TOM-150.md"), "w", encoding="utf-8") as fh:
            fh.write("# TOM-150: Eksport raportu do CSV\n\n**Status:** `in-progress` — brak testu.\n\nTreść.\n")
        with open(os.path.join(backlog, "TOM-151.md"), "w", encoding="utf-8") as fh:
            fh.write("Bez nagłówka\n")
        scanner = CoordinationScanner(world["claude"], world["codex"], clock=lambda: F.NOW)
        coord = scanner.read()["codex:" + F.CODEX_ROOT]
        b = scanner.board(coord, LIVE_CODEX, F.NOW)
        t = tickets(b)["TOM-150"]
        self.assertEqual((t["title"], t["officialTitle"], t["officialFrom"]),
                         ("Eksport raportu do CSV", "Eksport raportu do CSV", "backlog"))
        self.assertEqual(t["officialNote"], "in-progress — brak testu.")
        self.assertEqual(tickets(b)["TOM-151"]["officialTitle"], "")
        # Katalog koordynatora zniknął (usunięty worktree) — repozytorium z katalogów podagentów.
        coord.cwd = os.path.join(self.tmp, "nie-ma", "wt")
        for item in coord.runs.values():
            item.cwd = world["repo"]
        b = scanner.board(coord, LIVE_CODEX, F.NOW)
        self.assertEqual(b["repoPath"], world["repo"])
        self.assertEqual(tickets(b)["TOM-150"]["officialTitle"], "Eksport raportu do CSV")

    def test_old_files_are_outside_the_window_and_roots(self):
        world = F.build_world(self.tmp)
        old = F.NOW - 9 * 86400
        os.utime(world["log"].path, (old, old))
        scanner = CoordinationScanner(world["claude"], world["codex"], clock=lambda: F.NOW, days=7)
        self.assertNotIn("claude:" + F.CLAUDE_COORD, scanner.read())
        wt = os.path.join(self.tmp, "wt")
        os.makedirs(wt)
        gitdir = os.path.join(world["repo"], ".git", "worktrees", "wt")
        os.makedirs(gitdir)
        with open(os.path.join(gitdir, "commondir"), "w", encoding="utf-8") as fh:
            fh.write("../..\n")
        with open(os.path.join(wt, ".git"), "w", encoding="utf-8") as fh:
            fh.write(f"gitdir: {gitdir}\n")
        self.assertEqual(repo_roots(os.path.join(wt, "src")), [wt, world["repo"]])


class ActionsTest(World):
    def test_annotate_cache_diff_and_worktree(self):
        world = F.build_world(self.tmp)
        boards = CoordinationScanner(world["claude"], world["codex"], clock=lambda: F.NOW).refresh([LIVE_CLAUDE])
        sessions = [{"id": "claude:" + F.CLAUDE_COORD}, {"id": "claude:" + F.CLAUDE_SINGLE}]
        actions.annotate(sessions, boards)
        self.assertEqual(sessions[0]["coordination"], {"label": "TOM-20", "done": 2, "total": 10,
                                                       "text": "TOM-20 · 2/10", "working": 5, "waiting": 1,
                                                       "running": 3})
        self.assertNotIn("coordination", sessions[1])

        path = os.path.join(self.tmp, "state", "tide", "coordination.json")
        payload = actions.cache_payload(boards, F.NOW)
        actions.write_cache(path, payload)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        cached = actions.read_cache(path)
        first = cached["coordinators"][0]
        self.assertEqual(first["label"], "TOM-20")
        self.assertNotIn("runs", first)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("MAJOR-1", text, "w pliku nie ma raportów podagentów")
        # Z poleceń podagentów tylko początek (do 300 znaków) w polu „brief” ticketu — nic więcej.
        briefless = json.loads(text)
        for coordinator in briefless["coordinators"]:
            for t in coordinator["tickets"]:
                self.assertLessEqual(len(t.pop("brief")), 300)
        self.assertNotIn("Zadanie:", json.dumps(briefless, ensure_ascii=False), "ani poleceń poza „brief”")

        # Opisy kart po ludzku trafiają do pliku (krótkie napisy), polecenia w całości — nie.
        describe.Describer(mode="rules").decorate(boards)
        payload = actions.cache_payload(boards, F.NOW)
        t140 = next(t for t in payload["coordinators"][0]["tickets"] if t["id"] == "TOM-140")
        self.assertEqual(t140["now"], "Trwają poprawki")
        self.assertEqual(t140["done"], "Gotowe: guard i 6 testów.")
        self.assertEqual(t140["title"], "Distribution guard dla kompaktowej dystrybucji")
        for coordinator in payload["coordinators"]:
            for t in coordinator["tickets"]:
                self.assertLessEqual(len(t["done"]), 300)
                self.assertLessEqual(len(t["now"]), 300)
                self.assertLessEqual(len(t.pop("brief")), 300)
        self.assertNotIn("Zadanie:", json.dumps(payload, ensure_ascii=False))

        b = boards[0]
        trees = [{"isMain": True, "repoPath": world["repo"], "path": world["repo"], "mainBranch": "master"},
                 {"isMain": False, "repoPath": world["repo"], "path": "/w/tom-140", "branch": "tom-140-guard"}]
        asked = []
        target = actions.diff_target(b, "TOM-140", trees, git=lambda repo, br: asked.append((repo, br)) or True)
        self.assertEqual(target, {"repo": world["repo"], "main": "master", "branch": "tom-140-guard"})
        self.assertEqual(actions.diff_argv(target), ["git", "-C", world["repo"], "diff", "--stat", "--patch",
                                                     "master...tom-140-guard"])
        self.assertIsNone(actions.diff_target(b, "TOM-140", trees, git=lambda repo, br: False), "gałęzi nie ma")
        self.assertIsNone(actions.diff_target(b, "TOM-146", trees, git=lambda repo, br: True), "ticket bez gałęzi")
        self.assertEqual(actions.worktree_of(b, "TOM-140", trees), "/w/tom-140")
        self.assertEqual(actions.worktree_of(b, "TOM-141", trees), "")


if __name__ == "__main__":
    unittest.main()
