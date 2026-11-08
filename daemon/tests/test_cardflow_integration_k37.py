"""PT-256: współdziałanie opiekuna z filtrami poprzedniego kontekstu i błędami mastera."""
import unittest
from types import SimpleNamespace
from unittest import mock

from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.state import fold
from . import test_cardflow_caretaker as fixtures


class IntegrationTest(unittest.TestCase):
    def returned(self):
        j = fixtures.Journal()
        row = {"id": "PT-7", "workId": "#1", "blockers": []}
        j.add("plan.approved", rows=[row])
        j.failed_twice("first", "failed")
        j.add("plan.returned", caretaker="error:first", caretakerFrom="failed", baseline=[row])
        j.add("plan.revision-discarded")
        return j

    def engine(self, journal):
        engine = FlowEngine.__new__(FlowEngine)
        engine.journal = SimpleNamespace(read=lambda card: journal.events,
                                         read_complete=lambda card: journal.events,
                                         state=lambda card: fold(journal.events),
                                         append=lambda card, kind, **data: journal.add(kind, **data))
        return engine

    def test_normal_pass_resumes_a_returned_ticket_after_user_resolves_revision(self):
        j = self.returned()
        engine = self.engine(j)
        with mock.patch.object(engine, "_advance_plan_review", return_value=False), \
                mock.patch.object(engine, "_advance_planning", return_value=False), \
                mock.patch.object(engine, "_plan_next"), mock.patch.object(engine, "_advance_close", return_value=False), \
                mock.patch.object(engine, "_resume_step") as resume:
            self.assertEqual(engine._advance_card("K-1"), [])
        resume.assert_called_once()
        self.assertEqual(resume.call_args.args[2]["step"], "failed")
        self.assertTrue(resume.call_args.args[3]["caretaker"])

    def test_resume_allows_plan_return_but_rejects_previous_card_context(self):
        j = self.returned()
        engine = self.engine(j)
        with mock.patch.object(engine, "_project", return_value=(SimpleNamespace(), SimpleNamespace())), \
                mock.patch.object(engine, "_resume_executor", side_effect=FlowError("executor-reached", "test")):
            st = fold(j.events)
            with self.assertRaises(FlowError) as error:
                engine._resume_step("K-1", st, st.steps["failed"], {"choice": "retry", "caretaker": True})
            self.assertEqual(error.exception.code, "executor-reached")
            j.add("card.started", entry="now")
            st = fold(j.events)
            with self.assertRaises(FlowError) as error:
                engine._resume_step("K-1", st, st.steps["failed"], {"choice": "retry"})
            self.assertEqual(error.exception.code, "stale-context")

    def test_legacy_operation_success_keeps_parallel_stage_error(self):
        j = fixtures.Journal()
        j.add("result", action="plan-commit", key="plan", ok=False)
        j.add("flow.error", error="historyczny błąd")
        j.add("flow.error", stage="advance:PT-8", error="błąd równoległego ticketu")
        j.add("result", action="plan-commit", key="plan", ok=True)
        self.assertEqual(set(j.st.flow_errors), {"advance:PT-8"})
        self.assertEqual(j.st.flow_error, "błąd równoległego ticketu")

    def test_user_can_return_a_ready_revision_while_caretaker_cannot(self):
        j = self.returned()
        rows = j.st.plan_approved["rows"]
        j.add("plan.proposed", rows=rows, planner="Sonnet")
        j.add("plan.revision-ready", pending=True, baseline=rows, feedback="Pierwsze uwagi")
        j.add("plan.reviewed", verdict="APPROVE")
        engine = self.engine(j)
        with self.assertRaises(FlowError) as error:
            engine.edit_plan("K-1", {"action": "return", "feedback": "Uwagi opiekuna"},
                             caretaker_problem="error:first")
        self.assertEqual(error.exception.code, "revision-active")
        with mock.patch.object(engine, "_advance_user_revision"):
            reply = engine.edit_plan("K-1", {"action": "return", "feedback": "Drugie uwagi"})
        self.assertTrue(reply["ok"])
        self.assertEqual(j.st.plan_revision["baseline"], rows)
        self.assertIn("Pierwsze uwagi", j.st.plan_revision["feedback"])
        self.assertIn("Drugie uwagi", j.st.plan_revision["feedback"])
