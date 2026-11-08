"""PT-287 (K-69 #10): silnik liczy jedną politykę testów na każdy start i wznowienie kroku.

Przepływ przez publiczne wejście silnika (StartCard, ResolveCardStep) z atrapą procesu kroku (tests/flowfakes.py),
w procesie testu (fixture `FlowInProcess`). Przypadki decyzji `effective_tests` i treści kartek zostają w testach
jednostkowych #2 (test_cardflow_effective_tests.py); tu tylko kilka scenariuszy integracji z silnikiem.
"""
import json
import os
from unittest import mock

from tide_daemon.board import contract_approval
from tide_daemon.cardflow import handoff
from tide_daemon.tasks import agentcmd

from .privatebus import wait_until
from .test_cardflow import FlowInProcess
from .test_cardflow_dbus import git
from .test_cardflow_effective_tests import BASE, CHANGED, DETECTED_LINE, MASTER_FRAGMENTS

FIRST = {"changed": "make test-changed BASE={base} JOBS=1", "full": "make test"}
SECOND = {"changed": "make test-changed BASE={base} JOBS=2", "full": "make test"}
SECTION = "Obowiązujące testy ("


class StepTestPolicyFlowTest(FlowInProcess):
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

    def asked(self):
        """Karta z pytaniem kroku ticketu: (id karty, zdarzenie startu, odpowiedź StartCard)."""
        card_id = self.card(description="Dodaj eksport.\n\nATRAPA: pytanie")
        reply = self.start(card_id)
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
        zapisu dokładnie tego kroku (worktree i `out` z jego zdarzenia)."""
        calls = [(slug, probe, result) for slug, probe, result in self.approvals if probe.step_dir == step["stepDir"]]
        self.assertEqual(len(calls), 1, [p.step_dir for _, p, _ in self.approvals])
        slug, probe, result = calls[0]
        self.assertEqual(slug, "proj")
        areas = agentcmd.step_write_areas(probe).writable()
        self.assertIn(step["worktree"], areas)
        self.assertIn(os.path.join(step["stepDir"], "out"), areas)
        return probe, result

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
        self.approve(FIRST)
        card_id, first, _reply = self.asked()
        self.assertEqual(first["testPolicy"]["source"], "profile")
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
