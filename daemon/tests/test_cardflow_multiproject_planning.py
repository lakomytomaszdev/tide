"""K-33 #4 (PT-115): Planowanie w kilku projektach.

Zlecenie Planowania Lekkiego z dwoma projektami (tymczasowe repozytoria, atrapa programu): krok widzi oba
repozytoria (główne z zapisem, drugie tylko do odczytu) i listę projektów, propozycja z kolumną Projekt i blokerem
#3 → #1 między projektami czeka na zatwierdzenie, a „Zatwierdź plan” odmawia (grupa kart to #5) bez zmiany
dziennika. Zlecenie z projektem bez repozytorium kończy się `no-repo` z nazwą projektu, bez startu kroku.
Te same przypadki biegną przez prywatną magistralę D-Bus (StartCard, ApproveCardPlan) i w procesie testu.
"""
import json
import os
import shutil

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import CLAUDE_MD, FlowCase, git


class MultiProjectPlanning:
    # --- zależne od sposobu wywołania usługi ------------------------------------------------------
    def upsert(self, data: dict) -> dict:
        raise NotImplementedError

    def approve(self, card: str) -> dict:
        raise NotImplementedError

    # --- przypadki ----------------------------------------------------------------------------------
    def extra_repo(self, name="extra", prefix="EX"):
        env = getattr(self, "git_env", None)
        repo = os.path.join(self.base, "ws", name)
        os.makedirs(repo)
        git(repo, "init", "-q", env=env)
        with open(os.path.join(repo, "CLAUDE.md"), "w", encoding="utf-8") as fh:
            fh.write(CLAUDE_MD.replace("`PT`", f"`{prefix}`"))
        git(repo, "add", "-A", env=env)
        git(repo, "commit", "-q", "-m", "init", env=env)
        reply = self.upsert({"slug": name, "name": name.capitalize(), "repo": repo})
        self.assertTrue(reply["ok"], reply)
        return repo

    def test_light_planning_with_two_projects_proposes_one_plan_with_project_column(self):
        extra = self.extra_repo()
        card = self.card(description="Wspólna zmiana API i klienta.\n\nATRAPA: plan-multi")
        reply = self.start(card, entry="planning", mode="light", executor="Sonnet", projects=["extra"])
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(self.shown(card)["projects"], ["extra"], "projekty zlecenia zapisane w karcie")
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=30), self.events(card))
        shown = self.wait_column(card, "waiting")
        plan = shown["derived"]["flow"]["plan"]
        self.assertEqual(plan["status"], "pending", plan)

        started = self.of(card, "card.started")[0]
        self.assertEqual([(p["slug"], p["prefix"], p["tracker"], p["main"]) for p in started["projects"]],
                         [("proj", "PT", "local", True), ("extra", "EX", "local", False)])
        step = self.of(card, "step.started")[0]
        with open(os.path.join(step["stepDir"], "out", "fake-call.json"), encoding="utf-8") as fh:
            call = json.load(fh)
        argv, prompt = call["argv"], call["prompt"]
        self.assertTrue(call["cwd"].startswith(self.repo + "-worktrees"), "główne repozytorium: worktree karty")
        self.assertEqual(argv[argv.index("--add-dir") + 1], extra)
        self.assertIn(f"Edit(/{os.path.realpath(extra)}/**)", argv[argv.index("--disallowedTools"):])
        with open(os.path.join(step["stepDir"], "in", "settings.json"), encoding="utf-8") as fh:
            filesystem = json.load(fh)["sandbox"]["filesystem"]
        self.assertIn(extra, filesystem["allowRead"])
        self.assertEqual(filesystem["allowWrite"], [os.path.join(step["stepDir"], "out")])
        self.assertIn("Projekty zlecenia", prompt)
        self.assertIn(f"- proj — proj: {self.repo} (projekt karty", prompt)
        self.assertIn(f"- extra — Extra: {extra} (repozytorium tylko do odczytu", prompt)
        self.assertIn("prefiks EX, tracker local, numery: lokalny EX", prompt)
        self.assertIn("| Blokery | Projekt |", prompt)

        self.assertEqual([(r["workId"], r["project"], r["blockers"]) for r in plan["rows"]],
                         [("#1", "extra", []), ("#2", "proj", []), ("#3", "proj", ["#1"])])
        self.assertEqual(plan["waves"], [["#1", "#2"], ["#3"]])
        self.assertEqual([(p["slug"], p["source"]) for p in plan["projects"]],
                         [("proj", "lokalny PT"), ("extra", "lokalny EX")])

        before = self.events(card)
        refused = self.approve(card)
        self.assertFalse(refused["ok"], refused)
        self.assertEqual(refused["error"], "group-approval")
        self.assertIn("zatwierdzanie grupy kart — w następnej wersji", refused["message"])
        wait_until(lambda: False, timeout=0.5)
        self.assertEqual(self.events(card), before, "odmowa nie zmienia dziennika")
        self.assertEqual(self.results(card, "reserve"), [])

    def test_project_without_repository_ends_with_no_repo_before_any_step(self):
        self.assertTrue(self.upsert({"slug": "bez", "name": "Bez repozytorium"})["ok"])
        card = self.card(description="ATRAPA: plan-multi")
        reply = self.start(card, entry="planning", mode="light", executor="Sonnet", projects=["bez"])
        self.assertFalse(reply["ok"], reply)
        self.assertEqual(reply["error"], "no-repo")
        self.assertIn("Bez repozytorium", reply["message"])
        self.assertEqual(self.events(card), [])

        gone = self.extra_repo("znika", "ZN")
        shutil.rmtree(gone)
        other = self.card(description="ATRAPA: plan-multi")
        reply = self.start(other, entry="planning", mode="light", executor="Sonnet", projects=["znika"])
        self.assertFalse(reply["ok"], reply)
        self.assertEqual(reply["error"], "no-repo")
        self.assertIn("Znika", reply["message"])
        self.assertEqual(self.events(other), [])

    def test_now_and_plan_entries_stay_single_project(self):
        self.extra_repo()
        card = self.card()
        for entry in ("now", "import"):
            reply = self.start(card, entry=entry, projects=["extra"])
            self.assertFalse(reply["ok"], reply)
            self.assertEqual(reply["error"], "single-project")
        self.assertEqual(self.events(card), [])

    def test_single_project_plan_still_approves(self):
        card = self.card(description="ATRAPA: planowanie")
        self.assertTrue(self.start(card, entry="planning", mode="light", executor="Sonnet")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card, "plan.reviewed"), timeout=30), self.events(card))
        self.assertEqual(self.of(card, "plan.proposed")[0].get("projects"), None)
        approved = self.approve(card)
        self.assertTrue(approved["ok"], approved)
        self.assertEqual(approved["map"], {"#1": "PT-1"})


class MultiProjectPlanningInProcessTest(MultiProjectPlanning, FlowInProcess):
    def upsert(self, data):
        return json.loads(self.board.upsert_project(json.dumps(data)))

    def approve(self, card):
        box = []
        self.flow.approve_plan(card, box.append)
        self.assertTrue(wait_until(lambda: box, timeout=10))
        return json.loads(box[0])


class MultiProjectPlanningDBusTest(MultiProjectPlanning, FlowCase):
    def upsert(self, data):
        return self.call("UpsertProject", json.dumps(data))

    def approve(self, card):
        return self.call("ApproveCardPlan", card)
