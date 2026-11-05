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

        def append(card, kind, **fields):
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
                        self.assertEqual([o["title"] for o in decision["options"]], ["Ponów etap", "Zamknij"])
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
