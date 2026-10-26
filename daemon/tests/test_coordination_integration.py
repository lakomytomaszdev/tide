"""Koordynacja w innych miejscach: KRunner („sesje”), raport poranny („Plany i tickety”) i launcher okna.

Bez D-Bus: rdzeń KRunnera z atrapą launcherów, dodatek raportu na projektach z collect.py
(plik tablicy z koordynacji i plany z repozytoriów), Launchers.show_sessions z zapisem polecenia.
"""
import os
import shutil
import tempfile
import unittest

from . import coordfix as F
from tide_daemon.coordination import actions
from tide_daemon.coordination.scanner import CoordinationScanner
from tide_daemon.krunner.runner import SESSIONS_MATCH, KRunnerCore
from tide_daemon.reports import collect, plans, render
from tide_daemon.worktrees.launchers import Launchers

COORDINATORS = [
    {"id": "claude:a", "project": "LogViewer", "title": "Release candidate TOM-20", "label": "TOM-20",
     "branch": "codex/tom-20-rc", "live": True, "lastActivityTs": F.NOW - 60, "progress": {"text": "TOM-20 · 8/13"},
     "counts": {"czeka": 1, "w-toku": 2, "review": 1}},
    {"id": "codex:b", "project": "legal-agents", "title": "Renewal LA-030…LA-040", "label": "", "branch": "main",
     "live": False, "lastActivityTs": F.NOW - 7200, "progress": {"text": "9/12"}, "counts": {}},
]


class FakeLaunchers:
    sessions_window = "/x/tide-sessions.py"

    def __init__(self):
        self.shown = []

    def available(self):
        return {"terminal": True, "rider": False}

    def show_sessions(self, coordinator=""):
        self.shown.append(coordinator)
        return True


class EmptyIndex:
    built = True

    def entries(self):
        return []


class KRunnerTest(unittest.TestCase):
    def setUp(self):
        self.launchers = FakeLaunchers()
        self.core = KRunnerCore(EmptyIndex(), lambda: [], self.launchers, clock=lambda: F.NOW,
                                coordination=lambda: COORDINATORS)

    def test_keyword_lists_window_and_coordinators(self):
        matches = self.core.match("sesje")
        self.assertEqual([m["id"] for m in matches], [SESSIONS_MATCH, "coordination:claude:a", "coordination:codex:b"])
        self.assertEqual(matches[0]["text"], "Tide Sesje")
        self.assertEqual(matches[0]["properties"]["subtext"], "Koordynatorzy, tickety i podagenci · 2 z ostatnich dni, 1 działa")
        self.assertEqual(matches[1]["text"], "Sesje · LogViewer · Release candidate TOM-20")
        self.assertEqual(matches[1]["properties"]["subtext"], "TOM-20 · 8/13 · 1 czeka · 3 w toku · 1 min temu")
        self.assertEqual(matches[0]["properties"]["category"], "Tide Sesje")

    def test_words_filter_coordinators_and_prefix_works(self):
        self.assertEqual([m["id"] for m in self.core.match("sesje legal")], ["coordination:codex:b"])
        self.assertEqual([m["id"] for m in self.core.match("sesje tom-20")], ["coordination:claude:a"])
        self.assertEqual(self.core.match("ses")[0]["id"], SESSIONS_MATCH)
        self.assertEqual([m for m in self.core.match("sesjexyz") if m["id"].startswith("coord")], [])

    def test_run_opens_window_only_for_known_coordinators(self):
        self.assertTrue(self.core.run(SESSIONS_MATCH))
        self.assertTrue(self.core.run("coordination:claude:a"))
        self.assertFalse(self.core.run("coordination:claude:obcy"))
        self.assertEqual(self.launchers.shown, ["", "claude:a"])

    def test_without_window_or_area_nothing_is_offered(self):
        self.launchers.sessions_window = ""
        self.assertEqual(self.core.match("sesje"), [])
        core = KRunnerCore(EmptyIndex(), lambda: [], FakeLaunchers(), clock=lambda: F.NOW)
        self.assertEqual(core.match("sesje"), [])


class ReportPlansTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-coord-report-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.world = F.build_world(self.tmp)

    def projects(self):
        repo = collect.Project(key=self.world["repo"], name="demo", path=self.world["repo"])
        other = os.path.join(self.tmp, "workspace", "other")
        plans_dir = os.path.join(other, "docs", "agents", "plans")
        os.makedirs(plans_dir)
        with open(os.path.join(plans_dir, "2026-10-25-cleanup.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Sprzątanie\ntracker: local\nticket-prefix: TIDE\nstatus: active\n"
                     "updated: 2026-10-25T10:00:00Z\n---\n| Ticket | Tytuł | Stan | Gałąź | Agent | Uwagi |\n"
                     "| --- | --- | --- | --- | --- | --- |\n| TIDE-1 | Pierwszy | gotowe | | | |\n"
                     "| TIDE-2 | Drugi <b>x</b> | czeka | | | pytanie |\n")
        with open(os.path.join(plans_dir, "2026-09-01-old.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntide-plan: 1\ntitle: Stary\nstatus: done\nupdated: 2026-09-01T10:00:00Z\n---\n")
        quiet = collect.Project(key=other, name="other", path=other)
        return repo, quiet

    def test_board_cache_and_plan_files(self):
        boards = CoordinationScanner(self.world["claude"], self.world["codex"], clock=lambda: F.NOW).refresh(
            [{"id": "claude:" + F.CLAUDE_COORD, "state": "working"}])
        cache = os.path.join(self.tmp, "state", "coordination.json")
        actions.write_cache(cache, actions.cache_payload(boards, F.NOW))
        repo, other = self.projects()
        plans.attach([repo, other], F.NOW - 86400, F.NOW, cache)
        titles = [p["title"] for p in repo.plans]
        self.assertIn("Release candidate TOM-20", titles)
        self.assertEqual(titles.count("Release candidate TOM-20"), 1, "plan koordynatora nie jest pokazany drugi raz")
        board = next(p for p in repo.plans if p["source"] == "board")
        self.assertEqual((board["done"], board["total"], board["label"]), (2, 10, "TOM-20"))
        self.assertEqual(board["tickets"][0]["state"], "czeka", "najpierw to, co czeka")
        self.assertEqual([p["title"] for p in other.plans], ["Sprzątanie"], "plan zakończony przed okresem pominięty")
        html = render._plans(other)
        self.assertIn("Plany i tickety", html)
        self.assertIn("Drugi &lt;b&gt;x&lt;/b&gt;", html, "tytuły z planu są escapowane")
        self.assertIn("1 z 2 gotowe", html)
        card = render._project_card(repo)
        self.assertIn("TOM-20 · 2/10", card)

    def test_without_cache_and_on_errors_the_report_goes_on(self):
        repo, other = self.projects()
        plans.attach([repo, other], F.NOW - 86400, F.NOW, None)
        self.assertEqual([p["source"] for p in repo.plans], ["plan"], "bez tablicy — sam plan z repozytorium")
        broken = collect.Project(key="x", name="x", path=None)
        plans.attach([broken], 0, F.NOW, os.path.join(self.tmp, "nie-ma.json"))
        self.assertEqual(broken.plans, [])
        self.assertEqual(render._plans(collect.Project(key="y", name="y", path="/y")), "")


class LauncherTest(unittest.TestCase):
    def test_show_sessions_runs_installed_window_with_selection(self):
        tmp = tempfile.mkdtemp(prefix="tide-coord-launch-")
        self.addCleanup(shutil.rmtree, tmp)
        script = os.path.join(tmp, "tide-sessions.py")
        env = {"HOME": tmp, "TIDE_SESSIONS_WINDOW": script, "PATH": ""}
        launchers = Launchers(env=env, path="")
        spawned = []
        launchers.spawn = lambda argv, path: spawned.append(argv) or True
        self.assertFalse(launchers.show_sessions("claude:a"), "okno nie jest zainstalowane")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("#\n")
        self.assertTrue(launchers.show_sessions("claude:a"))
        self.assertTrue(launchers.show_sessions())
        self.assertEqual(spawned, [["/usr/bin/python3", "-s", script, "--select", "claude:a"],
                                   ["/usr/bin/python3", "-s", script]])


if __name__ == "__main__":
    unittest.main()
