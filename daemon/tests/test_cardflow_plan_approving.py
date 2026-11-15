"""PT-407 (K-102): migawka karty mówi prawdę o trwającym zatwierdzaniu planu (`flow.plan.approving`, `approvalError`).

Dziennik składany ze zdarzeń publicznych, stan przez `fold`, widok przez `derived` i `build_decisions` — bez Qt
i bez dysku. Zachowanie kolejki (drugie wywołanie dostaje od razu „trwa”) sprawdza test_cardflow_approving_monitor.py.
"""
import unittest

from tide_daemon.cardflow.decisions import build_decisions
from tide_daemon.cardflow.state import BASE_HELD_APPROVAL, derived, fold

from .test_cardflow_decisions import CARD, Events, plan_events

PROPOSED_SEQ = 2   # `plan_events`: card.started (1), plan.proposed (2)
BASE_REQUEST = {"decision": "base-1", "baseProposal": {"valid": True, "ref": "refs/heads/x", "baseCommit": "abc",
                                                      "evidence": "wybór użytkownika"}}


def reviewed(**extra) -> Events:
    return plan_events(reviewer="Sonnet", verdict="APPROVE", findings=[], **extra)


def requested() -> Events:
    return reviewed().add("plan.approval_requested", proposal=PROPOSED_SEQ)


def plan_of(events: Events) -> dict:
    return derived(events.state(), CARD)["flow"]["plan"]


class ApprovingFromJournalTest(unittest.TestCase):
    def test_without_request_the_plan_is_not_approving(self):
        plan = plan_of(reviewed())
        self.assertEqual((plan["approving"], plan["approvalError"]), (False, ""))

    def test_request_without_approval_is_approving_and_status_stays_pending_semantics(self):
        events = requested()
        plan = plan_of(events)
        self.assertEqual((plan["approving"], plan["approvalError"]), (True, ""))
        self.assertEqual(plan["status"], "pending")   # semantyka `status` bez nowych wartości

    def test_approved_plan_is_no_longer_approving(self):
        events = requested().add("plan.approved", map={"#1": "PT-1", "#2": "PT-2"}, rows=[])
        plan = plan_of(events)
        self.assertEqual((plan["approving"], plan["approvalError"]), (False, ""))
        self.assertEqual(plan["status"], "approved")

    def test_request_for_an_older_proposal_does_not_count(self):
        events = requested().add("plan.proposed", planner="Opus", rows=[{"workId": "#1", "title": "A", "blockers": []}])
        self.assertEqual(plan_of(events)["approving"], False)

    def test_plan_difference_has_no_durable_request_so_it_is_never_approving(self):
        events = (reviewed().add("plan.approved", map={}, rows=[])
                  .add("plan.returned", feedback="popraw", baseline=[])
                  .add("plan.revision-ready", pending=True, baseline=[], difference=[])
                  .add("plan.reviewed", reviewer="Sonnet", verdict="APPROVE", findings=[]))
        plan = plan_of(events)
        self.assertEqual(plan["status"], "pending")
        self.assertEqual((plan["approving"], plan["approvalError"]), (False, ""))

    def test_failed_reservation_shows_error_and_is_not_approving(self):
        events = requested().add("result", action="reserve", key=f"plan:{PROPOSED_SEQ}:#1", ok=False,
                                 error="brak miejsca na numer")
        plan = plan_of(events)
        self.assertEqual((plan["approving"], plan["approvalError"]), (False, "brak miejsca na numer"))

    def test_failed_reservation_older_than_the_request_is_ignored(self):
        events = (reviewed().add("result", action="reserve", key=f"plan:{PROPOSED_SEQ}:#1", ok=False, error="stary")
                  .add("plan.approval_requested", proposal=PROPOSED_SEQ))
        plan = plan_of(events)
        self.assertEqual((plan["approving"], plan["approvalError"]), (True, ""))

    def test_later_successful_reservation_clears_the_error(self):
        key = f"plan:{PROPOSED_SEQ}:#1"
        events = (requested().add("result", action="reserve", key=key, ok=False, error="chwilowy")
                  .add("result", action="reserve", key=key, ok=True, ticket="PT-1"))
        plan = plan_of(events)
        self.assertEqual((plan["approving"], plan["approvalError"]), (True, ""))

    def test_reservation_of_another_proposal_does_not_count(self):
        events = requested().add("result", action="reserve", key="plan:99:#1", ok=False, error="cudza")
        self.assertEqual(plan_of(events)["approving"], True)

    def test_card_base_decision_shows_the_reason_instead_of_approving(self):
        events = requested().add("card.base-requested", **BASE_REQUEST)
        plan = plan_of(events)
        self.assertEqual((plan["approving"], plan["approvalError"]), (False, BASE_HELD_APPROVAL))
        self.assertIn("decyzja o bazie", plan["approvalError"])

    def test_base_decision_wins_over_a_failed_reservation(self):
        events = (requested().add("result", action="reserve", key=f"plan:{PROPOSED_SEQ}:#1", ok=False, error="x")
                  .add("card.base-requested", **BASE_REQUEST))
        self.assertEqual(plan_of(events)["approvalError"], BASE_HELD_APPROVAL)

    def test_agent_base_proposal_blocks_like_the_card_decision(self):
        events = (requested().step("K-1-plan-1", kind="planning", result="question", question="Zła baza?",
                                   baseProposal={"valid": True, "ref": "refs/heads/x", "baseCommit": "abc"}))
        plan = plan_of(events)
        self.assertEqual((plan["approving"], plan["approvalError"]), (False, BASE_HELD_APPROVAL))

    def test_after_the_base_decision_is_settled_the_approval_resumes(self):
        events = (requested().add("card.base-requested", **BASE_REQUEST)
                  .add("card.base-kept", decision="base-1"))
        self.assertEqual(plan_of(events)["approving"], True)


class ApprovingPhaseAndDecisionsTest(unittest.TestCase):
    def test_phase_is_background_approval_and_no_plan_approval_decision(self):
        events = requested()
        shown = derived(events.state(), CARD)
        self.assertEqual((shown["column"], shown["note"]), ("progress", "zatwierdzanie planu w tle"))
        self.assertEqual(shown["question"], "")
        self.assertNotIn("plan-approval", [d["kind"] for d in build_decisions(events.state())])

    def test_without_the_request_the_plan_is_still_offered_for_approval(self):
        events = reviewed()
        shown = derived(events.state(), CARD)
        self.assertEqual((shown["column"], shown["question"]), ("waiting", "Zatwierdzić plan?"))
        self.assertIn("plan-approval", [d["kind"] for d in build_decisions(events.state())])

    def test_failed_reservation_returns_the_approval_decision(self):
        events = requested().add("result", action="reserve", key=f"plan:{PROPOSED_SEQ}:#1", ok=False, error="błąd")
        shown = derived(events.state(), CARD)
        self.assertEqual((shown["column"], shown["question"]), ("waiting", "Zatwierdzić plan?"))
        self.assertIn("plan-approval", [d["kind"] for d in build_decisions(events.state())])

    def test_held_base_keeps_todays_phase_and_decisions(self):
        events = requested().add("card.base-requested", **BASE_REQUEST)
        shown = derived(events.state(), CARD)
        self.assertNotEqual(shown["note"], "zatwierdzanie planu w tle")
        self.assertIn("plan-approval", [d["kind"] for d in build_decisions(events.state())])


if __name__ == "__main__":
    unittest.main()
