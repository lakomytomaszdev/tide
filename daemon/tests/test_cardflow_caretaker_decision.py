"""PT-154: decyzja opiekuna bez własnej decyzji kroku, zamknięcie i powiadomienie raz."""
from types import SimpleNamespace
import unittest
from unittest import mock

from tide_daemon.cardflow import caretaker
from tide_daemon.cardflow.engine import FlowEngine, FlowError
from tide_daemon.cardflow.state import fold
from apps.board.tests.decisionfix import journal_caretaker_card


class CaretakerDecisionTest(unittest.TestCase):
    def engine(self, trigger="flow-error", limit=False):
        events = journal_caretaker_card(trigger, limit)
        engine = FlowEngine.__new__(FlowEngine)
        engine._settings_data = mock.Mock(return_value={})
        engine.models_config = engine.catalog = None

        def append(card, kind, /, **fields):
            event = {"seq": len(events) + 1, "ts": "2026-11-03T11:01:00+01:00", "type": kind, **fields}
            events.append(event)
            return event

        engine.journal = SimpleNamespace(read=lambda card: events, append=append)
        engine._notices = []
        engine._caretaker_withheld = mock.Mock(return_value=set())
        engine._limit_view = mock.Mock()
        return engine, events

    def test_both_triggers_and_limit_give_a_decision_and_one_notice_even_after_restart(self):
        for trigger in ("flow-error", "stalled"):
            for limit in (False, True):
                with self.subTest(trigger=trigger, limit=limit):
                    engine, events = self.engine(trigger, limit)
                    for _ in range(2):
                        shown = engine.derived({"id": "K-8", "column": "todo"})
                        self.assertEqual(shown["column"], "waiting")
                        [decision] = shown["flow"]["decisions"]
                        self.assertEqual(decision["kind"], "caretaker")
                        self.assertTrue(decision["happened"] and decision["why"])
                        self.assertTrue(decision["caretaker"]["summary"] and decision["caretaker"]["recommendation"])
                        self.assertEqual([o["title"] for o in decision["options"]],
                                         ["Ponów etap", "Zamknij", "Porozmawiaj z opiekunem"])
                        engine._queue_notices("K-8")
                    self.assertEqual(len(engine.take_notices()), 1)
                    engine._queue_notices("K-8")
                    self.assertEqual(engine.take_notices(), [])

    def test_choices_close_the_problem_reset_count_and_only_retry_runs_the_stage(self):
        for trigger in ("flow-error", "stalled"):
            for choice in ("retry-stage", "dismiss"):
                with self.subTest(trigger=trigger, choice=choice):
                    engine, events = self.engine(trigger)
                    engine._advance_card = mock.Mock(return_value=[])
                    reply = engine.resolve_caretaker("K-8", {"choice": choice})
                    self.assertTrue(reply["ok"])
                    self.assertEqual(engine._advance_card.call_count, int(choice == "retry-stage"))
                    st = fold(events)
                    self.assertEqual(caretaker.owned(st, reply["problem"])["count"], 0)
                    self.assertFalse(caretaker.owned(st, reply["problem"])["handed"])
                    self.assertEqual(caretaker.problems(st), [])
                    if trigger == "flow-error":
                        engine.journal.append("K-8", "flow.error", stage="advance", error="Nowy błąd")
                        engine.journal.append("K-8", "flow.error-persisted", stage="advance", error="Nowy błąd")
                    else:
                        engine.journal.append("K-8", "card.stalled")
                    self.assertEqual(caretaker.owned(fold(events), reply["problem"])["count"], 0)
                    self.assertEqual(engine.derived({"id": "K-8", "column": "todo"})["flow"]["decisions"], [])

    def test_each_escalated_stage_describes_its_own_error_in_decision_and_notice(self):
        engine, events = self.engine()
        engine.journal.append("K-8", "flow.error", stage="close", error="Nie udało się domknąć karty.")
        engine.journal.append("K-8", "flow.error-persisted", stage="close", error="Nie udało się domknąć karty.")
        engine.journal.append("K-8", "caretaker.started", problem="flow-error:close", step="K-8-care-2",
                              target="", trigger="flow-error", n=1)
        engine.journal.append("K-8", "caretaker.escalated", problem="flow-error:close",
                              step="K-8-care-2", target="", summary="Nie udało się domknąć karty.")
        shown = engine.derived({"id": "K-8", "column": "todo"})
        expected = {"flow-error:advance": "Nie udało się przygotować etapu karty.",
                    "flow-error:close": "Nie udało się domknąć karty."}
        decisions = shown["flow"]["decisions"]
        self.assertEqual({d["technical"]["problem"]: d["happened"] for d in decisions}, expected)
        engine._queue_notices("K-8")
        self.assertEqual({n["step"]: n["detail"] for n in engine.take_notices()},
                         {d["id"]: expected[d["technical"]["problem"]] for d in decisions})
        engine._queue_notices("K-8")
        self.assertEqual(engine.take_notices(), [])

    def test_caretaker_decision_takes_priority_over_parallel_ticket_work(self):
        for stage in ("close", "advance:PT-1"):
            with self.subTest(stage=stage):
                engine, events = self.engine()
                for event in events:
                    if event.get("stage") == "advance":
                        event["stage"] = stage
                    if event.get("problem") == "flow-error:advance":
                        event["problem"] = "flow-error:" + stage
                engine.journal.append("K-8", "step.started", step="PT-99-ticket", kind="ticket",
                                      ticket="PT-99", executor="Sonnet")
                shown = engine.derived({"id": "K-8", "column": "todo"})
                self.assertEqual(shown["column"], "waiting")
                [decision] = shown["flow"]["decisions"]
                self.assertEqual(decision["kind"], "caretaker")
                self.assertEqual(decision["happened"], "Nie udało się przygotować etapu karty.")
                self.assertEqual(shown["flow"]["flowError"], decision["happened"])
                self.assertIn("flowErrorHint", shown["flow"])
                self.assertEqual([s["step"] for s in fold(events).running()], ["PT-99-ticket"])
                [running] = [s for s in shown["flow"]["steps"] if s["id"] == "PT-99-ticket"]
                self.assertEqual(running["result"], "")
                engine.resolve_caretaker("K-8", {"choice": "dismiss"})
                self.assertEqual(engine.derived({"id": "K-8", "column": "todo"})["column"], "progress")
                self.assertEqual([s["step"] for s in fold(events).running()], ["PT-99-ticket"])

    def test_bad_choice_and_stale_problem_do_not_write(self):
        engine, events = self.engine()
        for request, code in (({"choice": "merge"}, "bad-choice"),
                              ({"choice": "dismiss", "problem": "stalled:card"}, "not-waiting"),
                              ({"choice": "dismiss", "caretaker": True}, "bad-request")):
            before = list(events)
            with self.assertRaises(FlowError) as caught:
                engine.resolve_caretaker("K-8", request)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(events, before)

    def test_parallel_ticket_completion_preserves_unresolved_stage_decision(self):
        for stage in ("close", "advance:PT-1"):
            for limit in (False, True):
                with self.subTest(stage=stage, limit=limit):
                    engine, events = self.engine(limit=limit)
                    problem = "flow-error:" + stage
                    for event in events:
                        if event.get("stage") == "advance":
                            event["stage"] = stage
                        if event.get("problem") == "flow-error:advance":
                            event["problem"] = problem
                    before = engine.derived({"id": "K-8", "column": "todo"})["flow"]["decisions"]
                    engine._queue_notices("K-8")
                    self.assertEqual(len(engine.take_notices()), 1)
                    engine.journal.append("K-8", "step.started", step="PT-99-ticket", kind="ticket",
                                          ticket="PT-99", executor="Sonnet")
                    engine.journal.append("K-8", "step.finished", step="PT-99-ticket", result="done")
                    st = fold(events)
                    self.assertIn(stage, st.flow_errors)
                    self.assertIn(problem, [p["id"] for p in caretaker.problems(st)])
                    own = caretaker.owned(st, problem)
                    self.assertEqual((own["count"], own["handed"]), (1, True))
                    shown = engine.derived({"id": "K-8", "column": "todo"})
                    self.assertEqual(shown["column"], "waiting")
                    self.assertEqual(shown["flow"]["decisions"], before)
                    engine._queue_notices("K-8")
                    self.assertEqual(engine.take_notices(), [])
                    self.assertTrue(engine.resolve_caretaker("K-8", {"choice": "dismiss"})["ok"])
                    self.assertEqual(caretaker.owned(fold(events), problem)["count"], 0)

    def test_stage_recovery_closes_escalation_and_resets_count_before_recurrence(self):
        for stage, kind, ticket in (("close", "close", ""), ("advance:PT-1", "ticket", "PT-1")):
            with self.subTest(stage=stage):
                engine, events = self.engine()
                problem = "flow-error:" + stage
                for event in events:
                    if event.get("stage") == "advance":
                        event["stage"] = stage
                    if event.get("problem") == "flow-error:advance":
                        event["problem"] = problem
                engine.journal.append("K-8", "step.started", step="recovery", kind=kind, ticket=ticket)
                engine.journal.append("K-8", "step.finished", step="recovery", result="done")
                self.assertNotIn(stage, fold(events).flow_errors)
                engine.journal.append("K-8", "flow.error", stage=stage, error="Nowy błąd")
                engine.journal.append("K-8", "flow.error-persisted", stage=stage, error="Nowy błąd")
                own = caretaker.owned(fold(events), problem)
                self.assertEqual((own["count"], own["handed"]), (0, False))
                self.assertEqual(engine.derived({"id": "K-8", "column": "todo"})["flow"]["decisions"], [])

    def test_pause_and_manual_backlog_take_priority_over_caretaker_decision(self):
        for entry in ("now", "plan"):
            for paused, column in ((True, "todo"), (False, "backlog"), (True, "backlog")):
                with self.subTest(entry=entry, paused=paused, column=column):
                    engine, events = self.engine()
                    events[0]["entry"] = entry
                    if column == "backlog":
                        engine.journal.append("K-8", "card.moved", **{"from": "waiting", "to": "backlog"})
                    if paused:
                        engine.journal.append("K-8", "card.paused")
                    shown = engine.derived({"id": "K-8", "column": column})
                    if not paused:
                        self.assertIsNone(shown)
                    else:
                        self.assertEqual(shown["column"], "backlog" if column == "backlog" else "progress")
                        self.assertTrue(shown["flow"]["paused"])
                        self.assertIn("wstrzymana", shown["note"])
                        self.assertEqual([d["kind"] for d in shown["flow"]["decisions"]], ["caretaker"])
                    self.assertTrue(caretaker.owned(fold(events), "flow-error:advance")["handed"])
