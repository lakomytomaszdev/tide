"""Standard planu 1.1 (PT-6): klucz `card` i plany projektów z `ai-docs: tide` w repozytorium tablicy.

- `card: K-12` w planie (zły — błąd planu), ID karty z gałęzi, wybór planu koordynatora po karcie,
- boardplans: projekt tablicy z `ai-docs: tide` → `<tablica>/docs/<projekt>/plans`, `repo` — nic,
  $TIDE_BOARD_DIR, katalog bez znacznika tablicy,
- te plany widzą: plan_mod.find_plans (raport), skaner koordynatorów (tablica Sesje z `card`),
  RepoPlans (kontrola numerów), tools/next_id.py i tools/check_plans.py; plany w repozytorium
  (`ai-docs: repo`) działają jak dotąd.
Tablica jest tymczasowa (TIDE_BOARD_DIR), tak jak rejestr numerów.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from . import coordfix as F
from .gitrepos import git
from .test_coordination_numbering import REPO, kinds, numbered, plan_text
from tide_daemon.coordination import board as board_mod, boardplans, plan as plan_mod
from tide_daemon.coordination.model import Coordinator
from tide_daemon.coordination.repoplans import RepoPlans
from tide_daemon.coordination.scanner import CoordinationScanner, _plan_files
from tide_daemon.reports import plans as report_plans


def with_card(text: str, card: str) -> str:
    return text.replace("tide-plan: 1\n", f"tide-plan: 1\ncard: {card}\n", 1)


class CardKeyTest(unittest.TestCase):
    def test_card_key_is_parsed_and_validated(self):
        plan = plan_mod.parse(with_card(plan_text("A", numbered("PT", 1, 1)), "k-12"), "a.md")
        self.assertEqual((plan.card, plan.errors), ("K-12", []))
        self.assertEqual(plan.to_json()["card"], "K-12")
        bad = plan_mod.parse(with_card(plan_text("A", []), "12"), "a.md")
        self.assertEqual(bad.card, "")
        self.assertIn("niepoprawny klucz card „12” (oczekiwano K-<numer>)", bad.errors)
        self.assertEqual(plan_mod.parse(plan_text("A", []), "a.md").card, "", "bez klucza — plan 1.0 bez zmian")

    def test_card_of_branch(self):
        self.assertEqual([plan_mod.card_of_branch(b) for b in
                          ("claude/k-12-okno", "codex/K-7", "feat/k-3_x", "feat/pt-4-launch", "k-1", "kk-2-x", "")],
                         ["K-12", "K-7", "K-3", "", "K-1", "", ""])

    def test_pick_prefers_plan_of_the_card_from_branch(self):
        def p(name, card="", status="active", updated="2026-10-27T10:00:00+01:00", session=""):
            text = plan_text(name, [], status=status, updated=updated)
            if card:
                text = with_card(text, card)
            if session:
                text = text.replace("coordinator: claude\n", f"coordinator: claude\nsession: {session}\n")
            return plan_mod.parse(text, name + ".md")
        k12 = p("k12", "K-12", updated="2026-10-27T09:00:00+01:00")
        k13 = p("k13", "K-13", updated="2026-10-27T11:00:00+01:00")
        loose = p("loose", updated="2026-10-27T08:00:00+01:00")
        plans = [k12, k13, loose]
        self.assertIs(plan_mod.pick(plans, "s", "claude", "claude/k-12-okno"), k12)
        self.assertIs(plan_mod.pick(plans, "s", "claude", "claude/k-14-inna"), loose, "plany innych kart odpadają")
        self.assertIs(plan_mod.pick(plans, "s", "claude", "master"), k13, "bez karty w gałęzi — jak dotąd")
        done = p("k12-done", "K-12", status="done", updated="2026-10-27T12:00:00+01:00")
        self.assertIs(plan_mod.pick([done, loose], "s", "claude", "claude/k-12-okno"), done)
        other = p("k12-other", "K-12", session="inna-sesja")
        self.assertIs(plan_mod.pick([other, loose], "s", "claude", "claude/k-12-okno"), loose,
                      "plan karty wskazujący inną sesję nie jest tego koordynatora")


class BoardPlansCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tide-boardplans-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.ws = os.path.join(self.tmp, "ws")
        self.board = os.path.join(self.ws, "tide-board")
        os.makedirs(os.path.join(self.board, "projects"))
        with open(os.path.join(self.board, "board.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntide-board: 1\nnext-card: 13\n---\n")
        self.repo = self.make_repo("logtool")          # ai-docs: tide
        self.local = self.make_repo("plasma-tide")      # ai-docs: repo
        self.project("logtool", self.repo, "tide")
        self.project("plasma-tide", self.local, "repo")
        self.plans = os.path.join(self.board, "docs", "logtool", "plans")
        os.makedirs(self.plans)
        self.write(os.path.join(self.plans, "2026-10-27-vin.md"),
                   with_card(plan_text("Historia VIN", numbered("LR", 1, 3), prefix="LR"), "K-12"))
        patcher = mock.patch.dict(os.environ, {"TIDE_BOARD_DIR": self.board,
                                               "XDG_STATE_HOME": os.path.join(self.tmp, "state")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_repo(self, name):
        path = os.path.join(self.ws, name)
        os.makedirs(os.path.join(path, plan_mod.PLANS_DIR))
        git(path, "init", "-q", "-b", "master")
        git(path, "commit", "-q", "--allow-empty", "-m", "init")
        return path

    def project(self, slug, repo, ai_docs):
        self.write(os.path.join(self.board, "projects", f"{slug}.md"),
                   f"---\nname: {slug}\nrepo: {repo}\nai-docs: {ai_docs}\n---\n")

    @staticmethod
    def write(path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)


class BoardPlansTest(BoardPlansCase):
    def test_plan_dirs_only_for_tide_docs_projects(self):
        self.assertEqual(boardplans.plan_dirs([self.repo]), [self.plans])
        self.assertEqual(boardplans.plan_dirs([os.path.join(self.ws, "logtool-wt"), self.repo]), [self.plans],
                         "worktree koordynatora i główny katalog")
        self.assertEqual(boardplans.plan_dirs([self.local]), [], "ai-docs: repo — plany w repozytorium")
        self.assertEqual(boardplans.plan_dirs([os.path.join(self.ws, "inne")]), [])
        self.assertEqual(boardplans.plan_dirs([self.repo], os.path.join(self.tmp, "nie-tablica")), [])
        self.project("plasma-tide", self.local, "tide")  # zmiana pliku projektu — odczyt od nowa
        self.assertEqual(boardplans.plan_dirs([self.local]), [os.path.join(self.board, "docs", "plasma-tide", "plans")])
        with mock.patch.dict(os.environ, {"TIDE_BOARD_DIR": os.path.join(self.tmp, "brak")}):
            self.assertEqual(boardplans.plan_dirs([self.repo]), [])

    def test_find_plans_and_scanner_files_read_both_places(self):
        self.write(os.path.join(self.repo, plan_mod.PLANS_DIR, "2026-10-20-stary.md"),
                   plan_text("Stary plan w repozytorium", numbered("LR", 4, 4), prefix="LR"))
        found = plan_mod.find_plans([self.repo])
        self.assertEqual(sorted((p.title, p.card) for p in found),
                         [("Historia VIN", "K-12"), ("Stary plan w repozytorium", "")])
        self.assertEqual(_plan_files([self.repo])[-1], os.path.join(self.plans, "2026-10-27-vin.md"))
        local = plan_mod.find_plans([self.local])
        self.assertEqual(local, [], "plasma-tide (repo): tylko docs/agents/plans, tu puste")

    def test_coordinator_board_and_summary_carry_card(self):
        plan = plan_mod.pick(plan_mod.find_plans([self.repo]), "x", "claude", "claude/k-12-historia-vin")
        coord = Coordinator(id="claude:x", tool="claude", path="/x.jsonl", session_id="x", cwd=self.repo,
                            branch="claude/k-12-historia-vin")
        board = board_mod.build(coord, plan=plan, now=F.NOW, repo_path=self.repo)
        self.assertEqual((board["card"], board["plan"]["card"]), ("K-12", "K-12"))
        self.assertEqual(board_mod.summary(board)["card"], "K-12")
        self.assertEqual(board_mod.summary(board_mod.build(coord, now=F.NOW))["card"], "")

    def test_numbering_counts_board_plans(self):
        self.write(os.path.join(self.repo, plan_mod.PLANS_DIR, "2026-10-26-inny.md"),
                   plan_text("Inny", [("LR-2", "Coś innego")], prefix="LR"))
        plans, _declared = RepoPlans(ttl=0).read([self.repo])
        self.assertEqual(sorted(p.title for p in plans), ["Historia VIN", "Inny"])
        scanner = CoordinationScanner(self.tmp, self.tmp, clock=lambda: F.NOW,
                                      registry=os.path.join(self.tmp, "state", "ids.json"))
        self.assertIn(("ticket-title", "hard", "LR-2"), kinds(scanner.warnings([self.repo])))
        self.assertEqual(report_plans.conflicts(self.repo)[0]["ticket"], "LR-2")

    def test_report_lists_board_plan_with_card(self):
        import types
        project = types.SimpleNamespace(name="logtool", path=self.repo, is_repo=True)
        report_plans.attach([project], F.NOW - 86400, F.NOW)
        self.assertEqual([(e["title"], e["card"], e["total"]) for e in project.plans], [("Historia VIN", "K-12", 3)])

    def test_next_id_and_check_plans_tools(self):
        env = dict(os.environ)
        out = subprocess.run(["/usr/bin/python3", os.path.join(REPO, "tools", "next_id.py"), self.repo,
                              "--prefix", "LR", "--dry-run"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual((out.returncode, out.stdout), (0, "LR-4\n"), out.stderr)
        self.write(os.path.join(self.repo, plan_mod.PLANS_DIR, "2026-10-26-inny.md"),
                   plan_text("Inny", [("LR-3", "Coś innego")], prefix="LR"))
        check = subprocess.run(["/usr/bin/python3", os.path.join(REPO, "tools", "check_plans.py"), self.ws,
                                "--no-youtrack", "--json"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        data = {os.path.basename(item["repo"]): item for item in json.loads(check.stdout)}
        self.assertIn(os.path.join(self.plans, "2026-10-27-vin.md"), data["logtool"]["plans"])
        self.assertEqual([w["ticket"] for w in data["logtool"]["warnings"] if w["severity"] == "hard"], ["LR-3"])
        self.assertNotIn("tide-board", data, "tablica sama nie jest repozytorium z planami")


if __name__ == "__main__":
    unittest.main()
