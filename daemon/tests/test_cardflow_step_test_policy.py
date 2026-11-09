"""PT-287 (K-69 #10): silnik liczy jedną politykę testów na każdy start i wznowienie kroku.

Przepływ przez publiczne wejście silnika (StartCard, ResolveCardStep) z atrapą procesu kroku (tests/flowfakes.py),
w procesie testu (fixture `FlowInProcess`). Przypadki decyzji `effective_tests` i treści kartek zostają w testach
jednostkowych #2 (test_cardflow_effective_tests.py); tu tylko kilka scenariuszy integracji z silnikiem.
"""
import dataclasses
import json
import os
from unittest import mock

from tide_daemon.board import commands, contract_approval
from tide_daemon.cardflow import handoff
from tide_daemon.tasks import agentcmd

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git
from .test_cardflow_effective_tests import BASE, CHANGED, DETECTED_LINE, MASTER_FRAGMENTS
# Moduły, nie klasy: zaimportowana klasa testów uruchomiłaby tu drugi raz swoje testy.
from . import test_cardflow_plan_integration as plan_flow, test_cardflow_waves as wave_flow

FIRST = {"changed": "make test-changed BASE={base} JOBS=1", "full": "make test"}
SECOND = {"changed": "make test-changed BASE={base} JOBS=2", "full": "make test"}
SECTION = "Obowiązujące testy ("


class PolicyFlowCase(FlowInProcess):
    """Repozytorium jak plasma-tide i podsłuch `approval_for_step` oraz startu piaskownicy (bez scenariuszy)."""

    def setUp(self):
        super().setUp()
        # Repozytorium jak plasma-tide: `test` w Makefile, `test-changed` w mk/tests.mk, uruchamiacz z `--modules`.
        for rel, text in CHANGED.items():
            path = os.path.join(self.repo, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "make")
        engine = self.flow.engine
        self.approvals = []
        real = engine.store.approval_for_step

        def counted(project, step):
            result = real(project, step)
            self.approvals.append((project.slug, step, result))
            return result

        patcher = mock.patch.object(engine.store, "approval_for_step", side_effect=counted)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Kroki, z których naprawdę startuje piaskownica: krok próbny polityki ma mieć te same obszary zapisu.
        self.launched = []
        real_background = agentcmd.background

        def spied(step):
            self.launched.append(step)
            return real_background(step)

        patcher = mock.patch.object(agentcmd, "background", side_effect=spied)
        patcher.start()
        self.addCleanup(patcher.stop)

    # --- pomocnicze -------------------------------------------------------------------------------

    def approve(self, tests):
        reply = self.store.approve_project_tests({"slug": "proj", "tests": tests})
        self.assertTrue(reply.get("testsApproved"), reply)

    def decide(self, card_id, request):
        box = []
        self.board.resolve_card_step(card_id, json.dumps(request), box.append)
        self.assertTrue(wait_until(lambda: box, timeout=20), "ResolveCardStep bez odpowiedzi")
        return json.loads(box[0])

    def prompt_of(self, step) -> str:
        with open(os.path.join(step["stepDir"], "in", "prompt.txt"), encoding="utf-8") as fh:
            return fh.read()

    def asked(self, mode="pytanie", **start):
        """Karta z pytaniem kroku ticketu: (id karty, zdarzenie startu, odpowiedź StartCard)."""
        card_id = self.card(description=f"Dodaj eksport.\n\nATRAPA: {mode}")
        reply = self.start(card_id, **start)
        self.assertTrue(reply["ok"], reply)
        self.wait_column(card_id, "waiting")
        return card_id, self.of(card_id, "step.started")[0], reply

    def resume(self, card_id, step) -> dict:
        card = self.shown(card_id)
        [decision] = [d for d in card["derived"]["flow"]["decisions"] if d["step"] == step["step"]]
        reply = decision["reply"]
        self.assertTrue(self.decide(card_id, {**reply["params"], reply["field"]: "ISO 8601"})["ok"])
        found = wait_until(lambda: [s for s in self.of(card_id, "step.started")
                                    if s.get("parentStep") == step["step"]], timeout=20)
        self.assertTrue(found, self.events(card_id))
        return found[0]

    def approval_for(self, step):
        """Jedyne wywołanie `approval_for_step` dla kroku: (krok z obszarami zapisu, wynik). Sprawdza, że to obszary
        zapisu dokładnie tego kroku (worktree i `out` z jego zdarzenia) i tego samego `agentcmd.Step`, z którego
        startuje piaskownica (`agentcmd.background`) — poza samą kartką."""
        calls = [(slug, probe, result) for slug, probe, result in self.approvals if probe.step_dir == step["stepDir"]]
        self.assertEqual(len(calls), 1, [p.step_dir for _, p, _ in self.approvals])
        slug, probe, result = calls[0]
        self.assertEqual(slug, "proj")
        areas = agentcmd.step_write_areas(probe).writable()
        self.assertIn(step["worktree"], areas)
        self.assertIn(os.path.join(step["stepDir"], "out"), areas)
        [real] = [s for s in self.launched if s.step_dir == step["stepDir"]]
        # Krok próbny powstaje przed polityką, więc nie ma jeszcze zgód kontraktu (PT-280) — poza nimi ten sam krok.
        self.assertEqual(dataclasses.replace(probe, prompt="", allowed_tools=()),
                         dataclasses.replace(real, prompt="", allowed_tools=()))
        self.assertEqual(areas, agentcmd.step_write_areas(real).writable())
        return probe, result


class StepTestPolicyFlowTest(PolicyFlowCase):
    # --- scenariusze ------------------------------------------------------------------------------

    def test_contract_change_reaches_the_resume_and_base_stays_pinned(self):
        self.approve(FIRST)
        card_id, first, reply = self.asked()
        base = [r for r in self.results(card_id, "ticket-branch")][0]["base"]
        policy = first["testPolicy"]
        self.assertEqual((policy["source"], policy["base"], policy["changed"]),
                         ("profile", base, f"make test-changed BASE={base} JOBS=1"))
        self.assertEqual(first["testPolicyCard"], policy)
        self.assertIn(f"`make test-changed BASE={base} JOBS=1`", self.prompt_of(first))
        self.assertNotIn(SECTION, self.prompt_of(first))
        self.assertEqual(self.approval_for(first)[1], (True, ""))

        # Gałąź karty idzie dalej, kontrakt się zmienia; wznowienie liczy politykę od nowa z przypiętą bazą.
        card_tree = reply["cardWorktree"]
        with open(os.path.join(card_tree, "INNY.md"), "w", encoding="utf-8") as fh:
            fh.write("nowy commit karty\n")
        git(card_tree, "add", "-A")
        git(card_tree, "commit", "-q", "-m", "karta idzie dalej")
        tip = git(card_tree, "rev-parse", "HEAD").strip()
        self.assertNotEqual(tip, base)
        self.approve(SECOND)

        resumed = self.resume(card_id, first)
        policy = resumed["testPolicy"]
        self.assertEqual((policy["source"], policy["base"], policy["changed"]),
                         ("profile", base, f"make test-changed BASE={base} JOBS=2"))
        self.assertEqual(resumed["testPolicyCard"], first["testPolicy"])
        text = self.prompt_of(resumed)
        section = text[text.index(handoff.CURRENT_TESTS_OPEN):text.index(handoff.CURRENT_TESTS_CLOSE)]
        self.assertIn(SECTION, section)
        self.assertIn(f"`make test-changed BASE={base} JOBS=2`", section)
        self.assertNotIn(tip, text)
        probe, result = self.approval_for(resumed)
        self.assertEqual(result, (True, ""))

        # Brak zapisanej bazy: `{base}` zostaje nierozwinięte (ten sam punkt silnika, ten sam krok).
        bare = self.flow.engine.step_test_policy(card_id, probe, base="")
        self.assertEqual((bare.changed, bare.base_pending), ("make test-changed BASE={base} JOBS=2", True))
        self.assertIn("`{base}` to przypięty punkt startu", handoff.current_tests(bare, kind="ticket"))

    def test_revoked_approval_gives_policy_without_profile_on_resume(self):
        # Codex (review PT-287 M1): `/tmp` i `$TMPDIR` są w jego obszarach zapisu tylko bez katalogów odczytu, więc
        # krok próbny musi mieć te same `read_dirs` co krok, który startuje (sprawdza `approval_for`).
        self.approve(FIRST)
        card_id, first, _reply = self.asked(executor="Codex mocny", preferredProvider="openai")
        self.assertEqual(first["program"], "codex")
        self.assertEqual(first["testPolicy"]["source"], "profile")
        self.assertEqual(self.approval_for(first)[1], (True, ""))
        contract_approval.record(self.root, "proj", {})       # cofnięcie zatwierdzenia: odcisk usunięty

        resumed = self.resume(card_id, first)
        policy = resumed["testPolicy"]
        self.assertEqual(self.approval_for(resumed)[1][0], False)
        self.assertNotEqual(policy["source"], "profile")
        self.assertEqual((policy["changedSource"], policy["fullSource"], policy["unapproved"]),
                         ("detected", "detected", True))
        text = self.prompt_of(resumed)
        section = text[text.index(handoff.CURRENT_TESTS_OPEN):text.index(handoff.CURRENT_TESTS_CLOSE)]
        self.assertIn(handoff.UNAPPROVED_LINE, section)
        self.assertNotIn("JOBS=1", section)

    def test_plasma_tide_without_contract_keeps_reference_text_on_start_and_resume(self):
        card_id, first, _reply = self.asked()
        base = first["testPolicy"]["base"]
        self.assertTrue(base)
        expected = DETECTED_LINE + MASTER_FRAGMENTS["fragments"]["changed"]["ticket-tests"].replace(BASE, base)
        self.assertEqual(self.prompt_of(first).count(expected), 1)
        self.assertEqual(first["testPolicy"]["source"], "detected")

        resumed = self.resume(card_id, first)
        self.assertEqual(resumed["testPolicy"], first["testPolicy"])
        text = self.prompt_of(resumed)
        self.assertNotIn(handoff.CURRENT_TESTS_OPEN, text)
        self.assertEqual(text.count(expected), 1)
        for step in (first, resumed):
            self.assertEqual(self.approval_for(step)[1], (False, "projekt nie ma kontraktu testów"))


class ConflictAndPlanningPolicyFlowTest(PolicyFlowCase):
    """PT-284 (K-69 #7): krok konfliktu liczy politykę z bazą scalanego ticketu, planowanie dostaje linię kontraktu."""
    proposal = plan_flow.PlanFlowTest.proposal
    submit = plan_flow.PlanFlowTest.submit
    approve_plan = plan_flow.PlanFlowTest.approve
    wave_plan = wave_flow.WaveFlowTest.wave_plan

    def conflicting_plan(self, card_id):
        path = self.wave_plan(card_id)
        for number, mode in ((1, "gotowe"), (2, "powoli 2")):
            with open(os.path.join(os.path.dirname(path), "tickets", f"{number}.md"), "w", encoding="utf-8") as fh:
                fh.write(f"# Ticket {number}\n\nATRAPA: {mode}\nATRAPA-PLIK: same\n\n- [ ] Wynik.\n")
        self.assertTrue(self.submit(card_id, path)["ok"])
        approved = self.approve_plan(card_id)
        self.assertTrue(approved["ok"], approved)
        return approved

    def test_conflict_policy_uses_pinned_ticket_base_and_is_recomputed_on_resume(self):
        self.approve(FIRST)
        card_id = self.card(description="Plan z konfliktem\n\nATRAPA: conflict-error")
        self.conflicting_plan(card_id)
        self.assertTrue(wait_until(lambda: any(s.get("kind") == "conflict" for s in self.of(card_id, "step.started")),
                                   timeout=25), self.events(card_id))
        conflict = next(s for s in self.of(card_id, "step.started") if s.get("kind") == "conflict")
        ticket = conflict["ticket"]
        base = next(r for r in self.results(card_id, "ticket-branch") if r["ticket"] == ticket)["base"]
        # Gałąź karty poszła dalej (scalony pierwszy ticket): baza konfliktu to nadal punkt startu ticketu.
        card_tree = conflict["worktree"]
        self.assertNotEqual(git(card_tree, "rev-parse", "HEAD").strip(), base)
        policy = conflict["testPolicy"]
        self.assertEqual((policy["source"], policy["base"], policy["changed"]),
                         ("profile", base, f"make test-changed BASE={base} JOBS=1"))
        self.assertEqual(conflict["testPolicyCard"], policy)
        text = self.prompt_of(conflict)
        self.assertIn(f"uruchom w worktree karty `make test-changed BASE={base} JOBS=1`", text)
        self.assertNotIn("uruchom potrzebne testy", text)
        self.assertEqual(self.approval_for(conflict)[1], (True, ""))

        self.assertTrue(wait_until(lambda: any(e.get("step") == conflict["step"]
                                               for e in self.of(card_id, "step.finished")), timeout=15))
        self.approve(SECOND)
        self.assertTrue(self.decide(card_id, {"choice": "retry", "step": conflict["step"]})["ok"])
        found = wait_until(lambda: [s for s in self.of(card_id, "step.started")
                                    if s.get("parentStep") == conflict["step"]], timeout=20)
        self.assertTrue(found, self.events(card_id))
        resumed = found[0]
        self.assertEqual(resumed["kind"], "conflict")
        policy = resumed["testPolicy"]
        self.assertEqual((policy["base"], policy["changed"]), (base, f"make test-changed BASE={base} JOBS=2"))
        self.assertEqual(resumed["testPolicyCard"], conflict["testPolicy"])
        text = self.prompt_of(resumed)
        section = text[text.index(handoff.CURRENT_TESTS_OPEN):text.index(handoff.CURRENT_TESTS_CLOSE)]
        self.assertIn("Testy po scaleniu:", section)
        self.assertIn(f"`make test-changed BASE={base} JOBS=2`", section)
        self.assertEqual(self.approval_for(resumed)[1], (True, ""))

    def test_light_planning_and_plan_review_get_one_contract_line(self):
        self.approve(FIRST)
        card_id = self.card(description="Dodaj eksport. ATRAPA: planowanie")
        self.assertTrue(self.start(card_id, entry="planning", mode="light")["ok"])
        self.assertTrue(wait_until(lambda: self.of(card_id, "plan.reviewed"), timeout=20), self.events(card_id))
        steps = {s["kind"]: s for s in self.of(card_id, "step.started")}
        self.assertEqual(set(steps), {"planning", "plan-review"})
        for kind, step in steps.items():
            text = self.prompt_of(step)
            with self.subTest(kind=kind):
                self.assertEqual(text.count(handoff.PLAN_TESTS_PREFIX), 1)
                line = next(line for line in text.splitlines() if line.startswith(handoff.PLAN_TESTS_PREFIX))
                self.assertIn("zatwierdzony kontrakt w profilu projektu", line)
                self.assertIn("`make test-changed BASE={base} JOBS=1`", line)
                self.assertNotIn("testPolicy", step)
                self.assertEqual(self.approval_for(step)[1], (True, ""))
        # Format wyniku review planu i jego odczyt bez zmian.
        reviewed = self.of(card_id, "plan.reviewed")[0]
        self.assertEqual(reviewed["verdict"], "APPROVE")


FILE_TOOLS = {"Read", "Edit", "Write", "Glob", "Grep"}


class ContractGrantsFlowTest(PolicyFlowCase):
    """PT-280 (K-69 #3): zgody na polecenia zatwierdzonego kontraktu — z tej samej polityki co kartka startu albo
    wznowienia, liczone od nowa przy każdym; zmiana pliku bez zatwierdzenia i cofnięcie commita nie poszerzają listy."""
    proposal = plan_flow.PlanFlowTest.proposal
    submit = plan_flow.PlanFlowTest.submit
    approve_plan = plan_flow.PlanFlowTest.approve
    wave_plan = wave_flow.WaveFlowTest.wave_plan
    conflicting_plan = ConflictAndPlanningPolicyFlowTest.conflicting_plan

    def grants_of(self, step) -> list:
        """Zgody kroku sprawdzone naraz w zdarzeniu startu, w bloku kartki i w regułach Claude kroku, który naprawdę
        wystartował; tekst każdej zgody to polecenie z polityki tego kroku (jeden `TestPolicy`)."""
        expected = step["contractCommands"]
        policy = step["testPolicy"]
        self.assertTrue(set(expected) <= {policy["changed"], policy["full"]}, (expected, policy))
        text = self.prompt_of(step)
        if expected:
            self.assertEqual(text.count(handoff.CONTRACT_COMMANDS_OPEN), 1)
            block = text[text.index(handoff.CONTRACT_COMMANDS_OPEN):text.index(handoff.CONTRACT_COMMANDS_CLOSE)]
            listed = block.split(handoff.CONTRACT_COMMANDS_HEAD, 1)[1].split(handoff.CONTRACT_NOT_GRANTED)[0]
            self.assertEqual([line[2:] for line in listed.splitlines() if line.startswith("- ")], expected)
            self.assertIn(handoff.CONTRACT_COMMANDS_OPEN, text[text.rindex("Polecenia projektu w tym kroku"):])
        else:
            self.assertNotIn(handoff.CONTRACT_COMMANDS_OPEN, text)
        real = self.real(step)
        project = self.store.load().projects["proj"]
        listed = [*project.allowed_commands, *expected]
        rules, _ = commands.claude_tools(listed, project.denied_commands, step_dir=step["stepDir"])
        self.assertEqual(set(real.allowed_tools) - FILE_TOOLS, set(rules) - FILE_TOOLS)
        for command in expected:
            self.assertIn(f"Bash({command})", real.allowed_tools)
            self.assertNotIn(f"Bash({command} *)", real.allowed_tools)
            self.assertTrue(commands.is_allowed(command, listed))
        return expected

    def real(self, step):
        [real] = [s for s in self.launched if s.step_dir == step["stepDir"]]
        return real

    def assert_no_grant(self, step, *variants):
        self.assertEqual(self.grants_of(step), [])
        for command in variants:
            self.assertNotIn(f"Bash({command})", self.real(step).allowed_tools)

    def resume_asked(self, card_id, step):
        """Wznowienie kroku, który znowu zadał pytanie (atrapa pyta w każdej próbie): czeka na jego decyzję."""
        self.assertTrue(wait_until(lambda: any(d["step"] == step["step"] for d in
                                               self.shown(card_id)["derived"]["flow"]["decisions"]), timeout=20),
                        [e for e in self.events(card_id) if e["type"] in ("flow.error", "step.finished")])
        return self.resume(card_id, step)

    def revert_contract_commit(self):
        """Cofnięcie ostatniego commita Tablicy z plikiem kontraktu (ręczny `git revert`, bez zatwierdzenia)."""
        rel = os.path.join("projects", "proj", "box.yaml")
        commit = git(self.root, "log", "-1", "--format=%H", "--", rel).strip()
        self.assertTrue(commit)
        git(self.root, "revert", "--no-edit", commit)

    def test_ticket_grants_follow_approval_on_every_resume(self):
        self.approve(FIRST)
        card_id, first, _reply = self.asked("pytanie-zawsze")
        base = self.results(card_id, "ticket-branch")[0]["base"]
        old, new = f"make test-changed BASE={base} JOBS=1", f"make test-changed BASE={base} JOBS=2"
        self.assertEqual(self.grants_of(first), [old, "make test"])
        allowed_before = list(self.store.load().projects["proj"].allowed_commands)

        # Zmiana kontraktu w pliku bez zatwierdzenia: wznowienie nie ma ani starego, ani nowego polecenia.
        self.assertTrue(self.store.upsert_project({"slug": "proj", "tests": SECOND})["ok"])
        second = self.resume(card_id, first)
        self.assertTrue(second["testPolicy"]["unapproved"])
        self.assert_no_grant(second, old, new)

        # Zatwierdzenie (UpsertProject z okna projektu): kolejne wznowienie ma nowe polecenie.
        self.approve(SECOND)
        third = self.resume_asked(card_id, second)
        self.assertEqual(self.grants_of(third), [new, "make test"])
        self.assertNotIn(f"Bash({old})", self.real(third).allowed_tools)

        # Cofnięcie commita z plikiem: odcisk nie pasuje do treści — znowu bez zgód.
        self.revert_contract_commit()
        fourth = self.resume_asked(card_id, third)
        self.assert_no_grant(fourth, old, new)
        # Lista zapisana w projekcie się nie zmienia.
        self.assertEqual(self.store.load().projects["proj"].allowed_commands, allowed_before)

    def test_conflict_grants_use_merged_ticket_base(self):
        self.approve(FIRST)
        card_id = self.card(description="Plan z konfliktem\n\nATRAPA: conflict-error")
        self.conflicting_plan(card_id)
        self.assertTrue(wait_until(lambda: any(s.get("kind") == "conflict" for s in self.of(card_id, "step.started")),
                                   timeout=25), self.events(card_id))
        conflict = next(s for s in self.of(card_id, "step.started") if s.get("kind") == "conflict")
        base = next(r for r in self.results(card_id, "ticket-branch") if r["ticket"] == conflict["ticket"])["base"]
        self.assertNotEqual(git(conflict["worktree"], "rev-parse", "HEAD").strip(), base)
        old, new = f"make test-changed BASE={base} JOBS=1", f"make test-changed BASE={base} JOBS=2"
        self.assertEqual(self.grants_of(conflict), [old, "make test"])

        def retried(step):
            """Następna próba kroku konfliktu: ponowienie użytkownika albo automatyczne (atrapa zawsze kończy błędem,
            a Tide może sam ponowić krok po błędzie) — zgody liczy każda próba od nowa."""
            self.assertTrue(wait_until(lambda: any(e.get("step") == step["step"]
                                                   for e in self.of(card_id, "step.finished")), timeout=15))

            def child():
                return [s for s in self.of(card_id, "step.started") if s.get("parentStep") == step["step"]]

            def pending():
                return any(d["step"] == step["step"] for d in self.shown(card_id)["derived"]["flow"]["decisions"])

            self.assertTrue(wait_until(lambda: child() or pending(), timeout=20), self.events(card_id))
            if not child():
                reply = self.decide(card_id, {"choice": "retry", "step": step["step"]})
                self.assertTrue(reply["ok"] or wait_until(child, timeout=5), reply)
            found = wait_until(child, timeout=20)
            self.assertTrue(found, self.events(card_id))
            self.assertEqual(found[0]["kind"], "conflict")
            return found[0]

        self.assertTrue(self.store.upsert_project({"slug": "proj", "tests": SECOND})["ok"])
        second = retried(conflict)
        self.assert_no_grant(second, old, new)
        self.approve(SECOND)
        third = retried(second)
        self.assertEqual(self.grants_of(third), [new, "make test"])
        self.revert_contract_commit()
        self.assert_no_grant(retried(third), old, new)
