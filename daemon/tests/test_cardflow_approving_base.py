"""PT-407 (K-102): zatwierdzanie planu stojące na wstrzymanej decyzji o bazie (PT-208) kończy się po `keep` i ticku.

Prawdziwy silnik na dzienniku w katalogu tymczasowym (wzorzec test_cardflow_plan_integration.py): prośba o
zatwierdzenie jest w dzienniku, baza wstrzymana — migawka pokazuje powód zamiast „trwa”, a po `baseDecision: keep`
jeden tick dokańcza zatwierdzenie (`plan.approved`), więc faza „zatwierdzanie planu w tle” nie utyka bez przycisku.
"""
import json

from tide_daemon.cardflow.decisions import build_decisions
from tide_daemon.cardflow.engine import FlowDeferred, FlowError
from tide_daemon.cardflow.state import BASE_HELD_APPROVAL, derived, fold

from . import test_cardflow_plan_integration as fixtures
from .test_cardflow import FlowInProcess

BASE_REQUEST = {"decision": "base-1", "baseProposal": {"valid": True, "ref": "refs/heads/x", "baseCommit": "abc",
                                                      "evidence": "wybór użytkownika"}}


class ApprovalOnHeldBaseTest(FlowInProcess):
    # Pomocniki z modułu fixture, bez dziedziczenia jego testów (moduł, nie klasa: bez dubla testów bazowych).
    proposal = fixtures.PlanFlowTest.proposal
    submit = fixtures.PlanFlowTest.submit

    def setUp(self):
        super().setUp()
        self.assertTrue(json.loads(self.board.upsert_project(json.dumps(
            {"slug": "proj", "repo": self.repo, "aiDocs": "repo"})))["ok"])

    def plan(self, card):
        return derived(fold(self.flow.engine.journal.read(card)), {"column": "todo"})["flow"]["plan"]

    def kinds(self, card):
        return [d["kind"] for d in build_decisions(fold(self.flow.engine.journal.read(card)))]

    def test_held_base_shows_the_reason_and_keep_with_a_tick_finishes_the_approval(self):
        card = self.card()
        self.assertTrue(self.submit(card, self.proposal(card))["ok"])
        self.stop_flow()          # dalej sterujemy silnikiem ręcznie: bez wątku kolejki i tła
        engine = self.flow.engine
        journal = engine.journal
        proposal = fold(journal.read(card)).plan_proposed
        journal.append(card, "plan.approval_requested", proposal=proposal["seq"])
        journal.append(card, "card.base-requested", **BASE_REQUEST)

        # Wstrzymana baza: zatwierdzanie nie rusza (brak numerów i `plan.approved`), a migawka mówi dlaczego.
        with self.assertRaises((FlowDeferred, FlowError)):
            engine.approve_plan(card)
        self.assertEqual(self.of(card, "plan.approved"), [])
        plan = self.plan(card)
        self.assertEqual((plan["approving"], plan["approvalError"]), (False, BASE_HELD_APPROVAL))
        self.assertIn("plan-approval", self.kinds(card))

        # Zachowaj bazę: samo rozstrzygnięcie niczego jeszcze nie zatwierdza, ale zdejmuje wstrzymanie.
        reply = engine.resolve_step(card, {"choice": "answer", "step": BASE_REQUEST["decision"],
                                           "baseDecision": "keep"})
        self.assertEqual((reply["ok"], reply["baseDecision"]), (True, "keep"), reply)
        self.assertEqual(len(self.of(card, "card.base-kept")), 1)
        self.assertEqual(self.of(card, "plan.approved"), [])
        plan = self.plan(card)
        self.assertEqual((plan["approving"], plan["approvalError"]), (True, ""))

        # Tick wznawia zatwierdzanie z zapisanej prośby: `plan.approved`, koniec „trwa”.
        engine.tick()
        approved = self.of(card, "plan.approved")
        self.assertEqual(len(approved), 1, self.events(card))
        self.assertEqual(set(approved[0]["map"]), {"#1", "#2"})
        plan = self.plan(card)
        self.assertEqual((plan["approving"], plan["approvalError"], plan["status"]), (False, "", "approved"))
        self.assertNotIn("plan-approval", self.kinds(card))
