"""PT-73: alarm postoju, powiadomienia i zrobione tickety w raporcie."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from tide_daemon.cardflow.engine import FlowEngine
from tide_daemon.cardflow.journal import Journal
from tide_daemon.cardflow.state import derived, fold
from tide_daemon.reports.plans import _attach_flow


class OverviewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.board = os.path.join(self.temp.name, "board")
        os.mkdir(self.board)
        self.now = [1_800_000_000.0]
        self.journal = Journal(self.board, mirror=os.path.join(self.temp.name, "mirror"),
                               clock=lambda: self.now[0])
        self.store = SimpleNamespace(root=self.board,
                                     load=lambda: SimpleNamespace(cards={1: SimpleNamespace(column="todo")}))
        self.engine = FlowEngine(self.store, self.journal, clock=lambda: self.now[0])
        self.journal.append("K-1", "card.started", entry="plan", repo=self.temp.name, column="todo")

    def test_plan_approval_notifies_once(self):
        self.journal.append("K-1", "plan.proposed", rows=[], title="Plan")
        self.journal.append("K-1", "plan.reviewed", verdict="APPROVE")
        self.engine._queue_notices("K-1")
        self.engine._queue_notices("K-1")
        self.assertEqual([n["result"] for n in self.engine.take_notices()], ["plan"])

    def test_card_stalls_once_and_new_step_clears_marker(self):
        self.now[0] += 181
        self.engine._guard_card("K-1")
        self.engine._guard_card("K-1")
        self.assertEqual([n["result"] for n in self.engine.take_notices()], ["stalled"])
        self.assertTrue(derived(fold(self.journal.read("K-1")), {"column": "todo"})["flow"]["stalledSince"])
        self.journal.append("K-1", "step.started", step="s1", ticket="PT-1", kind="ticket", executor="Sonnet")
        shown = derived(fold(self.journal.read("K-1")), {"column": "todo"})
        self.assertEqual(shown["flow"]["stalledSince"], "")
        self.assertEqual(shown["flow"]["steps"][0]["startedTs"], self.now[0])

    def test_stall_keeps_ticket_counts_visible(self):
        self.journal.append("K-1", "plan.approved", rows=[{"id": "PT-1", "workId": "PT-1",
                                                          "title": "Gotowy", "blockers": []}])
        self.journal.append("K-1", "result", action="merge", key="PT-1", ok=True)
        self.now[0] += 181
        self.engine._guard_card("K-1")
        shown = derived(fold(self.journal.read("K-1")), {"column": "todo"})
        self.assertEqual(shown["done"], 1)
        self.assertEqual([(row["id"], row["state"]) for row in shown["tickets"]], [("PT-1", "gotowe")])
        self.assertTrue(shown["flow"]["stalledSince"])

    def test_finished_card_does_not_queue_plan_notification(self):
        self.journal.append("K-1", "plan.proposed", rows=[], title="Plan")
        self.journal.append("K-1", "plan.reviewed", verdict="APPROVE")
        self.journal.append("K-1", "card.finished")
        self.engine._queue_notices("K-1")
        self.assertEqual(self.engine.take_notices(), [])

    def test_each_card_close_notifies_once(self):
        self.journal.append("K-1", "card.closed", summary="Pierwsze podsumowanie")
        self.engine._queue_notices("K-1")
        self.engine._queue_notices("K-1")
        self.assertEqual([notice["result"] for notice in self.engine.take_notices()], ["review"])
        self.journal.append("K-1", "plan.returned", feedback="Dodaj poprawkę", baseline=[])
        self.journal.append("K-1", "card.closed", summary="Nowe podsumowanie")
        self.engine._queue_notices("K-1")
        self.assertEqual([notice["result"] for notice in self.engine.take_notices()], ["review"])

    def test_interactive_step_updates_activity_without_stopping_for_inactivity(self):
        self.journal.append("K-1", "step.started", step="s1", ticket="", kind="planning",
                            mode="interactive", executor="Sonnet", stepDir=self.temp.name)
        step = fold(self.journal.read("K-1")).steps["s1"]
        self.engine.guard_every = 0
        with mock.patch.object(self.engine, "_console_ended", return_value=False), \
             mock.patch.object(self.engine, "_progress_mark", side_effect=[(0,), (1,)]), \
             mock.patch("tide_daemon.cardflow.engine.time.monotonic", side_effect=[10.0, 80.0]):
            self.engine._poll_interactive("K-1", step)
            self.now[0] += 70
            self.engine._poll_interactive("K-1", step)
        events = self.journal.read("K-1")
        self.assertEqual(len([event for event in events if event["type"] == "step.activity"]), 1)
        self.assertFalse([event for event in events if event["type"] == "step.finished"])
        shown = derived(fold(events), {"column": "todo"})
        self.assertGreater(shown["flow"]["steps"][0]["lastActivityTs"],
                           shown["flow"]["steps"][0]["startedTs"])

    def test_named_limit_does_not_stall(self):
        self.journal.append("K-1", "step.waiting", ticket="PT-1", kind="ticket", reason="limit dostawcy")
        self.now[0] += 181
        self.engine._guard_card("K-1")
        self.assertFalse([e for e in self.journal.read("K-1") if e["type"] == "card.stalled"])

    def test_completed_ticket_without_next_step_is_reported_as_stalled(self):
        self.journal.append("K-1", "plan.approved", rows=[{"id": "PT-1", "title": "Zrobione", "blockers": []}])
        self.journal.append("K-1", "result", action="merge", key="PT-1", ok=True)
        self.now[0] += 181
        self.engine._guard_card("K-1")
        self.assertEqual(self.engine.take_notices()[0]["result"], "stalled")

    def test_report_reads_completed_ticket_with_executor_from_journal(self):
        old_state = os.environ.get("XDG_STATE_HOME")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.temp.name, "state")
        self.addCleanup(lambda: os.environ.pop("XDG_STATE_HOME", None) if old_state is None
                        else os.environ.__setitem__("XDG_STATE_HOME", old_state))
        journal = Journal(self.board, clock=lambda: self.now[0])
        journal.append("K-2", "card.started", entry="plan", repo=self.temp.name)
        journal.append("K-2", "plan.approved", rows=[{"id": "PT-2", "title": "Eksport"}])
        journal.append("K-2", "step.started", step="s2", ticket="PT-2", kind="ticket", executor="Astra")
        journal.append("K-2", "result", action="merge", key="PT-2", ok=True)
        other_repo = os.path.join(self.temp.name, "other")
        os.mkdir(other_repo)
        journal.append("K-2", "card.started", entry="plan", repo=other_repo)
        journal.append("K-2", "plan.approved", rows=[{"id": "OTHER-1", "title": "Drugi projekt"}])
        journal.append("K-2", "step.started", step="s3", ticket="OTHER-1", kind="ticket", executor="Sonnet")
        journal.append("K-2", "result", action="merge", key="OTHER-1", ok=True)
        project = SimpleNamespace(path=self.temp.name, is_repo=True, plans=[])
        second = SimpleNamespace(path=other_repo, is_repo=True, plans=[])
        _attach_flow([project, second], self.now[0] - 60, self.now[0] + 60, self.board)
        self.assertEqual(project.plans[0]["tickets"][0]["note"], "wykonał Astra")
        self.assertEqual(second.plans[0]["tickets"][0]["note"], "wykonał Sonnet")


class StalePlanReviewWaitTest(unittest.TestCase):
    """K-31: dawna blokada limitu nie może zasłaniać decyzji po nieudanym review planu."""

    def test_started_review_clears_old_limit_wait_and_shows_the_decision(self):
        events = [
            {"seq": 1, "type": "card.started", "entry": "planning", "executor": "Opus"},
            {"seq": 2, "type": "plan.proposed", "rows": [], "planner": "Opus", "title": "X"},
            {"seq": 3, "type": "step.waiting", "ticket": "", "kind": "plan-review", "provider": "anthropic",
             "reason": "czeka na limit Claude do sobota 18:00 (pozostało 7% w oknie tygodniowym)"},
            {"seq": 4, "type": "step.started", "step": "K-1-plan-1", "kind": "plan-review", "ticket": "",
             "executor": "Haiku", "provider": "anthropic", "attempt": 1},
            {"seq": 5, "type": "step.finished", "step": "K-1-plan-1", "ticket": "", "result": "error",
             "reason": "no-result"},
        ]
        st = fold(events)
        self.assertIsNone(st.plan_review_wait)
        out = derived(st, {"column": "todo"})
        self.assertNotIn("limit", out["note"])
        self.assertEqual(out["flow"]["decisions"][0]["kind"], "error")


class QueuedPlanReviewTest(unittest.TestCase):
    """03.11, K-31: kolejka miejsc u dostawcy to nie decyzja użytkownika i nie blokuje wznowienia review."""

    def test_slot_queue_keeps_card_in_progress_and_does_not_block_review(self):
        events = [
            {"seq": 1, "type": "card.started", "entry": "planning", "executor": "Opus"},
            {"seq": 2, "type": "plan.proposed", "rows": [], "planner": "Opus", "title": "X"},
            {"seq": 3, "type": "step.waiting", "ticket": "", "kind": "plan-review", "provider": "anthropic",
             "reason": "limit kroków dostawcy anthropic: 6/6"},
        ]
        st = fold(events)
        self.assertIsNone(st.plan_review_wait)
        out = derived(st, {"column": "todo"})
        self.assertEqual(out["column"], "progress")
        self.assertIn("w kolejce", out["note"])

    def test_exhausted_limit_still_asks_for_a_reviewer(self):
        events = [
            {"seq": 1, "type": "card.started", "entry": "planning", "executor": "Opus"},
            {"seq": 2, "type": "plan.proposed", "rows": [], "planner": "Opus", "title": "X"},
            {"seq": 3, "type": "step.waiting", "ticket": "", "kind": "plan-review", "provider": "anthropic",
             "reason": "czeka na limit Claude do sobota 18:00"},
        ]
        st = fold(events)
        self.assertIsNotNone(st.plan_review_wait)
        self.assertEqual(derived(st, {"column": "todo"})["column"], "waiting")


class DiscardedRevisionErrorsTest(unittest.TestCase):
    """K-31: błędy odrzuconej poprawki po review mówią, że obowiązuje poprzednia wersja, i znikają po zatwierdzeniu."""

    def events(self):
        return [
            {"seq": 1, "type": "card.started", "entry": "planning", "executor": "Opus"},
            {"seq": 2, "type": "plan.proposed", "rows": [], "planner": "Opus", "title": "X", "sourceStep": "K-1-plan-1-0"},
            {"seq": 3, "type": "plan.checked", "valid": False, "step": "K-1-plan-revise-2",
             "errors": ["#1: recenzent musi mieć inny model i zachować pracę lokalną"]},
        ]

    def test_errors_explain_the_discarded_revision(self):
        errors = derived(fold(self.events()), {"column": "todo"})["flow"]["plan"]["errors"]
        self.assertIn("odrzucona", errors[0])
        self.assertIn("#1: recenzent", errors[1])

    def test_errors_disappear_after_approval(self):
        events = self.events() + [{"seq": 4, "type": "plan.approved", "rows": []}]
        flow = derived(fold(events), {"column": "todo"})["flow"]
        self.assertEqual(flow["plan"]["errors"], [])
