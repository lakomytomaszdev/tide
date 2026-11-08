"""Scenariusze resetu ustawiają wyczerpany odczyt przed obsługą błędu dostawcy."""
import unittest
from types import SimpleNamespace
from unittest import mock

from . import test_cardflow_limits as scenarios


class LimitReadingSyncTest(unittest.TestCase):
    def test_automatic_reset_when_poll_precedes_start_reply(self):
        case = scenarios.TransferFlowTest("test_limit_without_any_choice_resumes_by_itself_after_the_reset")
        engine = SimpleNamespace(clock=lambda: 1_800_000_000, limits_source=None,
                                 get_flow_settings=lambda: {"limitThresholdPercent": 10})
        case.flow = SimpleNamespace(engine=engine)
        first = {"provider": "anthropic", "ticket": "PT-1", "worktree": "/unused", "step": "first"}
        started = [first]
        recorded = {}
        finished = []

        def poll(card, step):
            low = scenarios.limits.low_limit(engine.limits_source(), "anthropic", 10, engine.clock()) or {}
            recorded.update(reset=low.get("resetTs", 0), window=low.get("window", ""))
            finished.append({"reason": "limit"})

        engine._poll = poll

        def start(card):
            # Wątek usługi może obsłużyć log zanim Qt dostarczy odpowiedź StartCard do testu.
            engine._poll(card, first)
            return {"ok": True}

        def events(card, kind):
            if kind == "step.started":
                recovered = scenarios.limits.recovered(engine.limits_source(), "anthropic", 10,
                                                       engine.clock(), recorded.get("window", ""))
                if len(started) == 1 and finished and (recovered or
                        recorded.get("reset") and engine.clock() >= recorded["reset"]):
                    started.append({**first, "step": "second", "parentStep": "first"})
                return started
            return finished if kind == "step.finished" else []

        with mock.patch.object(case, "card", return_value="K-1"), \
                mock.patch.object(case, "start", side_effect=start), \
                mock.patch.object(case, "of", side_effect=events), \
                mock.patch.object(case, "events", return_value=[]), \
                mock.patch.object(case, "wait_column"), \
                mock.patch.object(scenarios, "wait_until", side_effect=lambda predicate, **kw: predicate()):
            case.test_limit_without_any_choice_resumes_by_itself_after_the_reset()
        self.assertEqual((recorded["reset"], recorded["window"]), (1_800_001_000, "session"))
